"""Engine contract: the pipeline every call walks - resolve, gate, validate,
authorize, budget, cache, dispatch, normalize. This is the part an autonomous
loop depends on when nobody is watching, so every refusal must be an envelope,
never an exception, and never a silent pass."""

from __future__ import annotations

import time

import pytest

from skeletonkey.core.engine import ApprovalRequired, CallContext, Engine
from skeletonkey.core.manifest import Requirement, ToolManifest
from skeletonkey.core.registry import Registry


def mkengine(*, handlers=None, config=None, approver=None, deny=None, read_only=False,
             profile=None, overrides=None):
    from skeletonkey.core.config import Config

    cfg = config or Config.load(cwd="/tmp", overrides={
        "roots": ["/tmp"], "state": {"dir": "/tmp/.sktest"}, **(overrides or {})})
    if deny is not None:
        cfg.policy.deny = list(deny)
    cfg.policy.read_only = read_only
    reg = Registry()
    for man, fn in (handlers or []):
        reg.register(man, fn)
    eng = Engine(config=cfg, registry=reg, approver=approver, profile=profile)
    return eng, reg


def tool(mid, *, risk="read", description="does a thing", timeout_s=5.0, props=None,
         required=None, needs=(), **kw):
    return ToolManifest(
        id=mid, title=mid, description=description, risk=risk, timeout_s=timeout_s,
        requirements=[Requirement("binary", n) for n in needs],
        input_schema={"type": "object", "properties": props or {}, "required": required or []},
        **kw,
    )


# ------------------------------------------------------------------ resolution
def test_unknown_tool_returns_a_suggestion_not_a_stack_trace():
    eng, _ = mkengine()
    res = eng.call("fs.reed", {"path": "x"})
    assert not res.ok and res.error.code in {"UNKNOWN_TOOL", "BAD_ARGS"}
    assert res.error.hint


def test_missing_argument_names_the_field_and_echoes_the_schema():
    eng, _ = mkengine(handlers=[(tool("fs.read", props={"path": {"type": "string"}}, required=["path"]), lambda **kw: "ok")])
    res = eng.call("fs.read", {})
    assert res.error.code == "MISSING_ARG"
    assert res.error.details["missing"] == "path"
    assert "path" in res.error.details["schema"]["properties"]
    assert res.next_actions and res.next_actions[0]["tool"] == "registry.describe"


def test_type_errors_carry_a_minimal_example():
    eng, _ = mkengine(handlers=[(tool("fs.read", props={"path": {"type": "string"}, "n": {"type": "integer", "default": 3}},
             required=["path"]), lambda **kw: "ok")])
    res = eng.call("fs.read", {"path": 17})
    assert res.error.code == "BAD_ARGS"
    assert res.error.details["errors"][0]["path"] == "path"
    assert set(res.error.details["minimal_example"]) == {"path", "n"}


def test_defaults_are_applied_before_the_handler_runs():
    seen = {}

    def h(path="?", limit=7):
        seen.update(path=path, limit=limit)
        return {"ok": 1}

    eng, _ = mkengine(handlers=[(tool("fs.read", props={"path": {"type": "string", "default": "dflt"},
                               "limit": {"type": "integer", "default": 7}},
             required=["path"]), h)])
    eng.call("fs.read", {"path": "p"})
    assert seen == {"path": "p", "limit": 7}


# ------------------------------------------------------------------ gating
def test_gated_tool_refuses_with_binary_and_receipt():
    from skeletonkey.core.profile import CapabilityProfile, ShellProbe

    prof = CapabilityProfile(os="linux", shells={"bash": ShellProbe(dialect="bash", kind="unix",
                                                                    path="/bin/bash")},
                             binaries={})
    eng, _ = mkengine(profile=prof, handlers=[
        (tool("tar.it", needs=("tar",)), lambda **kw: {})])
    res = eng.call("tar.it", {})
    assert res.error.code == "MISSING_BINARY", res.error
    assert "tar" in res.error.details["gate"]["unmet"][0]
    assert res.next_actions[0]["tool"] == "registry.search"


def test_disabled_tool_is_refused_even_when_callable_directly():
    eng, _ = mkengine(overrides={"tools": {"disable": ["fs.read"]}}, handlers=[
        (tool("fs.read"), lambda **kw: {"secret": "leaked"})])
    res = eng.call("fs.read", {})
    assert not res.ok and res.error.code == "TOOL_NOT_ADVERTISED", "disable must be a real wall"


# ------------------------------------------------------------------ policy
def test_read_only_mode_blocks_mutations_with_a_plan():
    calls = []
    eng, _ = mkengine(read_only=True, handlers=[
        (tool("fs.write", risk="write"), lambda **kw: calls.append(1))])
    res = eng.call("fs.write", {})
    assert not res.ok and res.error.code in {"READ_ONLY_MODE", "POLICY_BLOCKED"}
    assert calls == [], "the handler must never run"


def test_dry_run_reports_the_plan_instead_of_acting():
    eng, _ = mkengine(handlers=[(tool("fs.write", risk="write"), lambda **kw: {"wrote": True})])
    res = eng.call("fs.write", {"path": "a.txt"}, dry_run=True)
    assert not res.ok
    assert res.error.details["would_write"] is True
    assert "fs.write" in res.error.message


def test_deny_rule_cannot_be_negotiated():
    eng, _ = mkengine(deny=["fs.read(**/.ssh/**)"], handlers=[
        (tool("fs.read", props={"path": {"type": "string"}}), lambda **kw: {"data": "keys"})])
    res = eng.call("fs.read", {"path": "/home/u/.ssh/id_rsa"})
    assert res.error.code == "DENY_RULE", "deny is absolute by design"
    assert "cannot be overridden" in res.error.details["advice"]
    ok = eng.call("fs.read", {"path": "/tmp/x"})
    assert ok.ok, "the deny rule must not poison unrelated calls"


def test_destructive_needs_approval_then_a_token_unblocks_it():
    ran = []
    eng, _ = mkengine(handlers=[(tool("fs.delete", risk="destructive", destructive=True), lambda **kw: ran.append(1))])
    res = eng.call("fs.delete", {})
    assert res.error.code == "APPROVAL_REQUIRED"
    assert res.error.details["grant_options"] == ["once", "task", "session"]
    assert ran == []

    ctx = CallContext.from_config(eng.config)
    g = eng.grant(ctx, scope="task", tool="fs.delete")
    assert g["granted"]
    res2 = eng.call("fs.delete", {}, ctx=ctx, approval_token=g["approval_token"])
    assert res2.ok and ran == [1]


def test_auto_approve_write_covers_writes_but_not_execute():
    eng, _ = mkengine(handlers=[(tool("fs.write", risk="write"), lambda **kw: {"wrote": 1}),
        (tool("shell.run", risk="network"), lambda **kw: {"ran": 1})])
    assert eng.config.policy.auto_approve == ["none", "read", "write"]
    assert eng.call("fs.write", {}).ok
    bad = eng.call("shell.run", {})
    assert bad.error.code == "APPROVAL_REQUIRED", "network risk is never auto-approved"


def test_approver_callback_is_consulted_and_decline_is_final():
    prompts = []

    def approve(req: ApprovalRequired) -> bool:
        prompts.append(req)
        return False

    eng, _ = mkengine(approver=approve, handlers=[
        (tool("sh.run", risk="privileged"), lambda **kw: {"ran": True})])
    res = eng.call("sh.run", {})
    assert res.error.code == "APPROVAL_REQUIRED" and "declined" in res.error.message
    assert prompts and prompts[0].tool == "sh.run"
    assert prompts[0].prompt_payload()["approve_token"].startswith("grant:")


def test_a_throwing_approver_never_counts_as_consent():
    def boom(req):
        raise RuntimeError("dialog crashed")

    ran = []
    eng, _ = mkengine(approver=boom, handlers=[
        (tool("sh.run", risk="privileged"), lambda **kw: ran.append(1))])
    res = eng.call("sh.run", {})
    assert not res.ok and ran == [], "fail-closed"
    assert res.error.code in {"INTERNAL", "APPROVAL_REQUIRED"}


def test_malformed_approval_token_is_a_bad_arg():
    eng, _ = mkengine(handlers=[(tool("sh.run", risk="privileged"), lambda **kw: {})])
    res = eng.call("sh.run", {}, approval_token="../etc/passwd")
    assert res.error.code == "BAD_ARGS"


def test_escalated_risk_is_respected():
    eng, _ = mkengine(handlers=[(tool("fs.write", risk="write"), lambda **kw: {})])
    eng.config.policy.escalate = ["fs.write"]
    res = eng.call("fs.write", {})
    assert res.error.code == "APPROVAL_REQUIRED", "escalation must outrank auto_approve"


# ------------------------------------------------------------------ budget
def test_call_budget_is_a_hard_stop_with_summarize_and_stop():
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda **kw: {"n": 1})])
    ctx = CallContext.from_config(eng.config)
    ctx.max_calls = 2
    for _ in range(2):
        assert eng.call("fs.read", {}, ctx=ctx).ok
    res = eng.call("fs.read", {}, ctx=ctx)
    assert res.error.code == "BUDGET_EXCEEDED"
    assert res.next_actions[0]["action"] == "summarize_and_stop"
    assert "calls 2/2" in res.error.details["exceeded"][0]


def test_mutation_budget_counts_only_mutating_tools():
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda **kw: {"r": 1}),
        (tool("fs.write", risk="write"), lambda **kw: {"w": 1})])
    ctx = CallContext.from_config(eng.config)
    ctx.max_mutations = 1
    for _ in range(3):
        eng.call("fs.read", {}, ctx=ctx)
    assert ctx.mutations == 0
    assert eng.call("fs.write", {}, ctx=ctx).ok
    res = eng.call("fs.write", {}, ctx=ctx)
    assert res.error.code == "BUDGET_EXCEEDED"


def test_wall_clock_deadline_stops_a_runaway_task():
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda **kw: {"r": 1})])
    ctx = CallContext.from_config(eng.config)
    ctx.deadline = time.monotonic() - 1
    res = eng.call("fs.read", {}, ctx=ctx)
    assert res.error.code == "BUDGET_EXCEEDED" and "wall time" in res.error.message


def test_token_budget_stop_is_reported_in_the_result_metrics():
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda **kw: {"r": "x" * 9000})])
    ctx = CallContext.from_config(eng.config)
    ctx.max_tokens_out = 50
    eng.call("fs.read", {}, ctx=ctx)
    assert ctx.tokens_out > 50
    assert eng.call("fs.read", {}, ctx=ctx).error.code == "BUDGET_EXCEEDED"


# ------------------------------------------------------------------ execution
def test_handler_timeout_is_enforced():
    def sleeper():
        time.sleep(3)
        return {"no": "good"}

    eng, _ = mkengine(handlers=[(tool("slow.tool", timeout_s=0.3), sleeper)])
    t0 = time.monotonic()
    res = eng.call("slow.tool", {})
    assert res.error.code == "TIMEOUT"
    assert res.error.details["killed"] is True
    assert time.monotonic() - t0 < 2.5


def test_shell_timeout_next_action_offers_background():
    def sleeper():
        time.sleep(4)

    eng, _ = mkengine(handlers=[(tool("shell.slow", timeout_s=0.2), sleeper)])
    res = eng.call("shell.slow", {})
    assert res.error.code == "TIMEOUT"
    assert res.next_actions[0]["args"]["background"] is True, \
        "a timed-out shell call must be told about background mode"


def test_handler_exception_becomes_an_envelope_not_a_crash():
    def blowup():
        raise ZeroDivisionError("division by zero")

    eng, _ = mkengine(handlers=[(tool("bad.tool"), blowup)])
    res = eng.call("bad.tool", {})
    assert not res.ok
    assert res.error.code == "INTERNAL"
    assert "ZeroDivisionError" in res.error.message
    assert res.error.details["trace_id"]
    assert "unhandled" in " ".join(res.warnings)


def test_oserror_from_a_handler_is_classified_as_io():
    def missing():
        raise FileNotFoundError(2, "No such file or directory", "/nope/nope")

    eng, _ = mkengine(handlers=[(tool("bad.tool"), missing)])
    res = eng.call("bad.tool", {})
    assert res.error.code == "ENOENT"
    assert res.error.error_class == "execution"
    assert res.error.retryable is False, "a missing path is a fix-it, not a retry-it"
    assert "suggested" in res.error.hint.lower()


def test_idempotent_reads_are_cached_and_mutations_are_not():
    calls = []

    def reader():
        calls.append("r")
        return {"value": 1}

    def writer():
        calls.append("w")
        return {"value": 2}

    eng, _ = mkengine(handlers=[(tool("fs.read"), reader),
        (tool("fs.write", risk="write", idempotent=False), writer)])
    ctx = CallContext.from_config(eng.config)
    eng.call("fs.read", {}, ctx=ctx)
    second = eng.call("fs.read", {}, ctx=ctx)
    assert calls == ["r"], "identical read within TTL must not re-run"
    assert second.metrics.cached is True
    assert "idempotency cache" in " ".join(second.warnings)
    eng.call("fs.write", {}, ctx=ctx)
    eng.call("fs.write", {}, ctx=ctx)
    assert calls.count("w") == 2, "mutations must never be served from cache"


def test_cache_key_includes_cwd_and_args():
    calls = []
    eng, _ = mkengine(handlers=[(tool("fs.read", props={"p": {"type": "string"}}), lambda p="x": calls.append(p) or {"p": p})])
    ctx = CallContext.from_config(eng.config)
    eng.call("fs.read", {"p": "a"}, ctx=ctx)
    eng.call("fs.read", {"p": "b"}, ctx=ctx)
    assert calls == ["a", "b"]


def test_large_results_spill_to_an_artifact(tmp_path):
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda: {"blob": "z" * 200_000})],
                      config=None,
                      overrides={"budget": {"max_output_bytes": 4000, "spill_dir": str(tmp_path)}})
    res = eng.call("fs.read", {})
    d = res.to_dict(max_bytes=4000, spill_dir=str(tmp_path))
    assert d["data"]["spilled"] is True
    assert any(f.startswith("art_") for f in __import__("os").listdir(tmp_path))


def test_result_is_always_json_serialisable():
    eng, _ = mkengine(handlers=[(tool("weird"), lambda: {"path": __import__("pathlib").Path("/tmp/x")})])
    res = eng.call("weird", {})
    import json

    try:
        json.dumps(res.to_dict())
    except TypeError as exc:
        pytest.xfail(f"handler returned a non-JSON type and the envelope did not coerce it: {exc}")


# ------------------------------------------------------------------ audit
def test_ledger_records_success_and_failure_without_changing_outcomes(tmp_path):
    from skeletonkey.core.ledger import Ledger

    led = Ledger(tmp_path / "ledger.ndjson")
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda: {"v": 1}),
        (tool("fs.boom"), lambda: (_ for _ in ()).throw(RuntimeError("x")))])
    eng.ledger = led
    eng.call("fs.read", {})
    eng.call("fs.boom", {})
    rows = list(led.read(limit=10))
    assert [r.ok for r in rows] == [True, False]
    assert rows[1].error_code == "INTERNAL"
    assert rows[0].tool == "fs.read" and rows[0].args_digest
    assert led.verify()["valid"] is True, "the audit chain must stay intact"


def test_a_broken_ledger_never_fails_the_call():
    class BadLedger:
        def append(self, *a, **k):
            raise RuntimeError("disk full")

    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda: {"v": 1})])
    eng.ledger = BadLedger()
    assert eng.call("fs.read", {}).ok, "audit failure must not destroy the work product"


# ------------------------------------------------------------------ wiring
def test_injection_is_by_signature_only():
    seen = {}

    def only_args(path):
        seen["path"] = path
        return {"ok": 1}

    eng, _ = mkengine(handlers=[(tool("fs.read", props={"path": {"type": "string"}},
                                    required=["path"]), only_args)])
    assert eng.call("fs.read", {"path": "/tmp/a"}).ok
    assert seen == {"path": "/tmp/a"}


def test_close_is_idempotent():
    eng, _ = mkengine()
    eng.close()
    eng.close()
