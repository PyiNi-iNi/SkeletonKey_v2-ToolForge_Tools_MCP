"""ToolEngine - the one path every call takes, human, agent, or MCP host.

Pipeline (each stage can stop the call, and every stop is explained):

  resolve -> validate(args vs manifest) -> gate(profile) -> policy(deny rules ->
  rate limits -> approval/risk/allow rules) -> budget (task caps + mutation
  breaker) -> dispatch(timeout, redact) -> envelope -> ledger ->
  stats(feedback to registry ranking)

The first-party autopilot imports this class directly; MCP hosts get the same
engine behind a thin transport adapter. No second implementation of any check.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .envelope import Metrics, ToolResult
from .errors import E, SkeletonKeyError, ToolError, classify_exception
from .manifest import ToolManifest
from .policy import CompiledPolicy, PolicyRule
from .profile import CapabilityProfile
from .redact import redact_obj
from .registry import Registry
from .util import compact_json, glob_hit, glob_to_re, new_run_id, short_hash
from .validate import apply_defaults, validate

APPROVAL_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")


class ApprovalRequired(Exception):
    """Raised by the policy stage; the caller decides how to ask (MCP elicitation,
    TUI prompt, or autopilot auto-approve). Carries everything needed to ask."""

    def __init__(self, tool: str, reason: str, *, args: dict[str, Any], manifest: ToolManifest,
                 token: str = "", risk: str = "") -> None:
        super().__init__(f"{tool}: {reason}")
        self.tool = tool
        self.reason = reason
        self.args = args
        self.manifest = manifest
        self.token = token
        self.risk = risk or manifest.risk

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "kind": "approval_request",
            "tool": self.tool,
            "risk": self.risk,
            "reason": self.reason,
            "description": self.manifest.description,
            "destructive": self.manifest.destructive,
            "reversible": self.manifest.reversible,
            "args_preview": compact_json(redact_obj(self.args))[:1500],
            "approve_token": self.token,
            "grant_options": ["once", "task", "session", "deny"],
        }


@dataclass
class CallContext:
    """Per-call (and per-task, when shared) state an agent is entitled to assume."""

    task_id: str = ""
    session_id: str = ""
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    granted: set[str] = field(default_factory=set)      # "fs.write:*", tool ids approved
    hard_timeout: bool = True
    deadline: float | None = None                        # monotonic ceiling for the task
    # budget accounting (0 = unlimited)
    calls: int = 0
    mutations: int = 0
    tokens_out: int = 0
    max_calls: int = 0
    max_mutations: int = 0
    max_tokens_out: int = 0
    started: float = field(default_factory=time.monotonic)
    trace_id: str = field(default_factory=lambda: short_hash(new_run_id(), 10))
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: Config, *, task_id: str = "", session_id: str = "") -> CallContext:
        return cls(
            task_id=task_id or new_run_id(), session_id=session_id, cwd=cfg.workspace,
            max_calls=cfg.budget.task_max_calls, max_mutations=cfg.budget.task_max_mutations,
            max_tokens_out=cfg.budget.task_max_tokens_out,
            deadline=(time.monotonic() + cfg.budget.task_max_wall_s) if cfg.budget.task_max_wall_s else None,
        )

    def to_dict(self) -> dict[str, Any]:
        spent = {
            "calls": self.calls, "mutations": self.mutations, "tokens_out": self.tokens_out,
            "wall_s": round(time.monotonic() - self.started, 3),
        }
        limits = {"calls": self.max_calls, "mutations": self.max_mutations, "tokens_out": self.max_tokens_out}
        return {"task_id": self.task_id, "cwd": self.cwd, "trace_id": self.trace_id,
                "budget": {"spent": spent, "limits": {k: v for k, v in limits.items() if v}},
                "granted": sorted(self.granted)}


class Engine:
    def __init__(
        self,
        *,
        config: Config,
        registry: Registry | None = None,
        profile: CapabilityProfile | None = None,
        approver: Callable[[ApprovalRequired], bool] | None = None,
        ledger: Any | None = None,
        fs: Any = None,
        journal: Any = None,
    ) -> None:
        self.config = config
        self.profile = profile
        self.registry = registry or Registry(profile=profile)
        if profile is not None:
            self.registry.profile = profile
        self.approver = approver
        self.ledger = ledger
        self._fs = fs
        self._journal = journal
        self._shells: Any = None
        self._skills: Any = None
        self._search: Any = None
        self._last_profile_diff: dict[str, list[str]] = {}
        self._cache: dict[str, tuple[float, ToolResult]] = {}
        self._cache_ttl = 5.0
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sk-tool")
        self._started = time.monotonic()
        self._negotiated = False
        # rate limiting: one sliding window per tool + one mutation-burst window
        # per engine, both read under the same lock (the pool dispatches handlers
        # concurrently, and a limit checked twice is a limit that leaked once)
        self._rate_lock = threading.Lock()
        self._rate_windows: dict[str, deque[float]] = {}
        self._mutation_window: deque[float] = deque()
        self._policy_errors: list[str] = []
        self._policy = CompiledPolicy()
        self._compile_policy()

    # ------------------------------------------------------------------ lifecycle
    def attach(self, *, fs: Any = None, journal: Any = None, ledger: Any = None, profile: Any = None,
               shells: Any = None, skills: Any = None, search_backends: Any = None) -> None:
        """Late wiring: the toolkit builds sandbox/journal/shells after config+profile exist."""
        if fs is not None:
            self._fs = fs
        if journal is not None:
            self._journal = journal
        if ledger is not None:
            self.ledger = ledger
        if shells is not None:
            self._shells = shells
        if skills is not None:
            self._skills = skills
        if search_backends is not None:
            self._search = search_backends
        if profile is not None:
            self.profile = profile
            self.registry.profile = profile

    # --------------------------------------------------------------- capabilities
    def refresh_profile(self, *, force: bool = True) -> CapabilityProfile:
        """Re-probe the host, re-gate the registry, and report what changed.

        This is the hook behind `notifications/tools/list_changed`: installing
        ripgrep or pwsh should widen the advertised set without a restart.
        """
        from .profile import Prober

        old_ad = self.advertise()
        prof = Prober().probe(roots=self.config.roots,
                              cache_path=os.path.join(self.config.state.dir, "profile.json"),
                              ttl=self.config.state.profile_ttl_s, force=force)
        self.profile = prof
        self.registry.profile = prof
        if self._shells is not None:
            self._shells.profile = prof
        new_ad = self.advertise()
        self._last_profile_diff = new_ad.diff(old_ad)
        return prof

    def search_prefer(self, prefer: str | None) -> Any:
        """Search backend, optionally pinned to a provider for one call."""
        if self._search is None:
            from ..fsx.search import SearchBackend

            self._search = SearchBackend(self._fs.sb if self._fs else None, self.profile)
        if prefer is None:
            return self._search
        return type(self._search)(self._search.sb, self.profile, prefer=prefer)

    @property
    def shells(self) -> Any:
        return self._shells

    @property
    def skills(self) -> Any:
        return self._skills

    def close(self) -> None:
        try:
            self._pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        if self.ledger is not None:
            self.ledger.close()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------------------------------------------------------------- dispatch
    def call(
        self,
        tool_id: str,
        args: dict[str, Any] | None = None,
        *,
        ctx: CallContext | None = None,
        dry_run: bool | None = None,
        max_output_bytes: int | None = None,
        idempotency_key: str | None = None,
        approval_token: str | None = None,
    ) -> ToolResult:
        ctx = ctx or CallContext.from_config(self.config)
        args = dict(args or {})
        t0 = time.monotonic()
        man: ToolManifest | None = None
        cache_key: str | None = None
        res: ToolResult | None = None

        try:
            man = self.registry.get(tool_id)                       # 1. resolve
            # `dry_run` inside args and the call-level flag are the same request. Honouring
            # only the flag would let a documented preview (every fs.* tool has a `dry_run`
            # property) write to disk, which is the one failure mode a preview cannot have.
            asked_preview = bool(args.get("dry_run"))
            self._guard_gate(man, preview=bool(asked_preview or dry_run))   # 2. gate
            args = self._validate(man, args)                        # 3. validate
            dry = self.config.policy.read_only if dry_run is None else dry_run
            dry = bool(dry) or asked_preview
            self._authorize(man, args, ctx, approval_token=approval_token, dry_run=bool(dry))  # 4. policy
            self._charge_budget(ctx, man, dry_run=bool(dry))                    # 5. budget

            cache_key = self._cache_key(man, args, ctx)
            if cache_key and cache_key in self._cache:
                cached_at, cached = self._cache[cache_key]
                if time.time() - cached_at < self._cache_ttl:
                    res = ToolResult.success(cached.data, tool=man.id, artifacts=list(cached.artifacts),
                                             context=dict(cached.context), warnings=["served from idempotency cache"])
                    res.metrics = Metrics(duration_ms=int((time.monotonic() - t0) * 1000), cached=True,
                                          provider=man.provider, exit_code=cached.metrics.exit_code)
                    return res

            res = self._dispatch(man, args, ctx, dry_run=bool(dry))  # 6. run
            # 7. normalize
            res.tool = man.id
            res.metrics.duration_ms = int((time.monotonic() - t0) * 1000)
            res.metrics.provider = res.metrics.provider or man.provider
            if man.reversible and not res.metrics.extra.get("undo"):
                res.metrics.extra["undo_available"] = True
            # Who approved what belongs on the receipt, not only in the
            # transcript: a ledger reader must see the grant without
            # re-deriving it from the approval flow.
            for key in ("approval_grant", "policy_allow"):
                if key in ctx.extras:
                    res.metrics.extra[key] = ctx.extras.pop(key)
            if cache_key and res.ok and man.idempotent and not man.is_mutating:
                self._cache[cache_key] = (time.time(), res)
            return res

        except SkeletonKeyError as exc:
            res = ToolResult.failure(exc.err, tool=tool_id,
                                     next_actions=exc.next_actions or None,
                                     data=self._partial(exc))
            res.metrics.duration_ms = int((time.monotonic() - t0) * 1000)
            if man is not None:
                res.context.setdefault("schema", man.input_schema_for_host())
            return res

        except Exception as exc:
            err = classify_exception(exc)
            err.details.setdefault("trace_id", ctx.trace_id)
            res = ToolResult.failure(err, tool=tool_id)
            res.metrics.duration_ms = int((time.monotonic() - t0) * 1000)
            res.add_warning(f"unhandled {type(exc).__name__}; see error.details.trace_id")
            return res

        finally:
            ctx.calls += 1
            # One reliability record per call, from the one place that sees every path
            # (success, failure, cache hit). Counting only failures made
            # `registry.stats` report 0 calls for everything that worked, and provider
            # ranking - which reads this - was ranking on a hole.
            if man is not None and res is not None:
                self._record(man, res.ok, t0, error_code=res.error.code if res.error else None)
            try:
                if res is not None:
                    res.estimate()
                    ctx.tokens_out += res.metrics.est_tokens
                    if res.ok and man is not None and man.is_mutating:
                        ctx.mutations += 1
                        self._record_mutation()
            except Exception:
                pass
            try:
                # Auditing lives here, not in each return path: a run whose
                # successes were never recorded is not an audit trail.
                if res is not None:
                    self._ledger(man, args, res, t0, ctx, tool_name=tool_id)
            except Exception:
                pass

    # ------------------------------------------------------------------- stages
    @staticmethod
    def _previewable(man: ToolManifest) -> bool:
        """True when the tool implements its own no-side-effect preview."""
        return "dry_run" in ((man.input_schema or {}).get("properties") or {})

    def _guard_gate(self, man: ToolManifest, *, preview: bool = False) -> None:
        # Hard walls first, and they never depend on having a profile: a host that
        # skipped probing (or `gate_by_profile = false`) must still honour disable.
        if man.id in set(self.config.tools.disable or ()):
            raise SkeletonKeyError(
                E.TOOL_NOT_ADVERTISED, f"tool {man.id} is disabled by configuration",
                details={"disabled_by": "tools.disable",
                         "advice": "edit [tools] disable, or run with SKELETONKEY_TOOLS__DISABLE unset"},
                next_actions=[{"tool": "registry.describe", "args": {"tool": man.id}}],
            )
        if self.config.policy.read_only and man.is_mutating and not (preview and self._previewable(man)):
            raise SkeletonKeyError(
                E.READ_ONLY_MODE, f"{man.id} is a mutating tool and policy.read_only is enabled",
                details={"policy": "read_only", "tool": man.id,
                         "advice": ("pass dry_run=true to see what would change"
                                    if self._previewable(man) else
                                    "this tool cannot preview; unset policy.read_only to run it")},
            )
        if not self.config.tools.gate_by_profile or self.profile is None:
            return
        gate = self.registry.gate(man, read_only=self.config.policy.read_only,
                                   disabled=self.config.tools.disable)
        if gate.available:
            return
        unmet = gate.unmet or gate.reasons
        if preview and self._previewable(man) and unmet and all("read_only" in u for u in unmet):
            # read_only withholds mutating tools from the host; a preview is the
            # supported way to see what such a tool would do, so it stays reachable.
            return
        code = E.MISSING_BINARY if any("binary" in u for u in unmet) else (
            E.MISSING_SHELL if any("shell" in u for u in unmet) else E.TOOL_NOT_ADVERTISED)
        available_dialects = self.profile.available_dialects() if self.profile else []
        raise SkeletonKeyError(
            code, f"tool {man.id} is not available on this host",
            details={"gate": {"available": False, "unmet": unmet},
                     **({"available_dialects": available_dialects} if available_dialects else {}),
                     "receipt": self._receipt_for(unmet)},
            next_actions=[{"tool": "registry.search",
                           "args": {"query": man.capability or man.id, "include_gated": True}}],
        )

    def _receipt_for(self, unmet: list[str]) -> list[dict[str, Any]]:
        """Explain *how* we concluded the thing is missing (probed command + rc)."""
        if not self.profile:
            return []
        out = []
        for note in unmet:
            name = re.sub(r"^.*?([a-zA-Z0-9_.-]+)\s.*$", r"\1", note)
            hits = [r.to_dict() for r in self.profile.probe_receipt if name and name.lower() in r.command.lower()]
            if hits:
                out.append({"unmet": note, "probe": hits[0]})
        return out

    def _validate(self, man: ToolManifest, args: dict[str, Any]) -> dict[str, Any]:
        schema = man.input_schema
        if not schema.get("properties") and not schema.get("required"):
            return args
        args = apply_defaults(args, schema)
        errors = validate(args, schema)
        if errors:
            for err in errors:
                if err["keyword"] == "required":
                    where = err.get("path") or err["missing"]   # 'edits.0.old_text' when nested
                    raise SkeletonKeyError(
                        E.MISSING_ARG,
                        f"{man.id}: missing required argument {err['missing']!r}"
                        + (f" (inside {where})" if where != err["missing"] else ""),
                        details={"missing": err["missing"], "at": where,
                                 "schema": man.input_schema_for_host()},
                        next_actions=[{"tool": "registry.describe", "args": {"tool": man.id}}],
                    )
            raise SkeletonKeyError(
                E.BAD_ARGS, f"{man.id}: {len(errors)} argument error(s)",
                details={"errors": [{k: v for k, v in e.items() if k != "keyword"} for e in errors[:8]],
                         "schema": man.input_schema_for_host(),
                         "minimal_example": (man.examples[0].get("args") if man.examples
                                             else self._minimal_example(schema))},
            )
        return args

    @staticmethod
    def _minimal_example(schema: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, sub in (schema.get("properties") or {}).items():
            if not isinstance(sub, dict):
                continue
            if "default" in sub:
                out[key] = sub["default"]
            elif "enum" in sub:
                out[key] = sub["enum"][0]
            else:
                t = sub.get("type")
                t = t[0] if isinstance(t, list) else t
                out[key] = {"string": "", "integer": 0, "number": 0, "boolean": False,
                            "array": [], "object": {}}.get(t or "string", "")
        return out

    def _compile_policy(self) -> None:
        """Compile every policy spelling (legacy `deny` strings, `escalate`
        list, `rate_limits` map, structured `rule` tables) into one rule list.
        Malformed entries are recorded in `self._policy_errors` and skipped -
        never raised (a config typo must not take the toolkit down) and never
        guessed at (a broken deny rule that widens access is a security hole).
        """
        self._policy, self._policy_errors = CompiledPolicy.from_config(self.config)

    def _authorize(self, man: ToolManifest, args: dict[str, Any], ctx: CallContext, *,
                   approval_token: str | None = None, dry_run: bool = False) -> None:
        pol = self.config.policy

        # Deny is the first thing read in the whole stage, before any token,
        # grant or flag could be consulted: it is the rule an operator relies
        # on when they are not watching, and it must stay non-overridable.
        deny = self._policy.deny_hit(man.id, args)
        if deny:
            rule, evidence = deny
            raise SkeletonKeyError(
                E.DENY_RULE, f"{man.id} blocked by policy rule {rule.source}",
                details={"rule": rule.to_dict(), "matched": evidence,
                         "reason": rule.reason or "the rule carries no reason; ask the operator "
                                                 "to record one so the refusal is actionable",
                         "advice": "deny rules cannot be overridden per-call by design; an "
                                   "operator must remove or narrow the rule in config"},
            )

        risk = man.risk
        if (man.id in pol.escalate or (man.group in pol.escalate)
                or self._policy.escalate_hit(man.id, args) is not None):
            risk = "privileged"

        # Rate limits gate the real call, not its preview: a dry_run answers
        # with a plan and writes nothing, so it must not burn a rate slot.
        if not dry_run:
            rate = self._policy.strictest_rate(man.id, args)
            if rate:
                self._charge_rate(man, rate[0], rate[1])

        if dry_run and man.is_mutating:
            if self._previewable(man):
                # The handler implements the preview, so let it answer with the real plan
                # (diff, affected paths) instead of a guess from the schema. `dry_run` in a
                # schema is the tool's promise that the preview writes nothing.
                return
            raise SkeletonKeyError(
                E.READ_ONLY_MODE, f"{man.id} would mutate state; call ran with dry_run/read_only",
                details={"plan": self._plan_for(man, args), "would_write": True},
            )

        needs = self._needs_approval(man, risk)
        if needs:
            allow = self._policy.allow_hit(man.id, args)
            if allow and not (pol.read_only and man.is_mutating):
                # An allow rule is an operator decision recorded in config, not
                # a per-call argument: it removes the approval requirement for
                # *this shape of call*. It never clears a deny (checked above),
                # never overrides read_only (the wall stays), and the call
                # still walks every other gate.
                rule, evidence = allow
                ctx.extras["policy_allow"] = {"rule": rule.source,
                                              "reason": rule.reason or "",
                                              "matched": evidence}
                return
        if not needs:
            return
        if approval_token:
            if not APPROVAL_TOKEN_RE.match(approval_token):
                raise SkeletonKeyError(E.BAD_ARGS, "approval_token is malformed",
                                       details={"expected": "8-128 chars of [A-Za-z0-9_.:-]"})
            if approval_token in ctx.granted or approval_token == f"grant:{man.id}":
                ctx.extras["approval_grant"] = approval_token
                return
            raise SkeletonKeyError(E.APPROVAL_REQUIRED, "approval_token does not cover this tool",
                                   details={"tool": man.id, "granted": sorted(ctx.granted)})
        if risk in pol.auto_approve and (not man.destructive or not pol.confirm_destructive):
            # An operator who put "destructive" in auto_approve *and* turned
            # confirm_destructive off has said "nobody is watching, go ahead"; ignoring
            # that pairing would leave an autopilot stuck on a tool it can never approve.
            return
        if pol.read_only and man.is_mutating:
            raise SkeletonKeyError(E.READ_ONLY_MODE, f"{man.id} is a mutating tool and read_only is enabled")
        if self.approver is not None:
            req = ApprovalRequired(man.id, f"{risk}-risk action", args=args, manifest=man,
                                   token=f"grant:{man.id}")
            try:
                granted = bool(self.approver(req))
            except Exception as exc:
                raise SkeletonKeyError(
                    E.INTERNAL, f"approver raised {type(exc).__name__}: {exc}",
                    details={"traceback": str(exc)[:200],
                             "note": "an approver that throws is treated as a denial path, never as consent"},
                ) from exc
            if granted:
                ctx.granted.add(f"grant:{man.id}")
                ctx.extras["approval_grant"] = f"grant:{man.id}"
                return
            raise SkeletonKeyError(E.APPROVAL_REQUIRED, "approval was declined",
                                   details={"tool": man.id, "risk": risk,
                                            "next": "ask the user to widen policy.auto_approve or approve interactively"})
        raise SkeletonKeyError(
            E.APPROVAL_REQUIRED,
            f"{man.id} requires approval for risk={risk} and no approver is configured",
            details={"prompt": ApprovalRequired(man.id, "no approver", args=args, manifest=man,
                                                token=f"grant:{man.id}").prompt_payload(),
                     "grant_options": ["once", "task", "session"],
                     "advice": ("configure an approver, or set policy.auto_approve to include "
                                 '"' + risk + '" with policy.confirm_destructive=false for unattended runs')},
            next_actions=[{"tool": "policy.grant", "args": {"scope": "task", "tool": man.id}}]
            if hasattr(self, "registry") and self.registry.has("policy.grant") else [],
        )

    def _needs_approval(self, man: ToolManifest, risk: str) -> bool:
        pol = self.config.policy
        mode = man.approval
        if mode == "never":
            return False
        if mode == "always":
            return True
        if mode == "on_write":
            return man.is_mutating
        # policy mode: driven by config
        if risk in pol.require_approval:
            return True
        return man.destructive and pol.confirm_destructive

    def _charge_rate(self, man: ToolManifest, rule: PolicyRule,
                     evidence: dict[str, Any]) -> None:
        """Sliding-window rate limit for one tool. The call that crosses the
        limit is refused *before dispatch* (the tool does not run) and told
        when the window will open again - a refusal with a recovery time, not
        a wall."""
        now = time.monotonic()
        with self._rate_lock:
            dq = self._rate_windows.setdefault(man.id, deque())
            while dq and now - dq[0] > rule.window_s:
                dq.popleft()
            if len(dq) >= rule.rate:
                retry = max(0.0, rule.window_s - (now - dq[0]))
                raise SkeletonKeyError(
                    E.BUDGET_EXCEEDED, f"{man.id} hit its rate limit",
                    details={"exceeded": [f"rate_limit {man.id}: {len(dq)}/{rule.rate} calls "
                                          f"within {rule.window_s:g}s (rule {rule.source})"],
                             "rule": rule.to_dict(), "matched": evidence,
                             "retry_after_s": round(retry, 1),
                             "advice": "the limit resets as older calls age out of the window; "
                                       "batch the work into fewer calls, or wait retry_after_s"},
                    next_actions=[{"action": "summarize_and_stop",
                                   "why": "a rate limit is a hard stop for this tool, not a transient error"}],
                )
            dq.append(now)

    def _record_mutation(self) -> None:
        """Feed the mutation circuit breaker from the one place that sees every
        successful mutating call (the `finally` of `call`)."""
        with self._rate_lock:
            self._mutation_window.append(time.monotonic())

    def _plan_for(self, man: ToolManifest, args: dict[str, Any]) -> dict[str, Any]:
        """What *would* have happened - lets an agent present intent without side effects."""
        return {"tool": man.id, "risk": man.risk, "args": redact_obj(_shrink(args)),
                "reversible": man.reversible}

    # ------------------------------------------------------------------ budget
    def _charge_budget(self, ctx: CallContext, man: ToolManifest, *, dry_run: bool = False) -> None:
        now = time.monotonic()
        exceeded: list[str] = []
        if ctx.max_calls and ctx.calls >= ctx.max_calls:
            exceeded.append(f"calls {ctx.calls}/{ctx.max_calls}")
        if ctx.max_mutations and ctx.mutations >= ctx.max_mutations and man.is_mutating:
            exceeded.append(f"mutations {ctx.mutations}/{ctx.max_mutations}")
        # Mutation circuit breaker: the per-task caps above assume one task
        # behaves; this one does not, which is the runaway-loop case. It counts
        # *successful* mutations on this engine over a rolling 60s, whatever
        # task they belong to, and it previews nothing (dry_run writes nothing,
        # so it does not trip the breaker).
        burst = self.config.policy.max_mutations_per_minute
        if man.is_mutating and not dry_run and burst > 0:
            with self._rate_lock:
                dq = self._mutation_window
                while dq and now - dq[0] > 60.0:
                    dq.popleft()
                if len(dq) >= burst:
                    exceeded.append(f"mutation burst {len(dq)}/{burst} per 60s "
                                    "(policy.max_mutations_per_minute)")
        if ctx.max_tokens_out and ctx.tokens_out >= ctx.max_tokens_out:
            exceeded.append(f"tokens_out {ctx.tokens_out}/{ctx.max_tokens_out}")
        if ctx.deadline and now > ctx.deadline:
            exceeded.append(f"wall time exceeded {round(ctx.deadline - ctx.started, 1)}s budget")
        if exceeded:
            raise SkeletonKeyError(
                E.BUDGET_EXCEEDED, "task budget exhausted: " + "; ".join(exceeded),
                details={"exceeded": exceeded, "spent": ctx.to_dict()["budget"]["spent"],
                         "limits": ctx.to_dict()["budget"]["limits"]},
                next_actions=[{"action": "summarize_and_stop", "why": "budget is a hard stop, not a transient error"}],
            )

    # ------------------------------------------------------------------ dispatch
    def _dispatch(self, man: ToolManifest, args: dict[str, Any], ctx: CallContext, *,
                  dry_run: bool) -> ToolResult:
        if man.handler is None:
            raise SkeletonKeyError(E.INTERNAL, f"{man.id} has no handler")
        call_kwargs: dict[str, Any] = dict(args)
        sig_params = _handler_params(man.handler)
        if "ctx" in sig_params:
            call_kwargs["ctx"] = ctx
        if "engine" in sig_params:
            call_kwargs["engine"] = self
        if "fs" in sig_params and self._fs is not None:
            call_kwargs["fs"] = self._fs
        if "journal" in sig_params and self._journal is not None:
            call_kwargs["journal"] = self._journal
        if "shells" in sig_params and self._shells is not None:
            call_kwargs["shells"] = self._shells
        if "dry_run" in sig_params:
            call_kwargs["dry_run"] = dry_run

        timeout = man.effective_timeout(self._requested_timeout(args))
        timeout = min(timeout, self.config.policy.max_timeout_s)
        hard = ctx.hard_timeout and man.risk_at_least("read")
        if hard:
            fut = self._pool.submit(man.handler, **call_kwargs)
            try:
                raw = fut.result(timeout=timeout + 2.0)
            except FutTimeout:
                fut.cancel()
                raise SkeletonKeyError(
                    E.TIMEOUT, f"{man.id} exceeded {timeout:.1f}s",
                    details={"timeout_s": timeout, "killed": True,
                             "advice": "background=true on shell.run avoids blocking the loop"},
                    next_actions=[{"tool": "shell.run", "args": {**_shrink(args), "background": True}}]
                    if man.id.startswith("shell.") else [],
                ) from None
        else:
            raw = man.handler(**call_kwargs)
        return self._coerce_result(man, raw)

    @staticmethod
    def _requested_timeout(args: dict[str, Any]) -> float | None:
        for key in ("timeout_s", "timeout", "deadline_s"):
            if key in args and isinstance(args[key], (int, float)):
                return float(args[key])
        return None

    @staticmethod
    def _coerce_result(man: ToolManifest, raw: Any) -> ToolResult:
        if isinstance(raw, ToolResult):
            return raw
        if isinstance(raw, dict) and "ok" in raw and isinstance(raw["ok"], bool):
            # a hand-written dict envelope from a drop-in plugin
            data = raw.get("data", raw if "data" not in raw else None)
            if raw["ok"]:
                return ToolResult.success(data=data, artifacts=[], hints=list(raw.get("hints", [])),
                                           context=dict(raw.get("context", {})))
            err = raw.get("error") or {}
            return ToolResult.failure(ToolError(
                code=str(err.get("code", E.INTERNAL.code)),
                error_class=str(err.get("class", E.INTERNAL.cls.value)),
                message=str(err.get("message", "")),
                retryable=bool(err.get("retryable", False)),
                hint=str(err.get("hint", "")),
                details=dict(err.get("details", {})),
            ), data=raw.get("data"))
        return ToolResult.success(data=raw)

    # ------------------------------------------------------------------- results
    def _record(self, man: ToolManifest, ok: bool, t0: float, error_code: str | None = None) -> None:
        self.registry.record(man.id, ok=ok, duration_ms=int((time.monotonic() - t0) * 1000),
                             error_code=error_code)

    def _ledger(self, man: ToolManifest | None, args: dict[str, Any], res: ToolResult, t0: float,
                ctx: CallContext, *, tool_name: str = "") -> None:
        if self.ledger is None:
            return
        try:
            self.ledger.append(
                tool=(man.id if man else tool_name), args=args, ok=res.ok,
                duration_ms=int((time.monotonic() - t0) * 1000), run_id=res.run_id,
                error_code=res.error.code if res.error else None,
                risk=man.risk if man else "unknown", task_id=ctx.task_id, session_id=ctx.session_id,
                result=res.to_dict(max_bytes=1200),
            )
        except Exception:
            pass

    def _cache_key(self, man: ToolManifest, args: dict[str, Any], ctx: CallContext) -> str | None:
        """Key for the idempotency cache, or None when the tool must never be cached.

        `idempotent` means "same args, no side effects", not "stable answer": a session
        or job listing is a pure read that still changes the moment another call runs, so
        caching `shell.sessions` would hide the very state the agent is polling for.
        """
        if not man.idempotent or man.is_mutating or man.stateful not in ("", "none"):
            return None
        fp = self.profile.fingerprint if self.profile else "-"
        return f"{man.id}|{man.version}|{fp}|{ctx.cwd or ''}|{short_hash(compact_json(_shrink(args)), 16)}"

    @staticmethod
    def _partial(exc: SkeletonKeyError) -> Any:
        det = exc.details or {}
        if "data" in det:
            data = det["data"]
            det.pop("data")
            return data
        return None

    # ------------------------------------------------------------------- helpers
    def grant(self, ctx: CallContext, *, scope: str, tool: str) -> dict[str, Any]:
        if scope == "once":
            return {"granted": False, "note": "call again with approval_token"}
        token = f"grant:{tool}" if scope in ("task", "session") else None
        if token is None:
            return {"granted": False, "error": f"unknown scope {scope!r}"}
        ctx.granted.add(token)
        return {"granted": True, "scope": scope, "tool": tool, "approval_token": token}

    def advertise(self, **kw: Any) -> Any:
        kw.setdefault("read_only", self.config.policy.read_only)
        kw.setdefault("disabled", self.config.tools.disable)
        kw.setdefault("token_budget", self.config.budget.max_result_tokens * 40)
        return self.registry.advertise(**kw)

    @property
    def fs(self) -> Any:
        return self._fs

    @property
    def journal(self) -> Any:
        return self._journal


# ------------------------------------------------------------------------ utils


def _handler_params(fn: Callable[..., Any]) -> set[str]:
    import inspect

    try:
        return set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return set()


def _shrink(args: dict[str, Any], *, limit: int = 700) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > limit:
            out[k] = v[:limit] + f"...[{len(v) - limit} more chars]"
        elif isinstance(v, (dict, list)):
            text = compact_json(v)
            out[k] = text[:limit] + "..." if len(text) > limit else v
        else:
            out[k] = v
    return out


# Glob matching lives in core/util.py (shared with core/policy.py). The old
# module-level names are kept as aliases because earlier code used them here.
_glob_hit = glob_hit
_glob_to_re = glob_to_re


def _noop_context() -> Any:
    return nullcontext()
