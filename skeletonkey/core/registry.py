"""ToolRegistry - the dynamic part of the dynamic toolset.

Responsibilities:
  1. hold manifests + handlers (builtin, drop-in dirs, entry points, skills, remotes)
  2. gate them against the CapabilityProfile (don't advertise `pwsh` on a box
     without pwsh; don't advertise gnu-patch when only python fallback exists)
  3. collapse *capabilities* to one winning provider, ranked by declared
     priority then by observed success (the adaptive bit)
  4. answer `search()` for the "I need a tool that does X" loop
  5. emit an advertisement plan that respects a token budget

`advertise()` returns an `AdSnapshot` with `added/removed` so callers (MCP) can
raise tools/list_changed only when the set truly changed.
"""

from __future__ import annotations

import importlib.util
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .errors import E, SkeletonKeyError
from .manifest import RISK_ORDER, Requirement, ToolManifest
from .profile import CapabilityProfile
from .semantic import discover_backends
from .util import compact_json, new_run_id, short_hash

# Discovery tiers (P5a): a manifest's `tier` tells us *when* it may be advertised.
# `core` tools are the bootstrap surface (always available), `task` the plan-selected
# middle, `full` everything. Ordering is deliberately numeric so the filter is a
# comparison, not a lookup table.
TIERS = ("core", "task", "full")
TIER_ORDER = {"core": 0, "task": 1, "full": 2}


@dataclass
class ProviderStats:
    calls: int = 0
    ok: int = 0
    fail: int = 0
    total_ms: int = 0
    last_ms: int = 0
    last_error: str | None = None
    last_seen: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.ok / self.calls) if self.calls else 0.5

    @property
    def mean_ms(self) -> float:
        return (self.total_ms / self.calls) if self.calls else 0.0

    def score(self) -> float:
        """Higher is better. Weighted so reliability dominates latency."""
        import math

        if self.calls < 3:
            return 0.0  # not enough evidence: trust declared priority
        reliability = self.success_rate
        speed = math.exp(-min(self.mean_ms, 30_000) / 12_000)
        evidence = min(1.0, self.calls / 25)
        return 0.75 * reliability + 0.15 * speed + 0.10 * evidence

    def to_dict(self) -> dict[str, Any]:
        return {"calls": self.calls, "ok": self.ok, "fail": self.fail,
                "success_rate": round(self.success_rate, 3), "mean_ms": round(self.mean_ms, 1),
                **({"last_error": self.last_error} if self.last_error else {})}


@dataclass
class AdGate:
    """Why a tool is/isn't advertised right now."""

    available: bool
    reasons: list[str] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"available": self.available}
        if self.unmet:
            d["unmet"] = self.unmet
        if self.reasons:
            d["reasons"] = self.reasons
        return d


@dataclass
class AdSnapshot:
    tools: list[ToolManifest]
    gates: dict[str, AdGate]
    tokens: int
    digest: str
    selected: dict[str, str] = field(default_factory=dict)   # capability -> winning tool id
    # P5a: the selection receipt per capability that was de-duplicated (or is sole).
    # This is the *advertisement-time* mirror of "which provider answered and why":
    # a host never has to guess why it saw one search tool instead of four.
    selection_receipts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # tool id -> why it was trimmed by token budget / max_tools. Empty when nothing
    # was dropped, and the digest covers it either way.
    budget_drops: dict[str, str] = field(default_factory=dict)
    tier: str = "full"
    at: float = field(default_factory=time.time)

    @property
    def names(self) -> list[str]:
        return [t.id for t in self.tools]

    def diff(self, other: AdSnapshot) -> dict[str, list[str]]:
        a, b = set(self.names), set(other.names)
        return {"added": sorted(b - a), "removed": sorted(a - b)}

    def receipt_for(self, man: ToolManifest) -> dict[str, Any]:
        """The selection receipt for one advertised tool.

        A tool that shares its capability with no other provider gets an honest
        "sole provider" receipt; a tool that won a provider race gets the race
        receipt (winner + why + competitors).
        """
        cap = man.capability or man.id
        r = self.selection_receipts.get(cap)
        if r is not None:
            return r
        return {"tool": man.id, "provider": man.provider,
                "why": "only provider for this capability (no provider race)",
                "sole": True}


@dataclass
class Registry:
    profile: CapabilityProfile | None = None
    _tools: dict[str, ToolManifest] = field(default_factory=dict, repr=False)
    _stats: dict[str, ProviderStats] = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _generation: int = field(default=0, repr=False)
    _listeners: list[Callable[[str, ToolManifest | None], None]] = field(default_factory=list, repr=False)
    _sources: dict[str, str] = field(default_factory=dict, repr=False)
    loaded_dirs: list[str] = field(default_factory=list, repr=False)
    load_errors: list[dict[str, Any]] = field(default_factory=list, repr=False)
    # P5a: which tier `advertise()` defaults to. State, not a parameter, so a
    # `registry.expand` call - and the MCP bridge reading the registry - agree on
    # one number without threading a value through every call site.
    active_tier: str = "full"

    def __post_init__(self) -> None:
        self.load_errors = []

    def set_tier(self, tier: str) -> str:
        """Switch the advertised tier. Returns the previous tier.

        Changing the tier changes the tool set, which changes the digest, which is
        what makes `tools/list_changed` fire - no separate notification channel is
        needed: the diff is the signal.
        """
        if tier not in TIER_ORDER:
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown advertisement tier {tier!r}",
                                   details={"tier": tier, "tiers": list(TIERS),
                                            "hint": "core = bootstrap surface, task = plan middle, full = everything"})
        with self._lock:
            old, self.active_tier = self.active_tier, tier
            return old

    # ------------------------------------------------------------- registration
    def register(self, manifest: ToolManifest, handler: Callable[..., Any] | None = None, *,
                 replace: bool = False) -> ToolManifest:
        with self._lock:
            existing = self._tools.get(manifest.id)
            if existing and not replace:
                raise SkeletonKeyError(
                    E.CONFLICT, f"tool {manifest.id} already registered (source={existing.source})",
                    details={"existing_source": existing.source, "new_source": manifest.source,
                             "hint": "pass replace=True or use a different id"},
                )
            if handler is not None:
                manifest.handler = handler
            if manifest.handler is None:
                raise SkeletonKeyError(E.BAD_ARGS, f"tool {manifest.id} has no handler",
                                       details={"id": manifest.id})
            self._tools[manifest.id] = manifest
            self._sources[manifest.id] = manifest.source_path or manifest.source
            self._generation += 1
            self._notify("added", manifest)
            return manifest

    def unregister(self, tool_id: str) -> ToolManifest | None:
        with self._lock:
            man = self._tools.pop(tool_id, None)
            if man:
                self._generation += 1
                self._notify("removed", man)
            return man

    def get(self, tool_id: str) -> ToolManifest:
        try:
            return self._tools[tool_id]
        except KeyError:
            near = self.suggest(tool_id)
            raise SkeletonKeyError(
                E.UNKNOWN_TOOL, f"unknown tool {tool_id!r}",
                details={"requested": tool_id, "suggested": near,
                         "count": len(self._tools)},
                next_actions=[{"tool": "registry.search", "args": {"query": tool_id, "limit": 5}}]
                if not near else [{"tool": near[0]["id"], "note": "close match"}],
            ) from None

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def all(self) -> list[ToolManifest]:
        return [self._tools[k] for k in sorted(self._tools)]

    def by_group(self, group: str) -> list[ToolManifest]:
        return [t for t in self.all() if t.group == group]

    def by_capability(self, capability: str, *, prefix: bool = True) -> list[ToolManifest]:
        out = []
        for t in self.all():
            cands = [t.capability, *t.provides]
            for c in cands:
                if c == capability or (prefix and c.startswith(capability.rstrip(".") + ".")):
                    out.append(t)
                    break
        return out

    def suggest(self, name: str, *, limit: int = 3) -> list[dict[str, Any]]:
        """Typo/did-you-mean helper for agents that invent tool names."""
        scored = []
        target = name.lower().replace("/", ".")
        for tid in self._tools:
            score = _similar(target, tid.lower())
            if score > 0.34:
                scored.append((score, {"id": tid, "why": "name similarity", "score": round(score, 2)}))
        scored.sort(key=lambda x: -x[0])
        return [s[1] for s in scored[:limit]]

    # ----------------------------------------------------------------- listeners
    def on_change(self, cb: Callable[[str, ToolManifest | None], None]) -> None:
        self._listeners.append(cb)

    def _notify(self, event: str, man: ToolManifest | None) -> None:
        for cb in list(self._listeners):
            try:
                cb(event, man)
            except Exception:
                pass

    @property
    def generation(self) -> int:
        return self._generation

    # -------------------------------------------------------------------- stats
    def record(self, tool_id: str, *, ok: bool, duration_ms: int, error_code: str | None = None) -> None:
        with self._lock:
            s = self._stats.setdefault(tool_id, ProviderStats())
            s.calls += 1
            s.total_ms += int(duration_ms)
            s.last_ms = int(duration_ms)
            s.last_seen = time.time()
            if ok:
                s.ok += 1
                s.last_error = None
            else:
                s.fail += 1
                s.last_error = error_code

    def stats(self, tool_id: str | None = None) -> dict[str, Any]:
        if tool_id:
            s = self._stats.get(tool_id)
            # keyed by id either way: a filtered response must not change shape, or the
            # caller loses the tool name it asked about
            return {tool_id: s.to_dict() if s else {"calls": 0}}
        return {k: v.to_dict() for k, v in sorted(self._stats.items())}

    # ------------------------------------------------------------------- gating
    def gate(self, man: ToolManifest, *, read_only: bool = False,
             disabled: Iterable[str] = ()) -> AdGate:
        disabled_set = set(disabled)
        reasons: list[str] = []
        if man.id in disabled_set:
            return AdGate(False, ["disabled by configuration"])
        if not man.advertised:
            reasons.append(man.hidden_reason or "internal tool (not advertised)")
        if read_only and man.is_mutating:
            return AdGate(False, ["policy.read_only is on; mutating tool withheld"], reasons)
        if man.platforms and self.profile and self.profile.os not in man.platforms:
            return AdGate(False, [f"supports {man.platforms}, host is {self.profile.os}"], reasons)
        if self.profile and man.requirements:
            ok, unmet = self.profile.meets(man.requirements)
            if not ok:
                return AdGate(False, reasons, unmet)
        if self.profile and man.require_any:
            ok, notes = self.profile.meets_any(man.require_any)
            if not ok:
                return AdGate(False, reasons, ["none of: " + ", ".join(notes)])
        return AdGate(True, reasons)

    def rank_providers(self, capability: str) -> list[tuple[float, ToolManifest]]:
        cands = self.by_capability(capability)
        usable = [(m, g) for m in cands for g in [self.gate(m)] if g.available]
        scored: list[tuple[float, ToolManifest]] = []
        for man, _g in usable:
            stat = self._stats.get(man.id)
            scored.append((float(RISK_ORDER.get(man.risk, 0)) * -2.0 + man.priority
                           + (stat.score() * 40 if stat else 0.0), man))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return scored

    # -------------------------------------------------------------- advertisement
    def advertise(self, *, profile: CapabilityProfile | None = None, read_only: bool = False,
                  disabled: Iterable[str] = (), dedupe_capability: bool = True,
                  token_budget: int | None = None, include_internal: bool = False,
                  tier: str | None = None, max_tools: int | None = None,
                  tier_budgets: dict[str, dict[str, int]] | None = None) -> AdSnapshot:
        """The tool list a host actually sees right now.

        `tier` defaults to `registry.active_tier` (full unless `set_tier` ran):
        core tools advertise in every tier, task in task + full, full only in full.
        A tool withheld by tier shows up in `gates` with a reasoning row, so
        "why didn't I see it" is answerable for tier drops too.

        `tier_budgets` carries the `[advertise]` caps for the active tier (tools +
        tokens; 0 = no cap) and is applied only when the caller passed neither an
        explicit `token_budget` nor `max_tools` - an explicit budget always wins.

        dedupe_capability keeps one provider per capability (highest rank), which
        is how "adaptive" shows up to the model: it never sees 4 file-search tools.
        """
        tier = tier or self.active_tier
        if tier not in TIER_ORDER:
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown advertisement tier {tier!r}",
                                   details={"tier": tier, "tiers": list(TIERS)})
        if tier_budgets and tier in tier_budgets:
            if token_budget is None:
                token_budget = tier_budgets[tier].get("tokens") or None
            if max_tools is None:
                max_tools = tier_budgets[tier].get("tools") or None
        saved_profile = self.profile
        if profile is not None:
            self.profile = profile
        try:
            tools = self.all()
            gates: dict[str, AdGate] = {}
            survivors: list[ToolManifest] = []
            for man in tools:
                g = self.gate(man, read_only=read_only, disabled=disabled)
                # Tier withholding is a pre-gate fact about *when* the tool may show,
                # not about the host: record it separately so the withheld receipt
                # names the real reason instead of falling through to "budget drop".
                if TIER_ORDER.get(man.tier, 2) > TIER_ORDER[tier]:
                    if g.available:
                        g = AdGate(False, [f"tier {tier}: this tool is {man.tier}-tier"])
                    gates[man.id] = g
                    continue
                gates[man.id] = g
                if not g.available:
                    continue
                if not include_internal and not man.advertised:
                    continue
                survivors.append(man)

            selected: dict[str, str] = {}
            receipts: dict[str, dict[str, Any]] = {}
            chosen: list[ToolManifest] = []
            by_cap: dict[str, list[tuple[float, ToolManifest]]] = {}
            for man in survivors:
                cap = man.capability or man.id
                by_cap.setdefault(cap, []).append((self._score(man), man))
            for cap, lst in sorted(by_cap.items()):
                lst.sort(key=lambda x: (-x[0], x[1].id))
                # A capability with several *providers* (rg/grep/python search backends,
                # pwsh/powershell shells) collapses to the winner. Several tools that
                # merely share a capability namespace (jobs/wait/kill) are not
                # interchangeable, so they all stay advertised.
                providers = {m.provider for _s, m in lst} - {None}
                is_provider_race = len(lst) > 1 and len(providers) > 1
                if dedupe_capability and is_provider_race:
                    win = lst[0][1]
                    selected[cap] = win.id
                    receipts[cap] = self._selection_receipt(cap, lst)
                    chosen.append(win)
                else:
                    if len(lst) == 1:
                        receipts[cap] = self._selection_receipt(cap, lst)
                    chosen.extend(m for _s, m in lst)
            chosen.sort(key=lambda m: (m.group, m.id))
            budget_drops: dict[str, str] = {}
            if token_budget:
                kept: list[ToolManifest] = []
                running = 0
                for man in sorted(chosen, key=lambda m: -self._score(m)):
                    cost = man.tokens_estimate()
                    if kept and running + cost > token_budget:
                        budget_drops[man.id] = f"token budget {token_budget}"
                        continue
                    kept.append(man)
                    running += cost
                chosen = sorted(kept, key=lambda m: (m.group, m.id))
            if max_tools and len(chosen) > max_tools:
                kept = chosen[:max_tools]
                for man in chosen[max_tools:]:
                    budget_drops.setdefault(man.id, f"max_tools {max_tools}")
                chosen = kept
            tokens = sum(m.tokens_estimate() for m in chosen)

            digest_rows = [["tier", tier]] + [
                [m.id, m.version, sorted(m.tags), gates.get(m.id, AdGate(True)).available]
                for m in chosen
            ]
            if budget_drops:
                digest_rows.append({"dropped": sorted(budget_drops)})
            digest = short_hash(compact_json(digest_rows), 16)
            return AdSnapshot(tools=chosen, gates=gates, tokens=tokens, digest=digest,
                              selected=selected, selection_receipts=receipts,
                              budget_drops=budget_drops, tier=tier)
        finally:
            self.profile = saved_profile

    def _selection_receipt(self, capability: str,
                           ranked: list[tuple[float, ToolManifest]]) -> dict[str, Any]:
        """Why this capability's winner won - the honest, asserted receipt.

        The ranking is risk penalty + declared priority + observed success
        (`ProviderStats.score()`, which needs >= 3 calls before it counts). A
        receipt with no call evidence says so instead of pretending the score is
        learned behaviour.
        """
        wscore, wman = ranked[0]
        stat = self._stats.get(wman.id)
        competitors = [{"id": s[1].id, "provider": s[1].provider, "priority": s[1].priority,
                        "risk": s[1].risk, "score": round(s[0], 2)}
                       for s in ranked[1:]]
        if not competitors:
            why = "sole provider for this capability (no provider race)"
            sole = True
        elif stat is not None and stat.calls >= 3:
            why = (f"won provider race on observed reliability x latency (calls={stat.calls}, "
                   f"success={stat.success_rate:.2f}, mean={stat.mean_ms:.0f}ms) over "
                   + ", ".join(c["id"] for c in competitors))
            sole = False
        else:
            why = ("won provider race on declared priority (no call evidence yet; observed "
                   "success counts after 3 calls) over "
                   + ", ".join(c["id"] for c in competitors))
            sole = False
        return {"capability": capability, "tool": wman.id, "provider": wman.provider,
                "score": round(wscore, 2), "priority": wman.priority, "risk": wman.risk,
                "why": why, "sole": sole, "competitors": competitors}

    def _score(self, man: ToolManifest) -> float:
        stat = self._stats.get(man.id)
        return float(RISK_ORDER.get(man.risk, 0)) * -2.0 + man.priority + (stat.score() * 40 if stat else 0.0)

    # ------------------------------------------------------------------- search
    def search(self, query: str, *, limit: int = 8, include_gated: bool = False,
               group: str | None = None, max_risk: str | None = None,
               dialect: str | None = None, explain: bool = False) -> list[dict[str, Any]]:
        """Deterministic lexical ranking. (P5a: `explain` adds per-hit reasons;
        the semantic stage sits on top via `route`, never inside here.)"""
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        max_idx = RISK_ORDER.get(max_risk, 99) if max_risk else 99
        out: list[tuple[float, dict[str, Any]]] = []
        for man in self.all():
            g = self.gate(man)
            if not g.available and not include_gated:
                continue
            if group and man.group != group:
                continue
            if RISK_ORDER.get(man.risk, 0) > max_idx:
                continue
            if dialect and dialect not in ([*man.tags, man.provider or "", man.id]):
                continue
            score = _score_match(q_tokens, man)
            if score <= 0:
                continue
            hit = {
                "id": man.id, "title": man.title, "capability": man.capability,
                "risk": man.risk, "group": man.group, "score": round(score, 3),
                "description": man.description.strip().split("\n")[0][:200],
                "available": g.available, **({"gated": g.to_dict()} if not g.available else {}),
            }
            if explain:
                hit["reasons"] = _match_reasons(q_tokens, man)
            out.append((score, hit))
        out.sort(key=lambda x: (-x[0], x[1]["id"]))
        return [d for _s, d in out[:limit]]

    # ------------------------------------------------------------------- routing
    def _exact_match(self, task: str) -> str | None:
        """Exact-name fast path: `fs.patch`, `fs_patch` (a host that sanitises dots),
        or an id typed after a slash never loses to fuzzy ranking."""
        q = (task or "").strip().lower().replace("/", ".")
        if not q:
            return None
        for man in self.all():
            if q in (man.id, man.mcp_name, man.id.replace(".", "_"), man.id.replace(".", "/")):
                return man.id
        return None

    def route(self, task: str, *, k: int = 8, semantic: bool = False,
              include_gated: bool = False, group: str | None = None) -> dict[str, Any]:
        """Two-stage router (P5a): exact-name, then deterministic lexical, then the
        optional semantic backend. Returns scores *and* the reasons behind them.

        `semantic=True` means "use a registered `skeletonkey.semantic` backend if one
        exists"; with none installed (the shipped state) the answer is the lexical
        one and `note` says exactly that - no silent stage, no fabricated scores.
        """
        k = max(1, min(int(k), 50))
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        exact = self._exact_match(task)
        if exact is not None:
            man = self.get(exact)
            gate = self.gate(man)
            results.append({"id": man.id, "title": man.title, "score": 1.0,
                            "tier": man.tier, "provider": man.provider,
                            "reasons": ["exact name match"], "available": gate.available,
                            **( {"gated": gate.to_dict()} if not gate.available else {})})
            seen.add(man.id)
        hits = self.search(task, limit=k + len(seen), include_gated=include_gated,
                           group=group, explain=True)
        for h in hits:
            if h["id"] in seen:
                continue
            results.append(h)
            if len(results) >= k:
                break

        backends = discover_backends() if semantic else []
        mode = "semantic" if backends else "lexical"
        note = None
        if semantic and not backends:
            note = ("no semantic backend installed (entry-point group "
                    "skeletonkey.semantic); the deterministic lexical stage answered")
        if backends:
            b = backends[0]
            for r in results:
                r["semantic_score"] = round(float(b.score(task, r.get("description") or "")), 4)
            results.sort(key=lambda r: -(0.5 * float(r.get("score") or 0.0)
                                         + 0.5 * float(r.get("semantic_score") or 0.0)))
        return {"task": task[:300], "k": k, "mode": mode,
                "backend": (backends[0].name if backends else None),
                "backends_available": len(backends),
                "results": results[:k], "count": min(len(results), k),
                **( {"note": note} if note else {})}

    # ------------------------------------------------------------------ explain
    def explain(self, capability: str, *, k: int = 50) -> dict[str, Any]:
        """Why a capability is (or is not) advertised here, with receipts.

        Accepts a capability name (`search.text`) or a tool id (resolved to its own
        capability). Every candidate row carries its gate reasons, its score, and
        whether it won; the winner entry is the same receipt the snapshot carries.
        """
        cap = (capability or "").strip()
        if not cap:
            raise SkeletonKeyError(E.MISSING_ARG, "pass a capability or tool id",
                                   details={"examples": ["search.text", "fs.patch", "shell.run"]})
        if self.has(cap):
            cap = self.get(cap).capability or cap
        cands = self.by_capability(cap)
        if not cands:
            near = [c for c in sorted({m.capability for m in self.all()}) if cap.lower() in c.lower()]
            raise SkeletonKeyError(E.BAD_ARGS, f"no tool claims capability {cap!r}",
                                   details={"capability": cap, "near": near[:5],
                                            "count": len(self.all())},
                                   next_actions=[{"tool": "registry.list", "args": {}}])
        snap = self.advertise()
        rows = []
        for man in sorted(cands, key=lambda m: (-self._score(m), m.id)):
            gate = self.gate(man)
            rows.append({
                "id": man.id, "provider": man.provider, "priority": man.priority,
                "risk": man.risk, "tier": man.tier, "score": round(self._score(man), 2),
                "stats": self.stats(man.id)[man.id],
                "gate": gate.to_dict(), "advertised": man.id in snap.names,
            })
        winner = snap.selection_receipts.get(cap)
        if winner is None:
            advertised = [r["id"] for r in rows if r["advertised"]]
            winner = {"tool": None, "why": ("no tool for this capability is advertised "
                                            "in the current tier; see rows for gates")
                      if not advertised else "shared capability namespace: no provider race "
                      "(these tools are not interchangeable, so all stay)"}
        return {"capability": cap, "tier": snap.tier,
                "registered": len(cands), "advertised": [r["id"] for r in rows if r["advertised"]],
                "gated_out": [r["id"] for r in rows if not r["advertised"]],
                "winner": winner, "tools": rows[:k]}

    # ---------------------------------------------------------------- drop-ins
    def load_dir(self, path: str, *, source: str = "dropin", replace: bool = False) -> dict[str, Any]:
        """Import `*.py` files from a directory; each may expose TOOL(S)/register().

        Contract (see docs/PLUGIN-CONTRACT.md):
          module.TOOL: ToolManifest            - single tool, handler set
          module.TOOLS: list[ToolManifest]     - several
          module.register(reg)                 - imperative
        """
        report = {"dir": path, "files": 0, "added": [], "skipped": [], "errors": []}
        if not os.path.isdir(path):
            report["skipped"].append("missing-dir")
            return report
        for entry in sorted(os.listdir(path)):
            if not entry.endswith(".py") or entry.startswith(("_", ".")):
                continue
            fpath = os.path.join(path, entry)
            try:
                mod = _import_from_path(fpath)
            except Exception as exc:
                report["errors"].append({"file": entry, "error": f"{type(exc).__name__}: {exc}"})
                self.load_errors.append({"file": fpath, "stage": "import", "error": str(exc)[:400]})
                continue
            report["files"] += 1
            found = 0
            if hasattr(mod, "register") and callable(mod.register):
                try:
                    mod.register(self)
                    found += 1
                except Exception as exc:
                    report["errors"].append({"file": entry, "error": f"register(): {exc}"})
            for man in _manifests_of(mod, source=source, source_path=fpath):
                try:
                    self.register(man, replace=replace)
                    report["added"].append(man.id)
                    found += 1
                except SkeletonKeyError as exc:
                    report["skipped"].append({"id": man.id, "reason": exc.err.code, "message": str(exc)})
            if not found:
                report["skipped"].append({"file": entry, "reason": "no TOOL/TOOLS/register() found"})
        self.loaded_dirs.append(path)
        return report

    def load_entry_points(self, group: str = "skeletonkey.tools") -> list[str]:
        added: list[str] = []
        try:
            from importlib.metadata import entry_points

            # requires-python >= 3.11, so the group= form always exists; a broken
            # distribution's metadata is the only thing that can raise here.
            eps = entry_points(group=group)
        except Exception:
            return added
        for ep in eps or []:
            try:
                obj = ep.load()
            except Exception as exc:
                self.load_errors.append({"entry_point": ep.name, "stage": "load", "error": str(exc)[:400]})
                continue
            for man in _manifests_of_obj(obj, source=f"entrypoint:{ep.name}"):
                try:
                    self.register(man, replace=False)
                    added.append(man.id)
                except SkeletonKeyError as exc:
                    self.load_errors.append({"entry_point": ep.name, "stage": "register", "error": str(exc)[:200]})
        return added

    # ------------------------------------------------------------------- misc
    def overview(self) -> dict[str, Any]:
        groups: dict[str, dict[str, int]] = {}
        for t in self.all():
            g = groups.setdefault(t.group, {"total": 0, "mutating": 0})
            g["total"] += 1
            if t.is_mutating:
                g["mutating"] += 1
        return {
            "tools": len(self._tools), "generation": self._generation,
            "groups": groups, "loaded_dirs": self.loaded_dirs,
            "load_errors": self.load_errors,
            "capabilities": sorted({t.capability for t in self._all_values()}),
        }

    def _all_values(self) -> list[ToolManifest]:
        return list(self._tools.values())

    def snapshot_json(self) -> str:
        return compact_json([t.to_dict(include_schema=False) for t in self.all()])

    def describe(self, tool_id: str) -> dict[str, Any]:
        man = self.get(tool_id)
        gate = self.gate(man)
        d = man.to_dict(include_schema=True, include_handler=True)
        d["availability"] = gate.to_dict()
        d["stats"] = self.stats(man.id)
        d["typical"] = {"latency_ms": man.typical_latency_ms, "output_bytes": man.typical_output_bytes,
                        "timeout_s": man.timeout_s, "reversible": man.reversible}
        # P5a: the provider receipt from the current advertisement, or an honest
        # "not in the current advertisement" entry - why-not is data either way.
        snap = self.advertise()
        if man.id in snap.names:
            d["provider_receipt"] = snap.receipt_for(man)
            d["advertisement"] = {"tier": snap.tier, "advertised": True}
        else:
            d["provider_receipt"] = {"tool": man.id, "sole": False,
                                     "why": "not in the current advertisement; see availability "
                                            "(or the tier gate: expand first)"}
            d["advertisement"] = {"tier": snap.tier, "advertised": False}
        if man.examples:
            d["examples"] = man.examples
        if man.anti_patterns:
            d["anti_patterns"] = man.anti_patterns
        return d


# --------------------------------------------------------------------- helpers


def _manifests_of(mod: Any, *, source: str, source_path: str) -> list[ToolManifest]:
    out: list[ToolManifest] = []
    for attr in ("TOOL", "TOOLS", "MANIFESTS", "MANIFEST"):
        obj = getattr(mod, attr, None)
        if obj is None:
            continue
        out.extend(_manifests_of_obj(obj, source=source, source_path=source_path))
    return out


def _manifests_of_obj(obj: Any, *, source: str, source_path: str | None = None) -> list[ToolManifest]:
    if isinstance(obj, ToolManifest):
        return [obj]
    if isinstance(obj, dict):
        return [ToolManifest.from_dict(obj, source=source, source_path=source_path)]
    items: list[ToolManifest] = []
    if isinstance(obj, (list, tuple)):
        for item in obj:
            items.extend(_manifests_of_obj(item, source=source, source_path=source_path))
        return items
    if callable(obj):
        produced = obj()
        if isinstance(produced, (list, tuple)):
            for item in produced:
                items.extend(_manifests_of_obj(item, source=source, source_path=source_path))
        elif isinstance(produced, ToolManifest):
            items.append(produced)
    return items


def _import_from_path(path: str) -> Any:
    name = "sk_dropin_" + re.sub(r"\W+", "_", os.path.splitext(os.path.basename(path))[0]) + \
        "_" + short_hash(path, 6)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


_STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with", "by", "is", "are", "be", "this", "that", "it", "its", "you", "your", "we", "our", "from", "at", "as", "if", "then", "else", "when", "how", "what", "which", "can", "could", "would", "should", "do", "does", "not", "no", "yes", "all", "any"}

# P5a: the deterministic stage's synonym layer. Task verbs are not queries about
# words - "rename a symbol" must surface `fs.patch` ("replace edits") next to
# `fs.move` ("rename"), or discovery quietly hides the right tool behind wording the
# author did not use. Pure data, zero deps, and every entry earns its keep on the
# eval suite (tests/test_discovery.py asserts the top-k hit rate).
_INTENT: dict[str, tuple[str, ...]] = {
    "rename": ("rename", "move", "patch", "replace", "edit"),
    "move": ("move", "rename", "mv"),
    "find": ("find", "search", "locate", "grep", "glob", "match", "look"),
    "search": ("search", "find", "locate", "grep", "match", "glob", "read", "scan"),
    "look": ("look", "search", "find", "glob", "list"),
    "match": ("match", "search", "find", "grep", "glob"),
    "edit": ("edit", "patch", "replace", "change", "update", "rename", "modify"),
    "replace": ("replace", "patch", "edit", "rename", "change", "write"),
    "change": ("change", "patch", "edit", "update", "replace", "write"),
    "update": ("update", "patch", "edit", "replace", "change", "write"),
    "rewrite": ("rewrite", "write", "convert", "normalize", "replace", "patch"),
    "write": ("write", "create", "add", "save", "new"),
    "create": ("create", "write", "add", "mkdir", "new"),
    "add": ("add", "write", "create", "append", "install", "patch", "insert"),
    "extract": ("extract", "write", "create", "split", "move", "save"),
    "split": ("split", "extract", "separate", "move"),
    "delete": ("delete", "remove", "erase"),
    "remove": ("remove", "delete", "erase"),
    "erase": ("erase", "delete", "remove"),
    "run": ("run", "execute", "exec", "shell", "script", "background"),
    "execute": ("execute", "run", "exec", "shell"),
    "background": ("background", "run", "execute", "job", "daemon"),
    "watch": ("watch", "wait", "job", "poll", "monitor"),
    "wait": ("wait", "watch", "job", "poll"),
    "undo": ("undo", "revert", "rollback", "restore"),
    "revert": ("revert", "undo", "rollback", "restore"),
    "verify": ("verify", "check", "confirm", "assert", "ensure", "search"),
    "check": ("check", "verify", "inspect", "test", "stat", "search"),
    "read": ("read", "view", "cat", "inspect", "load", "offset"),
    "list": ("list", "ls", "show", "glob", "enumerate", "count"),
    "stat": ("stat", "inspect", "metadata", "size", "check"),
    "install": ("install", "setup", "bootstrap", "add"),
    "count": ("count", "list", "glob", "search"),
    "glob": ("glob", "list", "count", "search", "pattern"),
    "build": ("build", "run", "execute", "shell", "script", "compile"),
    "shrink": ("shrink", "stat", "size", "check"),
    "shrinks": ("shrinks", "stat", "size", "check", "shrink"),
    "secret": ("secret", "search", "key", "find", "scan"),
    "key": ("key", "secret", "search", "find", "scan", "patch", "replace"),
    "hardcoded": ("hardcoded", "search", "secret", "find", "scan"),
    "leak": ("leak", "search", "secret", "find", "scan"),
    "rotate": ("rotate", "replace", "patch", "edit", "change"),
    "dependency": ("dependency", "patch", "edit", "write", "add", "requirements"),
    "version": ("version", "patch", "edit", "write", "field"),
    "module": ("module", "write", "create", "file", "split", "extract"),
    "function": ("function", "write", "create", "extract", "module"),
    "crlf": ("crlf", "write", "newline", "convert", "rewrite", "normalize"),
    "line": ("line", "read", "write", "patch", "search"),
    "size": ("size", "stat", "check", "inspect", "metadata"),
    "case": ("case", "search", "ignore", "match"),
    "left": ("left", "search", "remain", "find", "count"),
    "remain": ("remain", "search", "count", "find", "glob"),
    "executable": ("executable", "chmod", "mode", "permission"),
    "mode": ("mode", "chmod", "permission", "executable"),
    "directory": ("directory", "mkdir", "create", "list"),
    "tree": ("tree", "mkdir", "create", "list", "directory"),
    "offset": ("offset", "read", "line", "limit"),
    "first": ("first", "read", "line", "offset"),
}


def _intent_expand(tok: str) -> list[str]:
    """A query token plus its synonyms, for the scoring pass.

    Unordered, capped (a token can only be responsible for so many hits before the
    expansion becomes noise), and *only* used to widen recall - the base token still
    scores highest because an exact hit beats a synonym hit.
    """
    return list(dict.fromkeys([tok, *_INTENT.get(tok, ())]))[:6]


def _tokenize(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9_]*|\d+", (text or "").lower())
    toks: list[str] = []
    for r in raw:
        for part in re.split(r"[_\-./]+", r):
            if part and part not in _STOP and len(part) > 1:
                toks.append(part)
    return toks


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _hit(tok: str, words: list[str]) -> float:
    """Word-boundary matching only: exact == 1.0, prefix (file->filesystem) == 0.5.

    Substring scoring ("file" inside "pro*file*", "name" inside "*name*space")
    promoted capability-noise tools above the tools that actually do the job, so
    we refuse to match inside a word at all.
    """
    best = 0.0
    for w in words:
        if w == tok:
            return 1.0
        if len(tok) >= 3 and w.startswith(tok):
            best = max(best, 0.5)
    return best


def _hit_expanded(tok: str, words: list[str]) -> tuple[float, str]:
    """Best hit for a token across its synonym expansion.

    Returns (score, matched_word). The base token is not discounted; a synonym hit
    is worth 85% of an exact hit of the same kind, so wording never beats wording
    but a real synonym never loses to nothing.
    """
    cands = _intent_expand(tok)
    best, best_w = _hit(tok, words), tok
    for syn in cands[1:]:
        h = _hit(syn, words) * 0.85
        if h > best:
            best, best_w = h, syn
    return best, best_w


def _score_match(q: list[str], man: ToolManifest) -> float:
    if not q:
        return 0.0
    name_words = re.split(r"[._\-/]", man.id.lower())
    desc_words = _words(man.description) + _words(man.title)
    tag_words = [t.lower() for t in man.tags]
    cap_words = _words(" ".join([man.capability, *man.provides]))
    toks = set(q)
    score = 0.0
    covered = 0
    for tok in toks:
        h_name, _ = _hit_expanded(tok, name_words)
        h_cap, _ = _hit_expanded(tok, cap_words)
        h_tag, _ = _hit_expanded(tok, tag_words)
        h_desc, _ = _hit_expanded(tok, desc_words)
        total = 4.0 * h_name + 2.2 * h_cap + 1.6 * h_tag + 1.0 * h_desc
        if total > 0:
            covered += 1
        score += total
    # A tool matching every word of the query should beat one matching one word,
    # even if that one word is a high-weight field.
    score *= 0.55 + 0.45 * (covered / max(1, len(toks)))
    # id-phrase bonus: "fs.search" typed verbatim should beat everything
    joined = " ".join(set(q))
    if joined and joined in man.id.lower().replace(".", " ").replace("_", " "):
        score *= 1.25
    return score / (1.0 + 0.015 * len(q))


def _match_reasons(q: list[str], man: ToolManifest) -> list[str]:
    """Why a tool matched: field + token + exact/prefix, capped for the wire.

    A routing decision is exposed or it is a guess - this is the exposure half of
    the P5a receipt (the provider-receipt half is the snapshot's
    `selection_receipts`).
    """
    name_words = re.split(r"[._\-/]", man.id.lower())
    cap_words = _words(" ".join([man.capability, *man.provides]))
    tag_words = [t.lower() for t in man.tags]
    desc_words = _words(man.description) + _words(man.title)
    reasons: list[str] = []
    for tok in dict.fromkeys(q):
        for label, words in (("name", name_words), ("capability", cap_words),
                             ("tags", tag_words), ("description", desc_words)):
            h, word = _hit_expanded(tok, words)
            if h > 0:
                kind = "syn" if word != tok else ("exact" if h >= 1.0 else "prefix")
                reasons.append(f"{label}:{kind}:{word}" + (f"<-{tok}" if word != tok else ""))
    return reasons[:6]


def _similar(a: str, b: str) -> float:
    """Cheap normalized similarity for did-you-mean."""
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    # bigram dice coefficient
    def bigrams(s: str) -> set[str]:
        return {s[i:i + 2] for i in range(len(s) - 1)}

    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2.0 * len(ba & bb) / (len(ba) + len(bb))


def parse_requirements(raw: list[dict[str, Any]] | list[str]) -> list[Requirement]:
    out: list[Requirement] = []
    for item in raw:
        if isinstance(item, str):
            kind = "binary"
            name = item
            if ":" in item:
                kind, name = item.split(":", 1)
            out.append(Requirement(kind, name))
        else:
            out.append(Requirement(**item))
    return out


def new_id(prefix: str = "call") -> str:
    return f"{prefix}_{new_run_id()}"
