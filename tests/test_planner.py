"""Tests for the P7 planner module (skeletonkey.core.planner)."""

from __future__ import annotations

import pytest

from skeletonkey.core.manifest import ToolManifest
from skeletonkey.core.planner import _infer_resource_group, plan

# ---- Helpers ---------------------------------------------------------------

def _manifest(
    id: str = "tool.a",
    risk: str = "read",
    parallel_safe: bool = True,
    stateful: str = "none",
    group: str = "test",
    typical_latency_ms: int = 50,
) -> ToolManifest:
    return ToolManifest(
        id=id,
        risk=risk,
        parallel_safe=parallel_safe,
        stateful=stateful,
        group=group,
        typical_latency_ms=typical_latency_ms,
    )


# ---- Basic plan construction -----------------------------------------------

def test_empty_plan():
    p = plan([])
    assert p.steps == []
    assert p.conflicts == []
    assert p.read_only is True
    assert p.safe_to_execute is True


def test_single_read_step():
    man = _manifest(risk="read")
    p = plan([(man, {"path": "/tmp/x"})])
    assert len(p.steps) == 1
    assert p.steps[0].tool_id == "tool.a"
    assert p.steps[0].risk == "read"
    assert p.read_only is True
    assert p.safe_to_execute is True


def test_write_step_sets_read_only_false():
    man = _manifest(risk="write")
    p = plan([(man, {"path": "/tmp/x"})])
    assert p.read_only is False
    assert any("write" in w for w in p.warnings)


def test_multiple_parallel_reads():
    manifests = [
        (_manifest(id=f"read.{i}"), {"path": f"/tmp/{i}"})
        for i in range(5)
    ]
    p = plan(manifests)
    assert len(p.steps) == 5
    # All parallel-safe reads with no dependencies should be in the same group
    assert all(s.parallel_safe for s in p.steps)


def test_serial_non_parallel_safe():
    manifests = [
        (_manifest(id="serial.a", parallel_safe=False), {}),
        (_manifest(id="serial.b", parallel_safe=False), {}),
    ]
    p = plan(manifests)
    # The second step should depend on the first (same resource group)
    assert 0 in p.steps[1].depends_on


# ---- Conflict detection ----------------------------------------------------

def test_write_write_conflict_detected():
    man = _manifest(id="fs.write", risk="write")
    p = plan([
        (man, {"path": "/tmp/x"}),
        (man, {"path": "/tmp/x"}),
    ])
    assert len(p.conflicts) >= 1
    assert p.conflicts[0].kind == "write_write"


def test_no_conflict_for_different_resources():
    man = _manifest(id="fs.write", risk="write", group="fs")
    p = plan([
        (man, {"path": "/tmp/a"}),
        (man, {"path": "/tmp/b"}),
    ])
    # Different paths -> different resource groups -> no conflict
    write_write = [c for c in p.conflicts if c.kind == "write_write"]
    assert len(write_write) == 0


def test_write_stateful_conflict():
    writer = _manifest(id="write.tool", risk="write", group="db")
    stateful = _manifest(id="stateful.tool", risk="read", stateful="host", group="db")
    p = plan([
        (stateful, {}),
        (writer, {}),
    ])
    assert any(c.kind == "write_stateful" for c in p.conflicts)


def test_strict_mode_raises_on_conflict():
    man = _manifest(id="fs.write", risk="write")
    with pytest.raises(Exception, match="conflict"):
        plan([
            (man, {"path": "/tmp/x"}),
            (man, {"path": "/tmp/x"}),
        ], strict=True)


# ---- Resource group inference ----------------------------------------------

def test_resource_group_fs_path():
    man = _manifest(group="fs")
    rg = _infer_resource_group(man, {"path": "/tmp/file.txt"})
    assert rg == "fs:/tmp/file.txt"


def test_resource_group_shell_cwd():
    man = _manifest(group="shell")
    rg = _infer_resource_group(man, {"cwd": "/home/user"})
    assert rg == "shell:/home/user"


def test_resource_group_integration():
    man = _manifest(id="github.repos.list", group="integrations")
    rg = _infer_resource_group(man, {})
    assert rg.startswith("integration:github")


def test_resource_group_default():
    man = _manifest(group="misc")
    rg = _infer_resource_group(man, {})
    assert rg == "group:misc"


# ---- Plan serialization ----------------------------------------------------

def test_plan_to_dict():
    man = _manifest(risk="read")
    p = plan([(man, {"x": 1})])
    d = p.to_dict()
    assert "id" in d
    assert "steps" in d
    assert len(d["steps"]) == 1
    assert d["steps"][0]["tool_id"] == "tool.a"
    assert d["read_only"] is True


def test_plan_id_is_unique():
    man = _manifest()
    p1 = plan([(man, {})])
    p2 = plan([(man, {})])
    assert p1.id != p2.id


# ---- Stateful steps tracking -----------------------------------------------

def test_stateful_steps_tracked():
    reads = [(_manifest(id=f"r.{i}"), {}) for i in range(3)]
    stateful = (_manifest(id="stateful.one", stateful="host"), {})
    p = plan([*reads, stateful])
    assert len(p.stateful_steps) == 1
    assert 3 in p.stateful_steps
    assert any("state" in w for w in p.warnings)


# ---- Estimated latency -----------------------------------------------------

def test_estimated_latency_is_sum():
    manifests = [
        (_manifest(id="lat.a", typical_latency_ms=100), {}),
        (_manifest(id="lat.b", typical_latency_ms=200), {}),
        (_manifest(id="lat.c", typical_latency_ms=300), {}),
    ]
    p = plan(manifests)
    assert p.estimated_latency_ms == 600


# ---- Safe to execute -------------------------------------------------------

def test_safe_to_execute_when_no_conflicts():
    man = _manifest()
    p = plan([(man, {})])
    assert p.safe_to_execute is True


def test_not_safe_when_conflicts_exist():
    man = _manifest(id="fs.write", risk="write")
    p = plan([
        (man, {"path": "/tmp/x"}),
        (man, {"path": "/tmp/x"}),
    ])
    assert p.safe_to_execute is False
