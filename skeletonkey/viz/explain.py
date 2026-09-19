"""V4: why-not. The most valuable question about a tool is why you cannot call it.

`sk viz` already says *that* a tool is withheld (V0's `withheld_by`) and shows the
gate's own prose. This module answers the follow-up an operator actually has:
**which rule decided, in what order, and what would have to change.**

The chain here mirrors `Registry.gate()` and `Registry.advertise()` step for
step, in their real order:

  1. `tools.disable` config          (fatal, and it short-circuits everything)
  2. `advertised = False`            (recorded, then enforced at step 7)
  3. `policy.read_only` vs mutating  (fatal)
  4. `platforms` vs host OS          (fatal)
  5. `requirements`   - all must hold
  6. `require_any`    - at least one must hold
  7. discovery tier
  8. internal (the step-2 flag, enforced)
  9. provider de-duplication
 10. token / max_tools budget

Mirroring is a liability - two implementations of one rule drift - so
`test_viz_explain.py` asserts the property that makes it safe: **a tool is
advertised if and only if its chain has no failing step**, for every tool, under
every projection the tests try. If the registry's logic changes and this does
not, that test fails.

Also here: hypothetical-host projection (what would a Windows box see?) and
model diffing (what changed between two snapshots?), both pure functions.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

# The vocabulary of decisions. `outcome` is one of:
#   pass    - this rule looked and was satisfied
#   fail    - this rule is why the tool is not advertised
#   note    - recorded, decided elsewhere (step 2 defers to step 8)
#   skip    - the rule does not apply to this tool
PASS, FAIL, NOTE, SKIP = "pass", "fail", "note", "skip"


@dataclass
class Step:
    """One rule, its verdict, and the evidence behind it."""

    rule: str
    outcome: str
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    fix: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rule": self.rule, "outcome": self.outcome}
        if self.detail:
            out["detail"] = self.detail
        if self.evidence:
            out["evidence"] = self.evidence
        if self.fix:
            out["fix"] = self.fix
        return out


@dataclass
class Chain:
    """The full decision path for one tool."""

    id: str
    advertised: bool
    steps: list[Step]

    @property
    def blocked_by(self) -> list[Step]:
        return [s for s in self.steps if s.outcome == FAIL]

    @property
    def verdict(self) -> str:
        failed = self.blocked_by
        if not failed:
            return "advertised on this host"
        return failed[0].detail or failed[0].rule

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "advertised": self.advertised,
                "verdict": self.verdict,
                "blocked_by": [s.rule for s in self.blocked_by],
                "steps": [s.to_json() for s in self.steps]}


def _req_text(req: Any) -> str:
    text = f"{req.kind}:{req.name}"
    if getattr(req, "min_version", None):
        text += f" >= {req.min_version}"
    return text


def explain_tool(registry: Any, tool_id: str, *, profile: Any = None,
                 tier: str | None = None, read_only: bool = False,
                 disabled: list[str] | None = None, _snap: Any = None) -> Chain:
    """Reconstruct the advertise-time decision chain for one tool.

    `_snap` is an advertise() result already computed for these exact
    projection arguments. It is an internal optimisation for `explain_model`,
    which would otherwise recompute the identical whole-registry snapshot once
    per tool - O(n^2) over a registry where advertise() is the expensive call.
    Passing a snapshot built with different arguments would silently produce a
    chain that does not match the projection, so it stays private.
    """
    man = registry.get(tool_id)
    disabled_set = set(disabled or ())
    prof = profile if profile is not None else getattr(registry, "profile", None)
    snap = _snap if _snap is not None else registry.advertise(
        profile=profile, tier=tier, read_only=read_only,
        disabled=disabled or (), include_internal=False)
    advertised = man.id in set(snap.names)
    steps: list[Step] = []

    # 1 -------------------------------------------------------------- config
    if man.id in disabled_set:
        steps.append(Step("tools.disable", FAIL,
                          "disabled by configuration",
                          fix=f"remove {man.id!r} from tools.disable"))
        # short-circuit: the registry stops here too
        return Chain(man.id, advertised, steps)
    steps.append(Step("tools.disable", PASS, "not disabled by configuration"))

    # 2 ------------------------------------------------------------ exposure
    if not man.advertised:
        steps.append(Step("manifest.advertised", NOTE,
                          man.hidden_reason or "the manifest opts out of advertisement",
                          fix="set advertised = true, or list the id under tools.enable"))
    else:
        steps.append(Step("manifest.advertised", PASS, "the manifest opts in"))

    # 3 ------------------------------------------------------------ read-only
    if read_only and man.is_mutating:
        steps.append(Step("policy.read_only", FAIL,
                          "policy.read_only is on and this tool mutates",
                          evidence=[f"risk={man.risk}",
                                    f"destructive={man.destructive}"],
                          fix="turn policy.read_only off to expose mutating tools"))
    elif read_only:
        steps.append(Step("policy.read_only", PASS, "read-only host, and this tool reads"))
    else:
        steps.append(Step("policy.read_only", SKIP, "policy.read_only is off"))

    # 4 ------------------------------------------------------------ platform
    host_os = getattr(prof, "os", "") or ""
    if man.platforms and prof and host_os not in man.platforms:
        steps.append(Step("manifest.platforms", FAIL,
                          f"supports {', '.join(man.platforms)}; this host is {host_os}",
                          evidence=[f"host os={host_os}"],
                          fix=f"run it on {' or '.join(man.platforms)}"))
    elif man.platforms:
        steps.append(Step("manifest.platforms", PASS,
                          f"{host_os} is in {', '.join(man.platforms)}"))
    else:
        steps.append(Step("manifest.platforms", SKIP, "no platform restriction"))

    # 5 -------------------------------------------------------- requirements
    if man.requirements and prof:
        ok, unmet = prof.meets(man.requirements)
        wanted = [_req_text(r) for r in man.requirements]
        if ok:
            steps.append(Step("requirements", PASS,
                              f"all {len(wanted)} requirement(s) met", evidence=wanted))
        else:
            steps.append(Step("requirements", FAIL,
                              "a required capability is missing here",
                              evidence=unmet,
                              fix="install the missing dependency, or use another provider"))
    elif man.requirements:
        steps.append(Step("requirements", NOTE, "no profile attached; requirements unchecked",
                          evidence=[_req_text(r) for r in man.requirements]))
    else:
        steps.append(Step("requirements", SKIP, "no hard requirements"))

    # 6 --------------------------------------------------------- require_any
    if man.require_any and prof:
        ok, notes = prof.meets_any(man.require_any)
        wanted = [_req_text(r) for r in man.require_any]
        if ok:
            steps.append(Step("require_any", PASS,
                              "at least one alternative is satisfied", evidence=wanted))
        else:
            steps.append(Step("require_any", FAIL,
                              "none of the alternatives are available here",
                              evidence=notes or wanted,
                              fix=f"provide any one of: {', '.join(wanted)}"))
    elif man.require_any:
        steps.append(Step("require_any", NOTE, "no profile attached; alternatives unchecked",
                          evidence=[_req_text(r) for r in man.require_any]))
    else:
        steps.append(Step("require_any", SKIP, "no alternative requirements"))

    # 7 ----------------------------------------------------------------- tier
    from skeletonkey.core.registry import TIER_ORDER

    active = snap.tier
    if TIER_ORDER.get(man.tier, 2) > TIER_ORDER.get(active, 2):
        steps.append(Step("discovery.tier", FAIL,
                          f"this tool is {man.tier}-tier and the host is showing {active}",
                          evidence=[f"tool tier={man.tier}", f"active tier={active}"],
                          fix=f"expand to the {man.tier} tier"))
    else:
        steps.append(Step("discovery.tier", PASS,
                          f"{man.tier}-tier shows in the {active} tier"))

    # 8 ------------------------------------------------------ internal, enforced
    if not man.advertised:
        steps.append(Step("internal", FAIL,
                          man.hidden_reason or "engine-only tool: registered but not listed",
                          fix="set advertised = true in the manifest"))
    else:
        steps.append(Step("internal", PASS, "listed to hosts"))

    # 9 ------------------------------------------------------------- provider
    cap = man.capability or man.id
    winner = snap.selected.get(cap)
    if winner and winner != man.id:
        receipt = snap.selection_receipts.get(cap, {})
        steps.append(Step("provider.dedupe", FAIL,
                          f"{winner} won the {cap!r} provider contest",
                          evidence=[f"winner={winner}",
                                    f"why={receipt.get('why', 'higher rank')}"],
                          fix=f"raise this tool's priority above {winner}"))
    elif winner:
        steps.append(Step("provider.dedupe", PASS,
                          f"won the {cap!r} provider contest"))
    else:
        steps.append(Step("provider.dedupe", SKIP, "no provider contest for this capability"))

    # 10 --------------------------------------------------------------- budget
    drop = snap.budget_drops.get(man.id)
    if drop:
        steps.append(Step("advertise.budget", FAIL, f"trimmed: {drop}",
                          evidence=[f"token cost={man.tokens_estimate()}"],
                          fix="raise the advertisement budget, or narrow the tier"))
    else:
        steps.append(Step("advertise.budget", PASS, "within the advertisement budget"))

    return Chain(man.id, advertised, steps)


def explain_model(registry: Any, **kwargs: Any) -> dict[str, Chain]:
    """A chain for every registered tool, keyed by id.

    The advertise() snapshot is computed once and shared: it is a pure
    function of the projection arguments, identical for every tool, and by far
    the most expensive part of an explain.
    """
    snap = registry.advertise(
        profile=kwargs.get("profile"), tier=kwargs.get("tier"),
        read_only=kwargs.get("read_only", False),
        disabled=kwargs.get("disabled") or (), include_internal=False)
    return {m.id: explain_tool(registry, m.id, _snap=snap, **kwargs)
            for m in sorted(registry.all(), key=lambda m: m.id)}


# --------------------------------------------------- hypothetical host switcher

# What a bare host of each OS looks like. Deliberately minimal: the question the
# switcher answers is "what would a *plain* box of this kind see", which is the
# useful worst case when you are deciding what to depend on.
#
# `python` is in every row on purpose - it is a first-class dialect on every
# target (README, docs/SHELL-DIALECTS.md), so dropping it when switching OS
# would fake a failure that no real host has.
_OS_SHELLS = {
    "windows": ("pwsh", "powershell", "python"),
    "linux": ("bash", "sh", "python"),
    "darwin": ("bash", "zsh", "sh", "python"),
}


def hypothetical_profile(base: Any, os_name: str) -> Any:
    """A copy of `base` rewritten as though it were running on `os_name`.

    Pure: the input profile is never mutated. Shells and OS-shaped capabilities
    are swapped for the target's, and the fingerprint is marked hypothetical so
    nothing downstream can mistake this for a real probe.
    """
    prof = copy.deepcopy(base)
    prof.os = os_name
    prof.fingerprint = f"hypothetical:{os_name}"
    prof.probe_receipt = []
    prof.warnings = [*getattr(prof, "warnings", []),
                     f"hypothetical {os_name} profile: derived, not probed"]

    wanted = _OS_SHELLS.get(os_name, ())
    prof.shells = {d: s for d, s in getattr(base, "shells", {}).items() if d in wanted}
    caps = {c for c in getattr(base, "capabilities", set())
            if not c.startswith("shell.")}
    if os_name == "windows":
        caps.add("shell.powershell")
        prof.filesystem = {**getattr(prof, "filesystem", {}), "symlinks": False,
                           "case_sensitive": False}
    else:
        caps.add("shell.unix")
        prof.filesystem = {**getattr(prof, "filesystem", {}), "symlinks": True,
                           "case_sensitive": os_name == "linux"}
    prof.capabilities = caps
    return prof


def project(registry: Any, *, os_name: str | None = None, tier: str | None = None,
            read_only: bool = False, base_profile: Any = None) -> Any:
    """Build the model a *hypothetical* host would see. Never re-probes."""
    from .model import build_viz_model

    base = base_profile if base_profile is not None else getattr(registry, "profile", None)
    prof = hypothetical_profile(base, os_name) if (os_name and base is not None) else base
    model = build_viz_model(registry, profile=prof, tier=tier, read_only=read_only)
    if os_name:
        model.host["hypothetical"] = True
        model.host["os"] = os_name
    return model


# ------------------------------------------------------------------- diffing

def diff_models(before: Any, after: Any) -> dict[str, Any]:
    """What changed between two models: tools, exposure, and cost.

    Works on `VizModel` objects or on the JSON dicts they serialise to, so a
    saved `sk viz --json` from another host or another commit can be compared
    against this one without a live registry.
    """
    a = _nodes_of(before)
    b = _nodes_of(after)

    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed: list[dict[str, Any]] = []
    exposure: list[dict[str, Any]] = []

    watched = ("risk", "tier", "capability", "version", "destructive", "reversible",
               "token_cost", "typical_latency_ms", "group", "provider", "approval")
    for tool_id in sorted(set(a) & set(b)):
        old, new = a[tool_id], b[tool_id]
        deltas = {k: {"from": old.get(k), "to": new.get(k)}
                  for k in watched if old.get(k) != new.get(k)}
        if deltas:
            changed.append({"id": tool_id, "changes": deltas})
        if old.get("advertised") != new.get("advertised"):
            exposure.append({"id": tool_id,
                             "from": bool(old.get("advertised")),
                             "to": bool(new.get("advertised")),
                             "why": new.get("withheld_by") or "now advertised"})

    def tokens(nodes: dict[str, dict[str, Any]]) -> int:
        return sum(n.get("token_cost", 0) for n in nodes.values() if n.get("advertised"))

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "exposure": exposure,
        "same": not (added or removed or changed or exposure),
        "counts": {"added": len(added), "removed": len(removed),
                   "changed": len(changed), "exposure": len(exposure)},
        "tokens": {"from": tokens(a), "to": tokens(b),
                   "delta": tokens(b) - tokens(a)},
        "digest": {"from": _digest_of(before), "to": _digest_of(after)},
    }


def _nodes_of(model: Any) -> dict[str, dict[str, Any]]:
    if isinstance(model, dict):
        return {n["id"]: n for n in model.get("nodes", [])}
    return {n.id: n.to_json() for n in model.nodes}


def _digest_of(model: Any) -> str:
    if isinstance(model, dict):
        return model.get("model_digest", "")
    return model.digest()
