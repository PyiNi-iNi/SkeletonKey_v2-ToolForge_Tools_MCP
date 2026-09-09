"""Tests for the P7 subagent boundary module (skeletonkey.core.subagent)."""

from __future__ import annotations

from typing import Any

from skeletonkey.core.manifest import ToolManifest
from skeletonkey.core.subagent import (
    Subagent,
    SubagentCall,
    SubagentSpec,
    call_as_subagent,
    create_subagent,
)

# ---- SubagentSpec tests ----------------------------------------------------

def test_default_spec():
    spec = SubagentSpec()
    assert spec.tier == "task"
    assert spec.read_only is True
    assert spec.max_calls == 50
    assert spec.max_wall_time_s == 60.0
    assert spec.max_output_bytes == 8_000


def test_spec_to_dict():
    spec = SubagentSpec(name="test-agent", tier="core", max_calls=10)
    d = spec.to_dict()
    assert d["name"] == "test-agent"
    assert d["tier"] == "core"
    assert d["max_calls"] == 10


# ---- Subagent tests --------------------------------------------------------

def test_create_subagent():
    sub = Subagent(spec=SubagentSpec(name="test"))
    assert sub.id.startswith("sub-")
    assert not sub.finished
    assert sub.calls == []


def test_subagent_remaining_calls():
    sub = Subagent(spec=SubagentSpec(max_calls=5))
    assert sub.remaining_calls == 5
    sub.calls = [SubagentCall()] * 3
    assert sub.remaining_calls == 2


def test_subagent_exhausted_by_calls():
    sub = Subagent(spec=SubagentSpec(max_calls=2))
    assert not sub.exhausted
    sub.calls = [SubagentCall()] * 2
    assert sub.exhausted


def test_subagent_exhausted_by_time():
    sub = Subagent(spec=SubagentSpec(max_wall_time_s=0.0))
    assert sub.exhausted


def test_subagent_close():
    sub = Subagent(spec=SubagentSpec(name="close-test"))
    sub.calls = [SubagentCall(ok=True), SubagentCall(ok=False)]
    summary = sub.close()
    assert summary["total_calls"] == 2
    assert summary["successful_calls"] == 1
    assert summary["failed_calls"] == 1
    assert sub.finished is True


def test_subagent_to_dict():
    sub = Subagent(spec=SubagentSpec(name="dict-test"))
    d = sub.to_dict()
    assert d["spec"]["name"] == "dict-test"
    assert "remaining_calls" in d
    assert "exhausted" in d


# ---- Access control tests --------------------------------------------------

def test_can_access_read_tool():
    sub = Subagent(spec=SubagentSpec(read_only=True))
    man = ToolManifest(id="fs.read", risk="read", tier="task")
    allowed, _reason = sub.can_access(man)
    assert allowed is True


def test_cannot_access_write_tool_in_read_only():
    sub = Subagent(spec=SubagentSpec(read_only=True))
    man = ToolManifest(id="fs.write", risk="write", tier="task")
    allowed, reason = sub.can_access(man)
    assert allowed is False
    assert "read-only" in reason


def test_can_access_write_when_not_read_only():
    sub = Subagent(spec=SubagentSpec(read_only=False))
    man = ToolManifest(id="fs.write", risk="write", tier="task")
    allowed, _reason = sub.can_access(man)
    assert allowed is True


def test_cannot_access_full_tier_from_task_subagent():
    sub = Subagent(spec=SubagentSpec(tier="task"))
    man = ToolManifest(id="debug.tool", risk="read", tier="full")
    allowed, reason = sub.can_access(man)
    assert allowed is False
    assert "tier" in reason


def test_can_access_core_tier_from_task_subagent():
    sub = Subagent(spec=SubagentSpec(tier="task"))
    man = ToolManifest(id="fs.read", risk="read", tier="core")
    allowed, _reason = sub.can_access(man)
    assert allowed is True


def test_allowlist_blocks_unlisted_tools():
    sub = Subagent(spec=SubagentSpec(allowed_tools=["fs.read"]))
    man_allowed = ToolManifest(id="fs.read", risk="read", tier="task")
    man_blocked = ToolManifest(id="fs.write", risk="write", tier="task")
    assert sub.can_access(man_allowed)[0] is True
    assert sub.can_access(man_blocked)[0] is False


def test_denylist_blocks_listed_tools():
    sub = Subagent(spec=SubagentSpec(denied_tools=["dangerous.tool"]))
    man = ToolManifest(id="dangerous.tool", risk="read", tier="task")
    allowed, reason = sub.can_access(man)
    assert allowed is False
    assert "denylist" in reason


def test_non_advertised_tool_blocked():
    sub = Subagent(spec=SubagentSpec())
    man = ToolManifest(id="internal.tool", risk="read", tier="task", advertised=False)
    allowed, reason = sub.can_access(man)
    assert allowed is False
    assert "not advertised" in reason


# ---- Ledger tail tests -----------------------------------------------------

def test_ledger_tail_returns_last_n():
    sub = Subagent()
    for i in range(25):
        sub.record_call(SubagentCall(tool_id=f"tool.{i}", ok=True))
    tail = sub.ledger_tail(limit=10)
    assert len(tail) == 10
    assert tail[0]["tool_id"] == "tool.15"
    assert tail[-1]["tool_id"] == "tool.24"


def test_ledger_tail_empty():
    sub = Subagent()
    assert sub.ledger_tail() == []


def test_ledger_tail_returns_dicts():
    sub = Subagent()
    sub.record_call(SubagentCall(tool_id="test", ok=True, data={"x": 1}))
    tail = sub.ledger_tail()
    assert isinstance(tail[0], dict)
    assert tail[0]["tool_id"] == "test"
    assert tail[0]["ok"] is True


# ---- call_as_subagent tests ------------------------------------------------

class FakeResult:
    def __init__(self, ok=True, data=None, error=None):
        self.ok = ok
        self.data = data
        self.error = error


class FakeRegistry:
    def __init__(self, manifests: dict[str, ToolManifest]):
        self._manifests = manifests

    def get(self, tool_id: str) -> ToolManifest:
        if tool_id in self._manifests:
            return self._manifests[tool_id]
        from skeletonkey.core.errors import E, SkeletonKeyError
        raise SkeletonKeyError(E.UNKNOWN_TOOL, f"No tool: {tool_id}")


class FakeEngine:
    def __init__(self, manifests: dict[str, ToolManifest], responses: dict[str, Any] | None = None):
        self.registry = FakeRegistry(manifests)
        self._responses = responses or {}

    def call(self, tool_id: str, args: dict) -> FakeResult:
        if tool_id in self._responses:
            return self._responses[tool_id]
        return FakeResult(ok=True, data={"echo": args})


def test_call_as_subagent_success():
    man = ToolManifest(id="fs.read", risk="read", tier="task")
    engine = FakeEngine({"fs.read": man})
    sub = Subagent(spec=SubagentSpec())
    sub.bind(engine)

    result = call_as_subagent(sub, "fs.read", {"path": "/tmp/x"})
    assert result["ok"] is True
    assert len(sub.calls) == 1
    assert sub.calls[0].tool_id == "fs.read"


def test_call_as_subagent_read_only_blocks_write():
    man = ToolManifest(id="fs.write", risk="write", tier="task")
    engine = FakeEngine({"fs.write": man})
    sub = Subagent(spec=SubagentSpec(read_only=True))
    sub.bind(engine)

    result = call_as_subagent(sub, "fs.write", {"path": "/tmp/x"})
    assert result["ok"] is False
    assert "read-only" in result["error"]


def test_call_as_subagent_unknown_tool():
    engine = FakeEngine({})
    sub = Subagent(spec=SubagentSpec())
    sub.bind(engine)

    result = call_as_subagent(sub, "nonexistent", {})
    assert result["ok"] is False
    assert "unknown" in result["error"].lower()


def test_call_as_subagent_exhausted():
    man = ToolManifest(id="tool.a", risk="read", tier="task")
    engine = FakeEngine({"tool.a": man})
    sub = Subagent(spec=SubagentSpec(max_calls=1))
    sub.bind(engine)

    r1 = call_as_subagent(sub, "tool.a", {})
    assert r1["ok"] is True
    r2 = call_as_subagent(sub, "tool.a", {})
    assert r2["ok"] is False
    assert "exhausted" in r2["error"]


def test_call_as_subagent_unbound():
    sub = Subagent(spec=SubagentSpec())
    result = call_as_subagent(sub, "any.tool", {})
    assert result["ok"] is False
    assert "not bound" in result["error"]


# ---- create_subagent convenience -------------------------------------------

def test_create_subagent_binds_engine():
    man = ToolManifest(id="tool.a", risk="read", tier="task")
    engine = FakeEngine({"tool.a": man})
    sub = create_subagent(engine)
    assert sub._engine is engine
    assert sub.spec.tier == "task"
    assert sub.spec.read_only is True
