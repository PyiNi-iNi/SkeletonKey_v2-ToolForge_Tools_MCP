"""The visualizer's data model: one JSON document describing the whole toolset.

Design rules (from `docs/TOOL-VISUALIZER-PLAN.md` §1, enforced by tests):

1. **Read-only projection.** Building a model calls `registry.all()` and
   `registry.advertise()` and nothing else. No handler runs, no state changes.
2. **Everything registered, not just everything advertised.** The interesting
   tools are the withheld ones, so every node carries `advertised` plus the
   gate receipt that decided it.
3. **Secrets never render.** Input schemas pass through the same redaction the
   external exporters use (`core.export`), and `secret_args` properties are
   dropped entirely - a visualizer is an export surface.
4. **Deterministic.** Same registry state ⇒ byte-identical JSON ⇒ same
   `model_digest`. Every collection is sorted; nothing carries a timestamp
   unless the caller asks for one.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from skeletonkey.core.export import _clean_input_schema, _redact_schema
from skeletonkey.core.manifest import ToolManifest
from skeletonkey.core.util import short_hash, stable_json

SCHEMA_VERSION = 1

# The four edge kinds V3 will draw. Named here because the schema, the tests and
# the eventual UI toggles must agree on the vocabulary.
EDGE_KINDS = ("see_also", "same_capability", "provides", "anti_pattern")


@dataclass
class VizNode:
    """One tool, flattened for display."""

    id: str
    group: str
    title: str
    summary: str                       # first sentence of the description
    description: str
    capability: str
    provides: list[str] = field(default_factory=list)
    provider: str | None = None
    priority: int = 50
    tier: str = "full"
    risk: str = "read"
    version: str = "1"
    source: str = "builtin"

    # behaviour flags an operator reads before calling anything
    destructive: bool = False
    reversible: bool = False
    idempotent: bool = True
    parallel_safe: bool = True
    open_world: bool = False
    mutating: bool = False
    stateful: str = "none"
    approval: str = "policy"

    # cost hints (the planner's, and the budget's, view of this tool)
    typical_latency_ms: int = 0
    typical_output_bytes: int = 0
    timeout_s: float = 60.0
    token_cost: int = 0

    # host applicability
    platforms: list[str] = field(default_factory=list)
    requires: list[dict[str, Any]] = field(default_factory=list)
    requires_any: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    # exposure, and the receipt behind it
    advertised: bool = False           # advertised *by this host, right now*
    advertisable: bool = True          # the manifest's own `advertised` flag
    hidden_reason: str = ""
    gate: dict[str, Any] = field(default_factory=dict)
    withheld_by: str = ""              # one-word cause: gate | tier | internal | budget | ""
    budget_drop: str = ""

    # docs
    example_count: int = 0
    examples: list[dict[str, Any]] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    see_also: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None

    # V5 overlay: measured behaviour from the ledger. Empty unless
    # `runtime.attach_runtime()` filled it in, and omitted from the JSON
    # entirely when empty, so an overlay-free model serialises exactly as it
    # did before V5 existed.
    runtime: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("output_schema") is None:
            d.pop("output_schema")
        if not d.get("runtime"):
            d.pop("runtime", None)
        return d


@dataclass
class VizEdge:
    """A typed relation between two tools. `kind` is one of EDGE_KINDS."""

    source: str
    target: str
    kind: str
    label: str = ""

    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.source, self.target)

    def to_json(self) -> dict[str, Any]:
        d = {"source": self.source, "target": self.target, "kind": self.kind}
        if self.label:
            d["label"] = self.label
        return d


@dataclass
class VizModel:
    """The whole toolset, ready to render."""

    nodes: list[VizNode]
    edges: list[VizEdge]
    groups: list[dict[str, Any]]
    capabilities: list[dict[str, Any]]
    aggregates: dict[str, Any]
    snapshot: dict[str, Any]
    host: dict[str, Any]
    schema_version: int = SCHEMA_VERSION
    # V5: ledger-derived summary (drift list, feed, busiest/slowest). Empty
    # unless an overlay was attached.
    runtime: dict[str, Any] = field(default_factory=dict)

    # convenience -------------------------------------------------------
    def node(self, tool_id: str) -> VizNode | None:
        for n in self.nodes:
            if n.id == tool_id:
                return n
        return None

    def edges_of(self, kind: str) -> list[VizEdge]:
        return [e for e in self.edges if e.kind == kind]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "snapshot": self.snapshot,
            "host": self.host,
            "aggregates": self.aggregates,
            "groups": self.groups,
            "capabilities": self.capabilities,
            "nodes": [n.to_json() for n in self.nodes],
            "edges": [e.to_json() for e in self.edges],
            **({"runtime": self.runtime} if self.runtime else {}),
        }

    def digest(self) -> str:
        return model_digest(self)


def summary_lines(model: VizModel) -> str:
    """A compact, diff-reviewable rendering of the toolset.

    The page is ~300 KB of HTML: excellent to look at, useless in a pull
    request. This is the artifact meant for review - one sorted line per tool
    with the facts that matter when a change lands, so `git diff` shows
    *exactly* what moved: a tool added, a risk class changed, a tool that
    stopped being advertised, a token cost that jumped.
    """
    agg = model.aggregates
    out = [
        "# SkeletonKey toolset",
        "#",
        "# Generated by `sk viz --summary`. One line per REGISTERED tool:",
        "#   <id>  <tier>  <risk>  <tokens>tok  <exposure>",
        "# Regenerate and commit this when the toolset changes.",
        "#",
        f"registered      {agg['registered']}",
        f"advertised      {agg['advertised']}",
        f"withheld        {agg['withheld']}",
        f"groups          {agg['groups']}",
        f"capabilities    {agg['capabilities']}",
        f"destructive     {agg['destructive']}",
        f"tokens          {model.snapshot['tokens']}",
        f"tier            {model.snapshot['tier']}",
        "",
    ]
    for node in sorted(model.nodes, key=lambda n: n.id):
        exposure = "advertised" if node.advertised else f"withheld:{node.withheld_by}"
        flags = "".join([
            "D" if node.destructive else "-",
            "R" if node.reversible else "-",
            "I" if node.idempotent else "-",
            "P" if node.parallel_safe else "-",
        ])
        out.append(f"{node.id:34} {node.tier:5} {node.risk:11} "
                   f"{node.token_cost:5}tok {flags} {exposure}")
    return "\n".join(out) + "\n"


def model_digest(model: VizModel) -> str:
    """Content hash of the model, ignoring the advertise-time clock.

    Two models with the same digest are the same document; that is what lets a
    served page detect drift (V2) and a diff view detect real change (V4).
    """
    payload = model.to_json()
    payload = {k: v for k, v in payload.items() if k != "snapshot"}
    payload["snapshot"] = {k: v for k, v in model.snapshot.items() if k != "at"}
    return short_hash(stable_json(payload), 16)


# --------------------------------------------------------------------- helpers

def _summary(man: ToolManifest) -> str:
    """First sentence of the description - what the card shows."""
    desc = (man.description or "").strip()
    if not desc:
        return man.title or man.id
    line = desc.split("\n", 1)[0].strip()
    idx = line.find(". ")
    if idx > 0:
        return line[: idx + 1]
    return line


def _safe_schema(man: ToolManifest) -> dict[str, Any]:
    """Host-facing input schema with credential-shaped defaults and secret args gone."""
    return _clean_input_schema(man)


def _safe_examples(man: ToolManifest) -> list[dict[str, Any]]:
    """Examples with secret args stripped and credential-shaped values redacted."""
    out: list[dict[str, Any]] = []
    secrets = set(man.secret_args or [])
    for ex in man.examples or []:
        if not isinstance(ex, dict):
            continue
        clean = _redact_schema(ex)
        args = clean.get("args")
        if isinstance(args, dict) and secrets:
            clean["args"] = {k: v for k, v in args.items() if k not in secrets}
        out.append(clean)
    return out


def _withheld_cause(man: ToolManifest, gate: Any, *, advertised: bool,
                    budget_drop: str) -> str:
    """One word for *why* a registered tool is not on this host's list."""
    if advertised:
        return ""
    if budget_drop:
        return "budget"
    if gate is not None and not getattr(gate, "available", True):
        reasons = list(getattr(gate, "reasons", []) or [])
        if any(r.startswith("tier ") for r in reasons):
            return "tier"
        return "gate"
    if not man.advertised:
        return "internal"
    return "unknown"


def _build_edges(nodes: list[VizNode], by_cap: dict[str, list[VizNode]]) -> list[VizEdge]:
    known = {n.id for n in nodes}
    edges: dict[tuple[str, str, str], VizEdge] = {}

    def add(edge: VizEdge) -> None:
        edges.setdefault(edge.key(), edge)

    for node in nodes:
        # 1. curated "next tool to reach for" links, straight from the manifest
        for target in node.see_also:
            if target in known and target != node.id:
                add(VizEdge(node.id, target, "see_also"))

        # 2. anti-patterns that name another tool ("use fs.patch instead")
        for text in node.anti_patterns:
            for other in known:
                if other != node.id and other in text:
                    add(VizEdge(node.id, other, "anti_pattern", label=text[:120]))

        # 3. provides: this tool declares it can satisfy someone else's capability
        for cap in node.provides:
            for peer in by_cap.get(cap, []):
                if peer.id != node.id:
                    add(VizEdge(node.id, peer.id, "provides", label=cap))

    # 4. same_capability: the provider contest, one undirected pair per bucket,
    #    emitted low-id -> high-id so the set is stable regardless of iteration.
    for cap, members in by_cap.items():
        if len(members) < 2:
            continue
        ids = sorted(m.id for m in members)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                add(VizEdge(a, b, "same_capability", label=cap))

    return sorted(edges.values(), key=lambda e: (e.kind, e.source, e.target))


def _histogram(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


# ----------------------------------------------------------------------- build

def build_viz_model(
    registry: Any,
    *,
    profile: Any = None,
    tier: str | None = None,
    read_only: bool = False,
    disabled: list[str] | None = None,
    include_schemas: bool = True,
    include_examples: bool = True,
) -> VizModel:
    """Fold the registry into a renderable model.

    `registry` is a `core.registry.Registry` (or anything with the same
    `all()` / `advertise()` surface). The advertise call is made with
    `include_internal=False`, i.e. exactly the list a host would get, so
    `node.advertised` means "this host would show you this tool".
    """
    manifests = sorted(registry.all(), key=lambda m: m.id)
    snap = registry.advertise(
        profile=profile,
        tier=tier,
        read_only=read_only,
        disabled=disabled or (),
        include_internal=False,
    )
    advertised_ids = set(snap.names)

    nodes: list[VizNode] = []
    for man in manifests:
        gate = snap.gates.get(man.id)
        is_adv = man.id in advertised_ids
        budget_drop = snap.budget_drops.get(man.id, "")
        node = VizNode(
            id=man.id,
            group=man.group,
            title=man.title,
            summary=_summary(man),
            description=(man.description or "").strip(),
            capability=man.capability or man.id,
            provides=sorted(man.provides or []),
            provider=man.provider,
            priority=man.priority,
            tier=man.tier,
            risk=man.risk,
            version=man.version,
            source=man.source,
            destructive=bool(man.destructive),
            reversible=bool(man.reversible),
            idempotent=bool(man.idempotent),
            parallel_safe=bool(man.parallel_safe),
            open_world=bool(man.open_world),
            mutating=bool(man.is_mutating),
            stateful=man.stateful,
            approval=man.approval,
            typical_latency_ms=man.typical_latency_ms,
            typical_output_bytes=man.typical_output_bytes,
            timeout_s=float(man.timeout_s),
            token_cost=man.tokens_estimate(),
            platforms=sorted(man.platforms or []),
            requires=[r.to_dict() for r in (man.requirements or [])],
            requires_any=[r.to_dict() for r in (man.require_any or [])],
            tags=sorted(man.tags or []),
            advertised=is_adv,
            advertisable=bool(man.advertised),
            hidden_reason=man.hidden_reason or "",
            gate=gate.to_dict() if gate is not None else {},
            withheld_by=_withheld_cause(man, gate, advertised=is_adv, budget_drop=budget_drop),
            budget_drop=budget_drop,
            example_count=len(man.examples or []),
            examples=_safe_examples(man) if include_examples else [],
            anti_patterns=list(man.anti_patterns or []),
            see_also=sorted(man.see_also or []),
            input_schema=_safe_schema(man) if include_schemas else {},
            output_schema=(_redact_schema(man.output_schema)
                           if include_schemas and man.output_schema else None),
        )
        nodes.append(node)

    by_cap: dict[str, list[VizNode]] = {}
    for node in nodes:
        by_cap.setdefault(node.capability, []).append(node)

    edges = _build_edges(nodes, by_cap)

    groups: list[dict[str, Any]] = []
    for name in sorted({n.group for n in nodes}):
        members = [n for n in nodes if n.group == name]
        groups.append({
            "name": name,
            "tools": len(members),
            "advertised": sum(1 for n in members if n.advertised),
            "token_cost": sum(n.token_cost for n in members if n.advertised),
            "risk": _histogram([n.risk for n in members]),
        })

    capabilities: list[dict[str, Any]] = []
    for cap in sorted(by_cap):
        members = by_cap[cap]
        providers = sorted({m.provider for m in members if m.provider})
        capabilities.append({
            "capability": cap,
            "tools": sorted(m.id for m in members),
            "providers": providers,
            "contested": len(members) > 1 and len(providers) > 1,
            "winner": snap.selected.get(cap),
            "receipt": snap.selection_receipts.get(cap, {}),
        })

    adv = [n for n in nodes if n.advertised]
    withheld = [n for n in nodes if not n.advertised]
    aggregates = {
        "registered": len(nodes),
        "advertised": len(adv),
        "withheld": len(withheld),
        "withheld_by": _histogram([n.withheld_by for n in withheld if n.withheld_by]),
        "tokens": snap.tokens,
        "token_cost_registered": sum(n.token_cost for n in nodes),
        "groups": len(groups),
        "capabilities": len(capabilities),
        "contested_capabilities": sum(1 for c in capabilities if c["contested"]),
        "risk": _histogram([n.risk for n in nodes]),
        "tiers": _histogram([n.tier for n in nodes]),
        "sources": _histogram([n.source for n in nodes]),
        "destructive": sum(1 for n in nodes if n.destructive),
        "mutating": sum(1 for n in nodes if n.mutating),
        "with_examples": sum(1 for n in nodes if n.example_count),
        "examples": sum(n.example_count for n in nodes),
        "edges": _histogram([e.kind for e in edges]),
    }

    prof = profile if profile is not None else getattr(registry, "profile", None)
    host = {
        "os": getattr(prof, "os", "") or "",
        "arch": getattr(prof, "arch", "") or "",
        "python_version": getattr(prof, "python_version", "") or "",
        "fingerprint": getattr(prof, "fingerprint", "") or "",
        "capabilities": sorted(getattr(prof, "capabilities", set()) or set()),
        "read_only": bool(read_only),
    }

    snapshot = {
        "digest": snap.digest,
        "tier": snap.tier,
        "tokens": snap.tokens,
        "count": len(snap.tools),
        "selected": dict(sorted(snap.selected.items())),
        "budget_drops": dict(sorted(snap.budget_drops.items())),
        "at": snap.at,
    }

    return VizModel(nodes=nodes, edges=edges, groups=groups, capabilities=capabilities,
                    aggregates=aggregates, snapshot=snapshot, host=host)
