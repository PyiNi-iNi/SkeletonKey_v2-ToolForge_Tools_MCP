"""Tests for P7 engine integration: planner/export tools, subagent CLI, quarantine promotion."""

from __future__ import annotations

import json
import subprocess
import sys

from skeletonkey.core.manifest import ToolManifest
from skeletonkey.core.planner import _typical_latency, plan
from skeletonkey.core.quarantine import (
    QuarantineSpec,
    call_quarantined,
    create_session,
    promote_and_register,
)
from skeletonkey.core.registry import Registry

# ---- promote_and_register --------------------------------------------------

class TestPromoteAndRegister:
    def test_promote_and_register_success(self):
        session = create_session("gen.test_tool", spec=QuarantineSpec(timeout_s=5.0))
        result = call_quarantined(session, "result = {'ok': True}")
        assert result.ok is True

        reg = Registry()

        def handler(**kw):
            return {"promoted": True}

        outcome = promote_and_register(
            session, reg,
            handler=handler,
            tier="full",
            description="A promoted test tool",
            risk="read",
            capability="gen.test",
        )
        assert outcome["ok"] is True
        assert outcome["tool_id"] == "gen.test_tool"
        assert outcome["source"] == "generated"
        assert outcome["tier"] == "full"
        assert outcome["quarantine_successes"] == 1

        # The tool should now be in the registry
        man = reg.get("gen.test_tool")
        assert man.id == "gen.test_tool"
        assert man.source == "generated"
        assert man.tier == "full"
        assert "generated" in man.tags
        session.cleanup()

    def test_promote_and_register_fails_without_success(self):
        session = create_session("gen.no_success")
        reg = Registry()

        def handler(**kw):
            return None

        outcome = promote_and_register(session, reg, handler=handler)
        assert outcome["ok"] is False
        assert outcome["reason"] == "not_eligible"
        session.cleanup()

    def test_promote_and_register_custom_tier(self):
        session = create_session("gen.task_tool", spec=QuarantineSpec(timeout_s=5.0))
        call_quarantined(session, "result = 'ok'")
        reg = Registry()
        outcome = promote_and_register(
            session, reg,
            handler=lambda **kw: None,
            tier="task",
            group="custom",
        )
        assert outcome["ok"] is True
        assert outcome["tier"] == "task"
        assert outcome["group"] == "custom"
        man = reg.get("gen.task_tool")
        assert man.tier == "task"
        assert man.group == "custom"
        session.cleanup()


# ---- Planner observed-latency feedback ------------------------------------

class TestPlannerLatencyFeedback:
    def test_latency_uses_declared_when_no_registry(self):
        man = ToolManifest(id="tool.a", typical_latency_ms=200)
        result = _typical_latency("tool.a", [(man, {})])
        assert result == 200

    def test_latency_uses_declared_when_no_stats(self):
        man = ToolManifest(id="tool.a", typical_latency_ms=200)
        reg = Registry()
        result = _typical_latency("tool.a", [(man, {})], registry=reg)
        assert result == 200

    def test_latency_uses_observed_when_enough_calls(self):
        man = ToolManifest(id="tool.a", typical_latency_ms=200)
        reg = Registry()
        # Register the tool and record some calls
        reg.register(man, handler=lambda **kw: None)
        reg.record("tool.a", ok=True, duration_ms=50)
        reg.record("tool.a", ok=True, duration_ms=60)
        reg.record("tool.a", ok=True, duration_ms=55)
        result = _typical_latency("tool.a", [(man, {})], registry=reg)
        # Should use observed mean (~55ms) not declared (200ms)
        assert result == 55

    def test_latency_falls_back_when_too_few_calls(self):
        man = ToolManifest(id="tool.a", typical_latency_ms=200)
        reg = Registry()
        reg.register(man, handler=lambda **kw: None)
        reg.record("tool.a", ok=True, duration_ms=50)
        reg.record("tool.a", ok=True, duration_ms=60)
        # Only 2 calls - not enough evidence
        result = _typical_latency("tool.a", [(man, {})], registry=reg)
        assert result == 200  # falls back to declared

    def test_plan_uses_registry_latency(self):
        reg = Registry()
        man = ToolManifest(id="tool.a", typical_latency_ms=200)
        reg.register(man, handler=lambda **kw: None)
        for _ in range(5):
            reg.record("tool.a", ok=True, duration_ms=30)
        p = plan([(man, {})], registry=reg)
        assert p.estimated_latency_ms == 30


# ---- Subagent CLI flag (integration test) ---------------------------------

class TestSubagentCLI:
    @staticmethod
    def _run_cli(*extra_args: str, state_dir: str = "/tmp/sk-test-sub-cli") -> dict:
        """Run sk call with --as-subagent and return the parsed JSON."""
        import os
        import shutil
        env = {**os.environ, "SKELETONKEY_STATE_DIR": state_dir}
        sk_bin = shutil.which("sk") or os.path.join(
            os.path.dirname(sys.executable), "sk"
        )
        cmd = [sk_bin, "--json", "call", *extra_args]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            cwd="/tmp", env=env,
        )
        return json.loads(result.stdout), result.returncode

    def test_call_as_subagent_read_only_blocks_write(self):
        """sk call with --as-subagent should enforce read-only by default."""
        data, _rc = self._run_cli(
            "fs.write", '{"path": "/tmp/x", "content": "test"}',
            "--as-subagent", "--sub-read-only",
            state_dir="/tmp/sk-test-sub-ro",
        )
        assert data["ok"] is False
        assert "read-only" in data.get("error", "")
        assert "subagent" in data

    def test_call_as_subagent_allows_read(self):
        """sk call with --as-subagent should allow read tools."""
        data, _rc = self._run_cli(
            "registry.stats", "{}",
            "--as-subagent",
            state_dir="/tmp/sk-test-sub-read",
        )
        assert data["ok"] is True
        assert "subagent" in data
        assert data["subagent"]["total_calls"] == 1

    def test_call_as_subagent_summary_in_output(self):
        """The subagent summary should be included in the output."""
        data, _rc = self._run_cli(
            "registry.stats", "{}",
            "--as-subagent", "--sub-max-calls", "10",
            state_dir="/tmp/sk-test-sub-summary",
        )
        assert "subagent" in data
        summary = data["subagent"]
        assert summary["total_calls"] == 1
        assert "elapsed_s" in summary
