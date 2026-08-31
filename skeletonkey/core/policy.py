"""Policy engine: rules as data, one evaluation point per call.

The P3 deliverable: `allow` / `deny` / `escalate` / `rate_limit` rules with
per-pattern matchers, compiled once per engine and evaluated in
`Engine._authorize` before any handler runs. The invariants:

* **deny stays non-overridable.** No approval token, grant, allow rule or
  flag clears a deny match - it is checked before any of those are read.
* **allow only removes the approval requirement** for matched calls. It never
  clears a deny, never touches the `read_only` wall (that lives in
  `_guard_gate` and is re-checked in the approval flow), and never changes
  what a tool may do - it changes who had to say yes.
* **every refusal carries the rule text and a fix.** The matched rule, the
  evidence (which argument, which glob/prefix fired) and the rule's `reason`
  ride in `error.details`, because policy that reads as advice gets ignored.

Rule grammar (config, `[policy]`):

    [[policy.rule]]
    action = "deny"                # deny | allow | escalate | rate_limit
    tool = "shell.run"             # tool id or glob; omit for any tool
    paths = ["**/.git/**"]         # path-ish args (path, src, dst, dest, target,
                                   # cwd, script, command): full value and basename
    argv_prefix = [["git", "push", "--force"]]  # prefixes of the `argv` array arg
    env = ["TOKEN_*"]              # globs of *names* in the `env` object arg
    reason = "why, and how to fix it"
    rate = 20                      # rate_limit only: max calls ...
    window_s = 60                  # ... in this sliding window (default 60)

A rule fires when its tool pattern matches **and** every non-empty constraint
it carries fires (any pattern within a constraint may match). A rule with no
constraints matches every call of the tool. The legacy `policy.deny` strings
(`tool(glob)`) and `policy.escalate` list compile to the same rule objects, as
does the `policy.rate_limits` map - one matcher, three spellings.

Matcher coverage, stated honestly: `host:port` matching for future network
tools is reserved, not implemented - there is no network tool to match yet,
and a matcher nobody exercises is how a rule ends up meaning something other
than what it says.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from typing import Any

from .util import compact_json
from .util import glob_hit as _glob_hit

_PATH_KEYS = ("path", "src", "dst", "dest", "target", "cwd", "script", "command")
_ACTIONS = ("deny", "allow", "escalate", "rate_limit")
_DENY_RE = re.compile(r"^([a-z0-9._*-]+)(?:\((.*)\))?$", re.I)


@dataclass
class PolicyRule:
    """One compiled policy rule. Immutable once built; `source` names where
    it came from so a refusal can point at the config line to fix."""

    action: str                                   # deny | allow | escalate | rate_limit
    tool: str | None = None                       # None = any tool; else id or glob
    paths: tuple[str, ...] = ()
    argv_prefixes: tuple[tuple[str, ...], ...] = ()
    env: tuple[str, ...] = ()
    reason: str = ""
    rate: int = 0
    window_s: float = 60.0
    source: str = ""

    # ------------------------------------------------------------- matching
    def matches_tool(self, tool_id: str) -> bool:
        if not self.tool or self.tool in ("*", "**"):
            return True
        return fnmatch.fnmatch(tool_id, self.tool)

    def match_args(self, args: dict[str, Any]) -> dict[str, Any] | None:
        """Evidence of which constraint fired, or None.

        Returns `{}` for a rule with no constraints: such a rule matches every
        call of the tool, and the evidence is the (empty) argument set.
        """
        if not (self.paths or self.argv_prefixes or self.env):
            return {}
        for key in _PATH_KEYS:
            value = args.get(key)
            if not isinstance(value, str) or not value:
                continue
            cand = value.replace("\\", "/")
            low = cand.lower()
            base = os.path.basename(low)
            for glob in self.paths:
                if _glob_hit(glob, low) or _glob_hit(glob, base) \
                        or _glob_hit(glob.replace("**/", ""), low):
                    return {"arg": key, "value": value[:200], "glob": glob}
        argv = args.get("argv")
        if isinstance(argv, (list, tuple)) and argv and all(isinstance(a, str) for a in argv):
            for prefix in self.argv_prefixes:
                if prefix and tuple(argv[: len(prefix)]) == prefix:
                    return {"arg": "argv", "argv": list(argv[:8]), "prefix": list(prefix)}
        env = args.get("env")
        if isinstance(env, dict):
            for name in env:
                for glob in self.env:
                    if fnmatch.fnmatch(str(name), glob):
                        return {"arg": "env", "name": str(name), "glob": glob}
        return None

    def matches(self, tool_id: str, args: dict[str, Any]) -> bool:
        return self.matches_tool(tool_id) and self.match_args(args) is not None

    # ------------------------------------------------------------- plumbing
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"action": self.action, "source": self.source}
        if self.tool:
            d["tool"] = self.tool
        if self.paths:
            d["paths"] = list(self.paths)
        if self.argv_prefixes:
            d["argv_prefix"] = [list(p) for p in self.argv_prefixes]
        if self.env:
            d["env"] = list(self.env)
        if self.reason:
            d["reason"] = self.reason
        if self.rate:
            d["rate"] = self.rate
            d["window_s"] = self.window_s
        return d


def _str_list(value: Any, what: str) -> tuple[str, ...] | str:
    """A tuple of strings, or an error string."""
    if not isinstance(value, (list, tuple)):
        return f"{what} must be a list of strings"
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return f"{what} must be a list of non-empty strings"
        out.append(item)
    return tuple(out)


def policy_rule_from_dict(raw: Any, *, default_window_s: float = 60.0) -> tuple[PolicyRule | None, str | None]:
    """Parse one structured rule from config data. Returns (rule, error)."""
    if not isinstance(raw, dict):
        return None, "a policy rule must be a table"
    known = {"action", "tool", "paths", "argv_prefix", "env", "reason", "rate", "window_s"}
    unknown = sorted(set(raw) - known)
    if unknown:
        return None, f"unknown field(s) {unknown} (a typo here silently weakens policy, so it is refused)"
    action = str(raw.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return None, f"action {raw.get('action')!r} is not one of {list(_ACTIONS)}"
    tool = raw.get("tool")
    if tool is not None and not isinstance(tool, str):
        return None, "tool must be a string (id or glob)"
    paths: tuple[str, ...] = ()
    if raw.get("paths") is not None:
        r = _str_list(raw["paths"], "paths")
        if isinstance(r, str):
            return None, r
        paths = r
    prefixes: tuple[tuple[str, ...], ...] = ()
    if raw.get("argv_prefix") is not None:
        if not isinstance(raw["argv_prefix"], (list, tuple)):
            return None, "argv_prefix must be a list of prefix lists"
        out = []
        for prefix in raw["argv_prefix"]:
            if not isinstance(prefix, (list, tuple)) or not prefix:
                return None, "each argv_prefix entry must be a non-empty list of strings"
            r = _str_list(prefix, "argv_prefix entry")
            if isinstance(r, str):
                return None, r
            out.append(r)
        prefixes = tuple(out)
    env: tuple[str, ...] = ()
    if raw.get("env") is not None:
        r = _str_list(raw["env"], "env")
        if isinstance(r, str):
            return None, r
        env = r
    reason = str(raw.get("reason") or "")
    rate = raw.get("rate")
    if rate is not None and (isinstance(rate, bool) or not isinstance(rate, int) or rate <= 0):
        return None, "rate must be a positive integer"
    window_s = raw.get("window_s", default_window_s)
    if isinstance(window_s, bool) or not isinstance(window_s, (int, float)) or float(window_s) <= 0:
        return None, "window_s must be a positive number"
    window_s = float(window_s)
    if action == "rate_limit" and not rate:
        return None, "a rate_limit rule needs rate (> 0)"
    return PolicyRule(action=action, tool=tool, paths=paths, argv_prefixes=prefixes,
                      env=env, reason=reason, rate=int(rate or 0), window_s=window_s), None


class CompiledPolicy:
    """All rules of one engine, in config order. Query order is fixed:
    deny first (absolute), then escalate, then rate_limit, then allow."""

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self.rules = rules or []
        self._deny = [r for r in self.rules if r.action == "deny"]
        self._allow = [r for r in self.rules if r.action == "allow"]
        self._escalate = [r for r in self.rules if r.action == "escalate"]
        self._rate = [r for r in self.rules if r.action == "rate_limit"]

    @classmethod
    def from_config(cls, cfg: Any) -> tuple[CompiledPolicy, list[str]]:
        """Compile legacy + structured config into rules. Malformed entries
        are reported (never raised): a config typo must degrade loudly, not
        take the toolkit down - but it must also never widen policy, so a
        broken rule is skipped, not guessed at."""
        pol = cfg.policy
        rules: list[PolicyRule] = []
        errors: list[str] = []
        for i, rule in enumerate(pol.deny or ()):
            if not isinstance(rule, str):
                errors.append(f"policy.deny[{i}] must be a string, got {type(rule).__name__}")
                continue
            m = _DENY_RE.match(rule.strip())
            if not m:
                errors.append(f"unparseable deny rule: {rule!r}")
                continue
            paths = (m.group(2),) if m.group(2) else ()
            rules.append(PolicyRule("deny", m.group(1), paths=paths,
                                    reason="policy.deny rule", source=f"policy.deny[{i}]"))
        for i, tool in enumerate(pol.escalate or ()):
            if not isinstance(tool, str):
                errors.append(f"policy.escalate[{i}] must be a string, got {type(tool).__name__}")
                continue
            rules.append(PolicyRule("escalate", tool, reason="policy.escalate entry",
                                    source=f"policy.escalate[{i}]"))
        for i, raw in enumerate(pol.rule or ()):
            parsed, err = policy_rule_from_dict(raw, default_window_s=float(pol.rate_window_s))
            if err:
                errors.append(f"policy.rule[{i}] ({compact_json(raw)[:120]}): {err}")
                continue
            parsed.source = f"policy.rule[{i}]"
            rules.append(parsed)
        for tool, rate in (pol.rate_limits or {}).items():
            if isinstance(rate, bool) or not isinstance(rate, int) or rate <= 0:
                errors.append(f"policy.rate_limits[{tool!r}] must be a positive integer, got {rate!r}")
                continue
            rules.append(PolicyRule("rate_limit", str(tool), rate=int(rate),
                                    window_s=float(pol.rate_window_s),
                                    reason=f"rate limit: at most {rate} calls per {float(pol.rate_window_s):g}s",
                                    source=f"policy.rate_limits[{tool!r}]"))
        return cls(rules), errors

    # ------------------------------------------------------------- queries
    def deny_hit(self, tool_id: str, args: dict[str, Any]) -> tuple[PolicyRule, dict[str, Any]] | None:
        for rule in self._deny:
            if rule.matches_tool(tool_id):
                evidence = rule.match_args(args)
                if evidence is not None:
                    return rule, evidence
        return None

    def allow_hit(self, tool_id: str, args: dict[str, Any]) -> tuple[PolicyRule, dict[str, Any]] | None:
        for rule in self._allow:
            if rule.matches_tool(tool_id):
                evidence = rule.match_args(args)
                if evidence is not None:
                    return rule, evidence
        return None

    def escalate_hit(self, tool_id: str, args: dict[str, Any]) -> tuple[PolicyRule, dict[str, Any]] | None:
        for rule in self._escalate:
            if rule.matches_tool(tool_id):
                evidence = rule.match_args(args)
                if evidence is not None:
                    return rule, evidence
        return None

    def strictest_rate(self, tool_id: str, args: dict[str, Any]) -> tuple[PolicyRule, dict[str, Any]] | None:
        """The tightest matching rate rule (lowest rate; tie -> shortest window)."""
        best: tuple[PolicyRule, dict[str, Any]] | None = None
        for rule in self._rate:
            if not rule.matches_tool(tool_id):
                continue
            evidence = rule.match_args(args)
            if evidence is not None and (best is None or (rule.rate, rule.window_s)
                                         < (best[0].rate, best[0].window_s)):
                best = (rule, evidence)
        return best

    def summary(self) -> dict[str, Any]:
        by: dict[str, int] = {}
        for r in self.rules:
            by[r.action] = by.get(r.action, 0) + 1
        return {"rules": len(self.rules), "by_action": by,
                "rate_limits": [r.to_dict() for r in self._rate]}
