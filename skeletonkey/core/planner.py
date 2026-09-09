"""Read-only execution planner with DAG ordering (P7 exploratory).

Uses ``parallel_safe`` and ``stateful`` metadata from ToolManifest to
build a dependency-ordered execution plan. The planner does NOT execute
anything; it produces a plan that an autopilot loop can verify before
dispatching.

Design principles:
  * Read-only plans are safe to inspect, serialize, and diff.
  * Conflicting mutations (two writes to the same resource) are
    flagged rather than applied last-writer-wins.
  * ``parallel_safe=False`` tools are serialized relative to any
    other tool touching the same resource group.
  * ``stateful="host"`` tools are annotated so the caller knows the
    plan has ordering constraints that survive across calls.

This is an exploratory seam, not a full DAG executor. It provides the
``plan()`` entry point and the ``Plan`` dataclass for inspection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.core.manifest import ToolManifest


@dataclass
class PlanStep:
    """One step in the execution plan.

    Attributes
    ----------
    index:
        Sequential position (0-based) within the plan.
    tool_id:
        The manifest id of the tool to call.
    args:
        The arguments for the call.
    depends_on:
        Indices of steps that must complete before this one.
    parallel_group:
        Steps sharing the same group id may run concurrently.
    risk:
        The manifest's risk classification.
    stateful:
        The manifest's statefulness annotation.
    parallel_safe:
        Whether this step may run concurrently with unrelated steps.
    resource_group:
        An opaque group id for conflict detection (e.g. a file path,
        a database name, an API endpoint prefix).
    """

    index: int
    tool_id: str
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)
    parallel_group: str = ""
    risk: str = "read"
    stateful: str = "none"
    parallel_safe: bool = True
    resource_group: str = ""


@dataclass
class PlanConflict:
    """A detected conflict between two plan steps.

    The planner does not silently resolve these; it reports them so
    the caller can decide (refuse, reorder, or require human approval).
    """

    step_a: int
    step_b: int
    kind: str  # "write_write", "write_stateful", "exclusive_resource"
    resource: str = ""
    detail: str = ""


@dataclass
class Plan:
    """A read-only execution plan.

    Attributes
    ----------
    id:
        Unique plan identifier (for journaling / replay).
    steps:
        Ordered list of plan steps.
    conflicts:
        Detected conflicts that the caller must resolve.
    parallel_groups:
        Mapping of group id to step indices that may run concurrently.
    read_only:
        True if every step is a read (risk="read" or risk="none").
    stateful_steps:
        Indices of steps that carry host or session state.
    estimated_latency_ms:
        Sum of typical_latency_ms across all steps (wall-clock lower bound
        for fully serial execution).
    warnings:
        Non-fatal issues the caller should be aware of.
    """

    id: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    conflicts: list[PlanConflict] = field(default_factory=list)
    parallel_groups: dict[str, list[int]] = field(default_factory=dict)
    read_only: bool = True
    stateful_steps: list[int] = field(default_factory=list)
    estimated_latency_ms: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "steps": [
                {
                    "index": s.index,
                    "tool_id": s.tool_id,
                    "args": s.args,
                    "depends_on": s.depends_on,
                    "parallel_group": s.parallel_group,
                    "risk": s.risk,
                    "stateful": s.stateful,
                    "parallel_safe": s.parallel_safe,
                    "resource_group": s.resource_group,
                }
                for s in self.steps
            ],
            "conflicts": [
                {
                    "step_a": c.step_a,
                    "step_b": c.step_b,
                    "kind": c.kind,
                    "resource": c.resource,
                    "detail": c.detail,
                }
                for c in self.conflicts
            ],
            "parallel_groups": self.parallel_groups,
            "read_only": self.read_only,
            "safe_to_execute": len(self.conflicts) == 0,
            "stateful_steps": self.stateful_steps,
            "estimated_latency_ms": self.estimated_latency_ms,
            "warnings": self.warnings,
        }

    @property
    def safe_to_execute(self) -> bool:
        """True when no unresolved conflicts block execution."""
        return len(self.conflicts) == 0


# ---- Resource group inference ----------------------------------------------

def _infer_resource_group(man: ToolManifest, args: dict[str, Any]) -> str:
    """Infer a resource group from the manifest and arguments.

    This is a best-effort heuristic. Tools in the same group are
    serialized if either is not parallel_safe.
    """
    # File-system tools: the path is the resource
    if man.group in ("fs",) and "path" in args:
        return f"fs:{args['path']}"
    # Shell tools: the cwd groups them
    if man.group in ("shell",) and "cwd" in args:
        return f"shell:{args['cwd']}"
    # Integration tools: detect by naming convention (provider.resource.action)
    # or by the integrations group. Only treat as integration if the group
    # is explicitly 'integrations' or the id has 3+ dot-separated parts.
    parts = man.id.split(".")
    if man.group == "integrations" or len(parts) >= 3:
        prefix = parts[0]
        return f"integration:{prefix}"
    # Default: group by tool group
    return f"group:{man.group or man.id}"


# ---- Plan construction -----------------------------------------------------

def plan(
    tasks: list[tuple[ToolManifest, dict[str, Any]]],
    *,
    strict: bool = False,
    registry: Any = None,
) -> Plan:
    """Build a read-only execution plan for a list of (manifest, args) pairs.

    Parameters
    ----------
    tasks:
        Ordered list of (manifest, args) pairs. The input order is the
        *intended* execution order; the planner may reorder parallel-safe
        reads but preserves the relative order of writes.
    strict:
        If True, raise on conflicts instead of recording them.
    registry:
        Optional registry to pull observed latency from. When provided,
        the planner uses ``ProviderStats.mean_ms`` (if available) instead
        of the manifest's ``typical_latency_ms``, giving the autopilot
        cheaper cost estimates over time.

    Returns
    -------
    A Plan with steps, conflicts, and parallel group annotations.
    """
    plan_id = f"plan-{uuid.uuid4().hex[:12]}"
    steps: list[PlanStep] = []
    conflicts: list[PlanConflict] = []
    warnings: list[str] = []

    # First pass: assign indices and resource groups
    resource_groups: dict[str, list[int]] = {}
    for i, (man, args) in enumerate(tasks):
        rg = _infer_resource_group(man, args)
        step = PlanStep(
            index=i,
            tool_id=man.id,
            args=dict(args),
            risk=man.risk,
            stateful=man.stateful,
            parallel_safe=man.parallel_safe,
            resource_group=rg,
        )
        steps.append(step)
        resource_groups.setdefault(rg, []).append(i)

    # Second pass: detect conflicts and build dependencies
    for rg, indices in resource_groups.items():
        if len(indices) < 2:
            continue
        for _ai, ai_idx in enumerate(indices):
            for bi_idx in indices[ai_idx + 1:]:
                sa = steps[ai_idx]
                sb = steps[bi_idx]
                conflict = _check_conflict(sa, sb, rg)
                if conflict:
                    conflicts.append(conflict)
                  # Serialize non-parallel-safe steps in the same resource group
                if (not sa.parallel_safe or not sb.parallel_safe) and ai_idx not in sb.depends_on:
                    sb.depends_on.append(ai_idx)

    # Third pass: assign parallel groups
    parallel_groups: dict[str, list[int]] = {}
    for step in steps:
        if step.parallel_safe and not step.depends_on:
            group = f"parallel-{step.index}"
            step.parallel_group = group
            parallel_groups[group] = [step.index]
        elif step.depends_on:
            # This step depends on others; it's in its own group
            group = f"serial-{step.index}"
            step.parallel_group = group
            parallel_groups[group] = [step.index]
        else:
            group = f"single-{step.index}"
            step.parallel_group = group
            parallel_groups[group] = [step.index]

    # Merge parallel groups: steps with no dependencies and parallel_safe
    # can share a group
    free_indices = [s.index for s in steps if s.parallel_safe and not s.depends_on]
    if len(free_indices) > 1:
        merged_group = "parallel-batch"
        for idx in free_indices:
            steps[idx].parallel_group = merged_group
            if merged_group not in parallel_groups:
                parallel_groups[merged_group] = []
            parallel_groups[merged_group].append(idx)
        # Remove the individual groups
        for idx in free_indices:
            key = f"parallel-{idx}"
            parallel_groups.pop(key, None)

    # Compute summary fields
    read_only = all(s.risk in ("read", "none") for s in steps)
    stateful_steps = [s.index for s in steps if s.stateful != "none"]
    estimated_latency = sum(
        _typical_latency(s.tool_id, tasks, registry=registry) for s in steps
    )

    # Warnings
    if not read_only:
        write_count = sum(1 for s in steps if s.risk not in ("read", "none"))
        warnings.append(f"plan contains {write_count} write/destructive step(s)")
    if stateful_steps:
        warnings.append(
            f"{len(stateful_steps)} step(s) carry host/session state; "
            "execution order matters"
        )
    if conflicts:
        warnings.append(f"{len(conflicts)} conflict(s) detected; resolve before execution")

    p = Plan(
        id=plan_id,
        steps=steps,
        conflicts=conflicts,
        parallel_groups=parallel_groups,
        read_only=read_only,
        stateful_steps=stateful_steps,
        estimated_latency_ms=estimated_latency,
        warnings=warnings,
    )

    if strict and conflicts:
        detail = "; ".join(
            f"steps {c.step_a}+{c.step_b}: {c.kind}" for c in conflicts
        )
        raise SkeletonKeyError(
            E.BAD_ARGS,
            f"Plan has {len(conflicts)} conflict(s): {detail}",
            details={"conflicts": [
                {"step_a": c.step_a, "step_b": c.step_b, "kind": c.kind, "resource": c.resource}
                for c in conflicts
            ]},
        )

    return p


def _check_conflict(sa: PlanStep, sb: PlanStep, resource: str) -> PlanConflict | None:
    """Detect a conflict between two steps sharing a resource group."""
    # Write-write conflict
    if sa.risk in ("write", "destructive") and sb.risk in ("write", "destructive"):
        return PlanConflict(
            step_a=sa.index,
            step_b=sb.index,
            kind="write_write",
            resource=resource,
            detail=f"both steps write to {resource}",
        )
    # Write touching a stateful step
    if sa.risk in ("write", "destructive") and sb.stateful != "none":
        return PlanConflict(
            step_a=sa.index,
            step_b=sb.index,
            kind="write_stateful",
            resource=resource,
            detail=f"write at step {sa.index} touches stateful step {sb.index}",
        )
    if sb.risk in ("write", "destructive") and sa.stateful != "none":
        return PlanConflict(
            step_a=sa.index,
            step_b=sb.index,
            kind="write_stateful",
            resource=resource,
            detail=f"write at step {sb.index} touches stateful step {sa.index}",
        )
    return None


def _typical_latency(tool_id: str, tasks: list[tuple[ToolManifest, dict]], *, registry: Any = None) -> int:
    """Look up the typical latency for a tool id.

    When a registry is provided, prefers observed ``ProviderStats.mean_ms``
    over the manifest's declared ``typical_latency_ms``. The observed value
    is used only when there are >= 3 calls (enough evidence to trust);
    otherwise falls back to the declared value.
    """
    if registry is not None:
        try:
            all_stats = registry.stats(tool_id)
            tool_stats = all_stats.get(tool_id, {})
            if tool_stats.get("calls", 0) >= 3:
                observed = tool_stats.get("mean_ms")
                if observed is not None and observed > 0:
                    return int(observed)
        except Exception:
            pass
    for man, _ in tasks:
        if man.id == tool_id:
            return man.typical_latency_ms
    return 50  # default assumption
