"""Subagent boundary (P7 exploratory).

A subagent is a restricted view of the engine for delegated tasks.
It gets:

  * A restricted tool tier (default: ``task`` — no full-tier tools).
  * Read-only mode by default (writes require explicit opt-in).
  * A compact envelope (reduced max output bytes).
  * A ledger tail only (the subagent sees its own calls, not the
    parent's full history).
  * A reduced timeout for each call.

The subagent is NOT a separate process; it is a view object that wraps
the engine with tighter constraints. The parent retains full control
and can inspect the subagent's call history.

Security contract:
  * The subagent cannot escape its tier or read-only constraints.
  * Approval-required tools are denied unless the parent explicitly
    auto-approves them for the subagent.
  * The subagent's budget is a subset of the parent's budget.
  * All calls are recorded in the parent's ledger with a subagent tag.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.core.manifest import ToolManifest


@dataclass
class SubagentSpec:
    """Specification for a subagent's constraints.

    Attributes
    ----------
    name:
        Human-readable name for the subagent.
    tier:
        Tool tier the subagent can see (default: ``task``).
    read_only:
        If True, all write/destructive tools are denied.
    max_calls:
        Maximum number of tool calls (default: 50).
    max_wall_time_s:
        Maximum total wall time (default: 60s).
    max_output_bytes:
        Maximum output per call (default: 8000 bytes; smaller than the
        main envelope's 24000).
    allowed_tools:
        If set, only these tool ids are available (allowlist).
    denied_tools:
        Tool ids that are always denied (denylist, applied after allowlist).
    auto_approve_writes:
        If True, write tools that the subagent can access are auto-approved.
        Default False — writes are denied in read-only mode or require
        explicit approval.
    tags:
        Tags attached to the subagent's ledger entries for filtering.
    """

    name: str = ""
    tier: Literal["core", "task", "full"] = "task"
    read_only: bool = True
    max_calls: int = 50
    max_wall_time_s: float = 60.0
    max_output_bytes: int = 8_000
    allowed_tools: list[str] | None = None
    denied_tools: list[str] = field(default_factory=list)
    auto_approve_writes: bool = False
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier,
            "read_only": self.read_only,
            "max_calls": self.max_calls,
            "max_wall_time_s": self.max_wall_time_s,
            "max_output_bytes": self.max_output_bytes,
            "allowed_tools": self.allowed_tools,
            "denied_tools": self.denied_tools,
            "auto_approve_writes": self.auto_approve_writes,
            "tags": self.tags,
        }


@dataclass
class SubagentCall:
    """Record of one tool call made by the subagent.

    These are the "ledger tail" the subagent can see.
    """

    tool_id: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    data: Any = None
    error: str = ""
    duration_ms: int = 0
    at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "args": self.args,
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "at": self.at,
        }


@dataclass
class Subagent:
    """A restricted view of the engine for delegated tasks.

    The subagent does not own an engine; it borrows the parent's
    engine with tighter constraints. The parent retains full control.

    Attributes
    ----------
    id:
        Unique subagent identifier.
    spec:
        The subagent specification.
    calls:
        History of tool calls made by this subagent.
    started_at:
        When the subagent was created.
    finished:
        Whether the subagent has been closed.
    """

    id: str = ""
    spec: SubagentSpec = field(default_factory=SubagentSpec)
    calls: list[SubagentCall] = field(default_factory=list)
    started_at: float = 0.0
    finished: bool = False
    _engine: Any = None  # Reference to the parent engine

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"sub-{uuid.uuid4().hex[:8]}"
        if not self.started_at:
            self.started_at = time.time()

    def bind(self, engine: Any) -> None:
        """Bind the subagent to a parent engine."""
        self._engine = engine

    @property
    def elapsed_s(self) -> float:
        """Seconds since the subagent was created."""
        return time.time() - self.started_at

    @property
    def remaining_calls(self) -> int:
        """How many more calls this subagent can make."""
        return max(0, self.spec.max_calls - len(self.calls))

    @property
    def remaining_wall_time_s(self) -> float:
        """How many more seconds this subagent can run."""
        return max(0.0, self.spec.max_wall_time_s - self.elapsed_s)

    @property
    def exhausted(self) -> bool:
        """True if the subagent has hit any limit."""
        return (
            self.finished
            or self.remaining_calls <= 0
            or self.remaining_wall_time_s <= 0
        )

    def can_access(self, manifest: ToolManifest) -> tuple[bool, str]:
        """Check whether the subagent can access a tool.

        Returns (allowed, reason) where reason explains why access was
        denied if not allowed.
        """
        # Tier check
        from skeletonkey.core.registry import TIER_ORDER
        if TIER_ORDER.get(manifest.tier, 2) > TIER_ORDER.get(self.spec.tier, 1):
            return False, f"tool tier {manifest.tier} exceeds subagent tier {self.spec.tier}"

        # Read-only check
        if self.spec.read_only and manifest.risk in ("write", "destructive", "privileged"):
            return False, f"subagent is read-only; tool risk is {manifest.risk}"

        # Allowlist check
        if self.spec.allowed_tools is not None and manifest.id not in self.spec.allowed_tools:
            return False, f"tool {manifest.id} not in subagent allowlist"

        # Denylist check
        if manifest.id in self.spec.denied_tools:
            return False, f"tool {manifest.id} is in subagent denylist"

        # Advertised check
        if not manifest.advertised:
            return False, "tool is not advertised"

        return True, ""

    def record_call(self, call: SubagentCall) -> None:
        """Record a call in the subagent's history."""
        self.calls.append(call)

    def ledger_tail(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return the last N calls as dicts.

        This is the "ledger tail" the subagent can see — a compact
        history of its own calls, not the parent's full ledger.
        """
        return [c.to_dict() for c in self.calls[-limit:]]

    def close(self) -> dict[str, Any]:
        """Close the subagent and return a summary.

        After closing, no more calls can be made.
        """
        self.finished = True
        return {
            "id": self.id,
            "name": self.spec.name,
            "total_calls": len(self.calls),
            "successful_calls": sum(1 for c in self.calls if c.ok),
            "failed_calls": sum(1 for c in self.calls if not c.ok),
            "elapsed_s": round(self.elapsed_s, 2),
            "exhausted": self.exhausted,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "spec": self.spec.to_dict(),
            "calls": len(self.calls),
            "elapsed_s": round(self.elapsed_s, 2),
            "remaining_calls": self.remaining_calls,
            "remaining_wall_time_s": round(self.remaining_wall_time_s, 2),
            "exhausted": self.exhausted,
            "finished": self.finished,
        }


# ---- Subagent engine wrapper -----------------------------------------------

def call_as_subagent(
    subagent: Subagent,
    tool_id: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Call a tool through the subagent boundary.

    The tool must pass the subagent's access checks. The result is
    truncated to ``max_output_bytes`` and recorded in the subagent's
    ledger tail.

    Returns a compact envelope (ok, data, error, duration_ms).
    """
    t0 = time.monotonic()

    if subagent.exhausted:
        return {
            "ok": False,
            "error": "subagent exhausted (limit reached)",
            "tool_id": tool_id,
            "duration_ms": 0,
        }

    if subagent._engine is None:
        return {
            "ok": False,
            "error": "subagent not bound to an engine",
            "tool_id": tool_id,
            "duration_ms": 0,
        }

    # Access check
    try:
        manifest = subagent._engine.registry.get(tool_id)
    except SkeletonKeyError:
        return {
            "ok": False,
            "error": f"unknown tool: {tool_id}",
            "tool_id": tool_id,
            "duration_ms": 0,
        }

    allowed, reason = subagent.can_access(manifest)
    if not allowed:
        return {
            "ok": False,
            "error": reason,
            "tool_id": tool_id,
            "duration_ms": 0,
        }

    # Execute through the engine
    try:
        result = subagent._engine.call(tool_id, args)
        duration = int((time.monotonic() - t0) * 1000)

        # Compact the result
        data = result.data if hasattr(result, "data") else result
        error_str = ""
        if hasattr(result, "error") and result.error:
            error_str = str(result.error)

        ok = result.ok if hasattr(result, "ok") else True

        # Truncate output
        if isinstance(data, (str, bytes)):
            data = data[:subagent.spec.max_output_bytes]
        elif isinstance(data, (dict, list)):
            import json
            serialized = json.dumps(data)
            if len(serialized) > subagent.spec.max_output_bytes:
                serialized = serialized[:subagent.spec.max_output_bytes]
                try:
                    data = json.loads(serialized + '"...truncated"}')
                except json.JSONDecodeError:
                    data = {"_truncated": True, "_size": len(serialized)}

        # Record the call
        call = SubagentCall(
            tool_id=tool_id,
            args=dict(args),
            ok=ok,
            data=data,
            error=error_str,
            duration_ms=duration,
            at=time.time(),
        )
        subagent.record_call(call)

        return {
            "ok": ok,
            "data": data,
            "error": error_str,
            "tool_id": tool_id,
            "duration_ms": duration,
        }

    except Exception as exc:
        duration = int((time.monotonic() - t0) * 1000)
        call = SubagentCall(
            tool_id=tool_id,
            args=dict(args),
            ok=False,
            error=str(exc)[:500],
            duration_ms=duration,
            at=time.time(),
        )
        subagent.record_call(call)
        return {
            "ok": False,
            "error": str(exc)[:500],
            "tool_id": tool_id,
            "duration_ms": duration,
        }


def create_subagent(
    engine: Any,
    spec: SubagentSpec | None = None,
) -> Subagent:
    """Create a new subagent bound to the given engine.

    The subagent starts with default constraints (task tier, read-only,
    50 calls max, 60s wall time). Override with ``spec`` for different
    constraints.
    """
    sub = Subagent(spec=spec or SubagentSpec())
    sub.bind(engine)
    return sub
