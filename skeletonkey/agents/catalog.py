"""The small, reviewable catalog of agent purposes.

A profile intentionally contains no tool objects and no handlers.  Its
selectors classify the manifests already present in the registry.  This keeps
ADR-0016's single source of truth intact while still giving an operator useful
roles to delegate to.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from skeletonkey.core.manifest import ToolManifest
from skeletonkey.core.subagent import SubagentSpec


@dataclass(frozen=True)
class AgentAction:
    """A purpose-level action shown in the UI and used to guide delegation."""

    id: str
    title: str
    objective: str
    capabilities: tuple[str, ...] = ()
    confirmation: str = "policy"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "objective": self.objective,
            "capabilities": list(self.capabilities),
            "confirmation": self.confirmation,
        }


@dataclass(frozen=True)
class AgentProfile:
    """A bounded autonomous role.

    ``groups`` and ``id_prefixes`` are selectors over live ToolManifest data;
    they are not a copy of the tool registry.  A profile may overlap another
    profile: the same tool can be useful to an analyst and a maintainer, while
    each agent still receives its own restricted allowlist at runtime.
    """

    id: str
    title: str
    purpose: str
    mission: str
    groups: tuple[str, ...] = ()
    id_prefixes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    tier: str = "task"
    read_only: bool = True
    max_calls: int = 50
    max_wall_time_s: float = 120.0
    max_output_bytes: int = 8_000
    actions: tuple[AgentAction, ...] = ()
    risk_note: str = ""

    def matches(self, manifest: ToolManifest) -> bool:
        """Return whether this purpose owns the manifest's tool family."""
        return bool(
            (self.groups and manifest.group in self.groups)
            or (self.id_prefixes and any(
                manifest.id == prefix.rstrip(".") or manifest.id.startswith(prefix)
                for prefix in self.id_prefixes))
            or (self.capabilities and manifest.capability in self.capabilities)
        )

    def subagent_spec(self, tool_ids: Iterable[str]) -> SubagentSpec:
        """Build the enforcement boundary used by the real engine."""
        return SubagentSpec(
            name=self.title,
            tier=self.tier,  # type: ignore[arg-type]
            read_only=self.read_only,
            max_calls=self.max_calls,
            max_wall_time_s=self.max_wall_time_s,
            max_output_bytes=self.max_output_bytes,
            allowed_tools=sorted(set(tool_ids)),
            tags=["purpose-agent", self.id],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "purpose": self.purpose,
            "mission": self.mission,
            "groups": list(self.groups),
            "id_prefixes": list(self.id_prefixes),
            "capabilities": list(self.capabilities),
            "tier": self.tier,
            "read_only": self.read_only,
            "max_calls": self.max_calls,
            "max_wall_time_s": self.max_wall_time_s,
            "max_output_bytes": self.max_output_bytes,
            "actions": [a.to_dict() for a in self.actions],
            "risk_note": self.risk_note,
        }


# The selector surface is deliberately grouped by the repository's declared
# tool families.  The final coverage test in tests/test_agents.py makes adding
# a new group an explicit design decision rather than silently orphaning it.
AGENT_PROFILES: tuple[AgentProfile, ...] = (
    AgentProfile(
        id="workspace-analyst",
        title="Workspace Analyst",
        purpose="Understand the workspace and explain what can safely happen next.",
        mission="Inspect files, search symbols, audit the registry, and produce evidence before a change.",
        groups=("fs", "registry", "capabilities", "profile"),
        tier="task",
        read_only=True,
        actions=(
            AgentAction("inspect", "Inspect", "Read the relevant files and host facts.", ("fs.read", "fs.list", "fs.stat")),
            AgentAction("search", "Find evidence", "Search the workspace or tool catalog for the requested subject.", ("search.text", "registry.search")),
            AgentAction("explain", "Explain access", "Show why a capability is available or withheld.", ("registry.explain", "registry.route")),
        ),
        risk_note="Writes, deletes, and permission changes remain unavailable in the analyst boundary.",
    ),
    AgentProfile(
        id="code-maintainer",
        title="Code Maintainer",
        purpose="Make bounded, reversible changes to workspace code and verify them.",
        mission="Plan an edit, patch only the requested roots, inspect the diff, and use journal undo when needed.",
        groups=("fs", "shell"),
        id_prefixes=("planner.",),
        tier="task",
        read_only=False,
        max_calls=40,
        max_wall_time_s=180.0,
        actions=(
            AgentAction("edit", "Edit", "Apply the smallest anchored patch or file operation that satisfies the task.", ("fs.edit", "fs.write"), "on_write"),
            AgentAction("verify", "Verify", "Run a quoted, bounded check and inspect its result.", ("exec.script", "shell.quote_check"), "policy"),
            AgentAction("rollback", "Rollback", "Undo this task's journal entries if verification fails.", ("fs.undo_task",), "on_write"),
        ),
        risk_note="Write and shell actions still pass through policy, approvals, roots, argv, and the ledger.",
    ),
    AgentProfile(
        id="shell-operator",
        title="Shell Operator",
        purpose="Run cross-platform commands safely and report bounded evidence.",
        mission="Choose a detected dialect, preserve literal argv boundaries, supervise jobs, and stop on unsafe conditions.",
        groups=("shell",),
        tier="task",
        read_only=False,
        max_calls=30,
        max_wall_time_s=120.0,
        actions=(
            AgentAction("prepare", "Prepare command", "Check dialect support and quote literal arguments before execution.", ("profile.shells", "shell.quote")),
            AgentAction("execute", "Execute", "Run a bounded command with a timeout and captured output.", ("exec.script",), "on_write"),
            AgentAction("supervise", "Supervise", "Observe, wait for, or stop a background job.", ("exec.jobs", "exec.sessions"), "policy"),
        ),
        risk_note="Command execution is open-world and requires the engine's normal approval policy.",
    ),
    AgentProfile(
        id="sandbox-engineer",
        title="Sandbox Engineer",
        purpose="Create isolated scratch workspaces for experiments and disposable runs.",
        mission="Provision a named sandbox, pin its runtime, execute within it, and report its inventory without escaping roots.",
        groups=("sandbox",),
        tier="full",
        read_only=False,
        max_calls=24,
        max_wall_time_s=180.0,
        actions=(
            AgentAction("provision", "Provision", "Create a disposable workspace with an explicit template and runtime.", ("sandbox.create",), "on_write"),
            AgentAction("run", "Run experiment", "Execute a bounded command with the sandbox environment on PATH.", ("sandbox.run",), "on_write"),
            AgentAction("inspect", "Inspect", "Read status, inventory, and the sandbox log.", ("sandbox.status",)),
        ),
        risk_note="A sandbox is defense against accidental damage, not a hostile-code security boundary.",
    ),
    AgentProfile(
        id="catalog-steward",
        title="Catalog Steward",
        purpose="Maintain the tool and project catalog as an accurate operational inventory.",
        mission="Compare, validate, generate, and update catalog artifacts while preserving manifest truth and auditability.",
        groups=("catalog", "planner"),
        tier="task",
        read_only=False,
        max_calls=35,
        max_wall_time_s=150.0,
        actions=(
            AgentAction("inventory", "Inventory", "List tools, integrations, projects, schemas, and distribution facts.", ("catalog.list_tools", "catalog.generate_inventory")),
            AgentAction("audit", "Audit", "Generate an audit or compare snapshots before changing anything.", ("catalog.generate_audit", "catalog.diff")),
            AgentAction("curate", "Curate", "Save or update catalog metadata after policy approval.", ("catalog.update_tool", "catalog.save"), "on_write"),
        ),
        risk_note="Catalog writes are explicit policy actions; the UI never performs them directly.",
    ),
    AgentProfile(
        id="integration-operator",
        title="Integration Operator",
        purpose="Discover, validate, and operate optional integrations without hiding their health state.",
        mission="Inspect available connectors, validate their contracts, and enable or remove them only with an explicit grant.",
        groups=("integrations",),
        tier="task",
        read_only=False,
        max_calls=30,
        max_wall_time_s=150.0,
        actions=(
            AgentAction("discover", "Discover", "Find supported CLIs, packs, and installed integrations.", ("integrations.discover", "integrations.list")),
            AgentAction("validate", "Validate", "Check configuration, health, and declared compatibility.", ("integrations.validate", "integrations.health")),
            AgentAction("operate", "Operate", "Load, enable, or install an integration after approval.", ("integrations.enable", "integrations.packs.install"), "on_write"),
        ),
        risk_note="Integrations can cross process and network boundaries; secrets are never rendered by this UI.",
    ),
    AgentProfile(
        id="live-prototyper",
        title="Live Prototyper",
        purpose="Develop and preview Python programs through the transactional live loop.",
        mission="Start a bounded live session, inspect state and frames, patch deliberately, and restore source when experiments end.",
        groups=("live",),
        tier="full",
        read_only=False,
        max_calls=24,
        max_wall_time_s=180.0,
        actions=(
            AgentAction("start", "Start session", "Load a workspace program and establish a live state snapshot.", ("live.start",), "on_write"),
            AgentAction("patch", "Patch", "Apply an in-place patch or controlled reload and inspect the frame.", ("live.patch", "live.reload", "live.render"), "on_write"),
            AgentAction("restore", "Restore", "Return experimental names to source and stop the session cleanly.", ("live.reload", "live.stop"), "on_write"),
        ),
        risk_note="Live REPL and patch actions execute Python; keep the panel loopback-bound and policy-gated.",
    ),
    AgentProfile(
        id="release-manager",
        title="Release Manager",
        purpose="Prepare, validate, and publish a release without exposing credentials.",
        mission="Scan placeholders, choose packaging and platform guidance, generate a test plan, then request approval before injection or publishing.",
        groups=("publishing",),
        tier="full",
        read_only=False,
        max_calls=28,
        max_wall_time_s=180.0,
        actions=(
            AgentAction("prepare", "Prepare release", "Read packaging, platform, and payment guidance for the target.", ("publish.knowledge.packaging", "publish.knowledge.platforms")),
            AgentAction("test", "Test release", "Generate an executable release test plan and scan for placeholders.", ("publish.testplan", "publish.scan")),
            AgentAction("publish", "Publish", "Inject stored placeholders or manage the write-only credential store only after approval.", ("publish.inject", "publish.store.write"), "on_write"),
        ),
        risk_note="Credential values are write-only to agents and are omitted from the UI, schemas, examples, and ledger previews.",
    ),
    AgentProfile(
        id="skill-curator",
        title="Skill Curator",
        purpose="Match procedural knowledge to a task and keep skill packs healthy.",
        mission="Discover the smallest useful skill context, inspect declarations, and install or remove packs only through the guarded path.",
        groups=("skills",),
        tier="task",
        read_only=False,
        max_calls=25,
        max_wall_time_s=120.0,
        actions=(
            AgentAction("match", "Match skill", "Find the most relevant procedural guidance for a task.", ("skills.match", "skills.load")),
            AgentAction("inspect", "Inspect pack", "Read the skill declaration and its synthesized tool surface.", ("skills.list", "skills.load")),
            AgentAction("manage", "Manage pack", "Install or uninstall a pack only when the operator enables that capability.", ("skills.install", "skills.uninstall"), "on_write"),
        ),
        risk_note="Skill installation is gated by configuration; a profile cannot self-grant that permission.",
    ),
    AgentProfile(
        id="safety-auditor",
        title="Safety Auditor",
        purpose="Explain capability gates, policy decisions, and the host profile before autonomy expands.",
        mission="Probe or inspect the capability surface, identify withheld tools, and produce an actionable safety report.",
        groups=("policy",),
        id_prefixes=("registry.", "capabilities.", "profile."),
        tier="core",
        read_only=True,
        max_calls=30,
        max_wall_time_s=90.0,
        actions=(
            AgentAction("profile", "Profile host", "Read the host capability and shell receipts.", ("profile.capabilities", "profile.shells")),
            AgentAction("audit", "Audit gates", "Explain every advertisement decision and risk boundary.", ("registry.explain", "registry.stats")),
            AgentAction("report", "Report", "Route a task to the least-risk provider without executing it.", ("registry.route",), "never"),
        ),
        risk_note="The auditor is intentionally read-only; policy.grant is visible as a gated capability, never auto-approved.",
    ),
)


def get_agent_profile(agent_id: str) -> AgentProfile:
    """Return a named profile with a useful error for UI/API callers."""
    for profile in AGENT_PROFILES:
        if profile.id == agent_id:
            return profile
    raise KeyError(f"unknown purpose agent {agent_id!r}")


def risk_ceiling(profile: AgentProfile) -> str:
    """Human-facing summary of the highest risk a profile may request."""
    return "read" if profile.read_only else "policy-gated writes"


__all__ = ["AGENT_PROFILES", "AgentAction", "AgentProfile", "get_agent_profile", "risk_ceiling"]
