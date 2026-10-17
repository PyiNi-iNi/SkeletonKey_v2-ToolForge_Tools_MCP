"""The purpose-agent projection over the live visualizer model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from skeletonkey.core.util import short_hash, stable_json
from skeletonkey.viz.model import VizNode, build_viz_model

from .catalog import AGENT_PROFILES, AgentProfile

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AgentTool:
    """A tool assignment with the agent's effective availability."""

    id: str
    title: str
    group: str
    capability: str
    risk: str
    tier: str
    advertised: bool
    status: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "group": self.group,
            "capability": self.capability,
            "risk": self.risk,
            "tier": self.tier,
            "advertised": self.advertised,
            "status": self.status,
            **({"reason": self.reason} if self.reason else {}),
        }


@dataclass
class PurposeAgent:
    """One profile plus its assignments from the current registry."""

    id: str
    title: str
    purpose: str
    mission: str
    tier: str
    read_only: bool
    max_calls: int
    max_wall_time_s: float
    max_output_bytes: int
    risk_note: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    tools: list[AgentTool] = field(default_factory=list)

    @property
    def available_tools(self) -> list[AgentTool]:
        return [t for t in self.tools if t.status == "ready"]

    @property
    def gated_tools(self) -> list[AgentTool]:
        return [t for t in self.tools if t.status != "ready"]

    @property
    def groups(self) -> list[str]:
        return sorted({t.group for t in self.tools})

    @property
    def risk(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for tool in self.tools:
            out[tool.risk] = out.get(tool.risk, 0) + 1
        return dict(sorted(out.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "purpose": self.purpose,
            "mission": self.mission,
            "tier": self.tier,
            "read_only": self.read_only,
            "limits": {
                "max_calls": self.max_calls,
                "max_wall_time_s": self.max_wall_time_s,
                "max_output_bytes": self.max_output_bytes,
            },
            "risk_note": self.risk_note,
            "actions": self.actions,
            "groups": self.groups,
            "tool_count": len(self.tools),
            "ready_count": len(self.available_tools),
            "gated_count": len(self.gated_tools),
            "risk": self.risk,
            "tools": [t.to_dict() for t in self.tools],
        }


@dataclass
class AgentModel:
    """The complete UI/API document.

    ``tool_digest`` proves which registry projection this directory came from.
    It is deliberately not a second source of tool facts: every assignment is
    copied from a ``VizNode`` produced by ``build_viz_model``.
    """

    agents: list[PurposeAgent]
    tool_digest: str
    tool_count: int
    assigned_tool_count: int
    unassigned_tools: list[str]
    host: dict[str, Any]
    schema_version: int = SCHEMA_VERSION

    @property
    def assigned_once(self) -> bool:
        return self.assigned_tool_count == self.tool_count and not self.unassigned_tools

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool_digest": self.tool_digest,
            "tool_count": self.tool_count,
            "assigned_tool_count": self.assigned_tool_count,
            "unassigned_tools": list(self.unassigned_tools),
            "host": dict(self.host),
            "agents": [a.to_dict() for a in self.agents],
        }

    def digest(self) -> str:
        return short_hash(stable_json(self.to_json()), 16)


def _node_status(profile: AgentProfile, node: VizNode, *, host_read_only: bool) -> tuple[str, str]:
    """Apply the same visible constraints the subagent boundary will enforce."""
    if not node.advertised:
        return "withheld", node.withheld_by or "the host did not advertise this tool"
    tier_order = {"core": 0, "task": 1, "full": 2}
    if tier_order.get(node.tier, 2) > tier_order.get(profile.tier, 1):
        return "tier", f"tool tier {node.tier} exceeds agent tier {profile.tier}"
    if host_read_only and node.mutating:
        return "host-read-only", "the current host is read-only"
    if profile.read_only and node.mutating:
        return "read-only", "this purpose agent is read-only"
    if node.risk in ("destructive", "privileged"):
        return "approval", f"{node.risk} actions require an explicit policy decision"
    return "ready", ""


def _agent_from_nodes(profile: AgentProfile, nodes: list[VizNode], *, host_read_only: bool) -> PurposeAgent:
    assignments: list[AgentTool] = []
    for node in nodes:
        if not profile.matches(_manifest_like(node)):
            continue
        status, reason = _node_status(profile, node, host_read_only=host_read_only)
        assignments.append(AgentTool(
            id=node.id,
            title=node.title,
            group=node.group,
            capability=node.capability,
            risk=node.risk if node.risk_declared else "undeclared",
            tier=node.tier,
            advertised=node.advertised,
            status=status,
            reason=reason,
        ))
    assignments.sort(key=lambda t: t.id)
    return PurposeAgent(
        id=profile.id,
        title=profile.title,
        purpose=profile.purpose,
        mission=profile.mission,
        tier=profile.tier,
        read_only=profile.read_only,
        max_calls=profile.max_calls,
        max_wall_time_s=profile.max_wall_time_s,
        max_output_bytes=profile.max_output_bytes,
        risk_note=profile.risk_note,
        actions=[a.to_dict() for a in profile.actions],
        tools=assignments,
    )


def _manifest_like(node: VizNode) -> Any:
    """Create the tiny selector view needed by ``AgentProfile.matches``.

    Keeping this adapter local avoids reconstructing a ToolManifest (and
    accidentally carrying a handler or a secret schema into this projection).
    """

    class SelectorView:
        group = node.group
        id = node.id
        capability = node.capability

    return SelectorView()


def build_agent_model(
    registry: Any,
    *,
    tier: str | None = None,
    read_only: bool = False,
    profiles: tuple[AgentProfile, ...] = AGENT_PROFILES,
) -> AgentModel:
    """Build the purpose UI from the same registry projection as ``sk viz``."""
    viz = build_viz_model(registry, tier=tier, read_only=read_only)
    agents = [_agent_from_nodes(p, viz.nodes, host_read_only=read_only) for p in profiles]
    assigned = {tool.id for agent in agents for tool in agent.tools}
    # A future group must be deliberately assigned.  The UI still renders an
    # unassigned section instead of inventing a purpose for an unknown tool.
    unassigned = sorted(n.id for n in viz.nodes if n.id not in assigned)
    return AgentModel(
        agents=agents,
        tool_digest=viz.digest(),
        tool_count=len(viz.nodes),
        assigned_tool_count=len(assigned),
        unassigned_tools=unassigned,
        host=dict(viz.host),
    )


__all__ = ["AgentModel", "AgentTool", "PurposeAgent", "build_agent_model"]
