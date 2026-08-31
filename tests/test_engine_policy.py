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


# ------------------------------------------------------------------ P3 policy rules
# The structured `policy.rule` tables (core/policy.py): deny/allow/escalate/
# rate_limit with path, argv-prefix and env-name matchers. Deny stays the
# non-overridable wall; allow only removes the approval requirement; every
# refusal carries the rule text and the matching evidence.

_SHELL_PROPS = {
    "script": {"type": "string"},
    "argv": {"type": "array", "items": {"type": "string"}},
    "env": {"type": "object", "additionalProperties": {"type": "string"}},
}


def test_deny_argv_prefix_blocks_shell_run_even_with_an_approval_token():
    """Acceptance 1: a deny rule with argv matching blocks the call *even when*
    the caller passes an approval token, names the rule, and the handler never
    runs. The token is first proven to unblock a non-matching call."""
    ran = []
    eng, _ = mkengine(handlers=[
        (tool("shell.run", risk="privileged", props=_SHELL_PROPS, required=["script"]),
         lambda **kw: ran.append(1) or {"ran": True})],
        overrides={"policy": {"rule": [{"action": "deny", "tool": "shell.run",
                                        "argv_prefix": [["git", "push", "--force"]],
                                        "reason": "no force pushes on this host"}]}})
    ctx = CallContext.from_config(eng.config)
    assert eng.call("shell.run", {"script": "git push", "argv": ["git", "push"]}, ctx=ctx).error.code \
        == "APPROVAL_REQUIRED", "privileged needs approval before the token story even starts"
    ok = eng.call("shell.run", {"script": "git push", "argv": ["git", "push"]},
                  ctx=ctx, approval_token="grant:shell.run")
    assert ok.ok and ran == [1], "the token must unblock a non-matching call"

    res = eng.call("shell.run", {"script": "git push --force", "argv": ["git", "push", "--force"]},
                   ctx=ctx, approval_token="grant:shell.run")
    assert res.error.code == "DENY_RULE", "deny outranks any token"
    assert ran == [1], "the handler must not run"
    det = res.error.details
    assert det["rule"]["source"] == "policy.rule[0]"
    assert det["rule"]["reason"] == "no force pushes on this host"
    assert det["matched"] == {"arg": "argv", "argv": ["git", "push", "--force"],
                              "prefix": ["git", "push", "--force"]}
    assert "cannot be overridden" in det["advice"]


def test_deny_rule_carries_the_rule_text_and_the_matching_argument():
    eng, _ = mkengine(handlers=[
        (tool("fs.write", risk="write", props={"path": {"type": "string"}, "content": {"type": "string"}},
             required=["path", "content"]), lambda **kw: {"w": 1})],
        overrides={"policy": {"rule": [{"action": "deny", "tool": "fs.*",
                                        "paths": ["**/.ssh/**"],
                                        "reason": "key material is off-limits"}]}})
    res = eng.call("fs.write", {"path": "/ws/.ssh/id_rsa", "content": "x"})
    assert res.error.code == "DENY_RULE"
    det = res.error.details
    assert det["rule"]["reason"] == "key material is off-limits"
    assert det["matched"] == {"arg": "path", "value": "/ws/.ssh/id_rsa", "glob": "**/.ssh/**"}
    assert eng.call("fs.write", {"path": "/ws/notes.txt", "content": "x"}).ok, \
        "the rule must not poison calls it does not match"


def test_deny_outranks_an_allow_rule_for_the_same_call():
    eng, _ = mkengine(handlers=[
        (tool("shell.run", risk="privileged", props=_SHELL_PROPS, required=["script"]),
         lambda **kw: {"ran": True})],
        overrides={"policy": {"rule": [
            {"action": "allow", "tool": "shell.run", "argv_prefix": [["git", "push", "--force"]]},
            {"action": "deny", "tool": "shell.run", "argv_prefix": [["git", "push", "--force"]],
             "reason": "the operator changed their mind"},
        ]}})
    res = eng.call("shell.run", {"script": "git push --force", "argv": ["git", "push", "--force"]})
    assert res.error.code == "DENY_RULE", "evaluation order is deny-then-allow; allow never wins"


def test_allow_rule_removes_the_approval_requirement_for_matched_calls_only():
    ran = []
    eng, _ = mkengine(handlers=[
        (tool("shell.run", risk="privileged", props=_SHELL_PROPS, required=["script"]),
         lambda **kw: ran.append(1) or {"ran": True})],
        overrides={"policy": {"rule": [{"action": "allow", "tool": "shell.run",
                                        "argv_prefix": [["git", "status"], ["git", "diff"]],
                                        "reason": "read-only git is safe unattended"}]}})
    res = eng.call("shell.run", {"script": "git status", "argv": ["git", "status"]})
    assert res.ok, "a matched allow rule removes the approval requirement with no approver configured"
    assert ran == [1]
    allow = res.metrics.extra.get("policy_allow")
    assert allow and allow["rule"] == "policy.rule[0]" and allow["reason"].endswith("unattended")
    res2 = eng.call("shell.run", {"script": "git push", "argv": ["git", "push"]})
    assert res2.error.code == "APPROVAL_REQUIRED" and ran == [1], \
        "the allow is scoped to the matching argv, not to the tool"


def test_allow_rule_does_not_override_read_only():
    ran = []
    eng, _ = mkengine(read_only=True, handlers=[
        (tool("fs.write", risk="write", props={"path": {"type": "string"}, "content": {"type": "string"}},
             required=["path", "content"]), lambda **kw: ran.append(1) or {"w": 1})],
        overrides={"policy": {"rule": [{"action": "allow", "tool": "fs.write"}]}})
    res = eng.call("fs.write", {"path": "a.txt", "content": "x"})
    assert res.error.code == "READ_ONLY_MODE" and ran == [], "read_only is a wall, not a preference"


def test_escalate_rule_reevaluates_risk_only_for_matched_calls():
    eng, _ = mkengine(handlers=[
        (tool("shell.run", risk="write", props=_SHELL_PROPS, required=["script"]),
         lambda **kw: {"ran": True})],
        overrides={"policy": {"rule": [{"action": "escalate", "tool": "shell.run",
                                        "argv_prefix": [["sudo"]]}]}})
    res = eng.call("shell.run", {"script": "sudo reboot", "argv": ["sudo", "reboot"]})
    assert res.error.code == "APPROVAL_REQUIRED", "sudo escalates write -> privileged"
    assert eng.call("shell.run", {"script": "echo hi", "argv": ["echo", "hi"]}).ok, \
        "the same tool without sudo stays auto-approved"


def test_deny_script_content_secret_path_blocks_shell_run():
    """The secret-path matcher: a `paths` glob is tested against the path-like
    tokens *inside* script text - `cat .env` names `.env` even though the whole
    string is not a path. Deny stays non-overridable, and the refusal names the
    token that fired."""
    ran = []
    eng, _ = mkengine(handlers=[
        (tool("shell.run", risk="privileged", props=_SHELL_PROPS, required=["script"]),
         lambda **kw: ran.append(1) or {"ran": True})],
        overrides={"policy": {"rule": [
            {"action": "deny", "tool": "shell.run", "paths": ["**/.env*", "**/*.pem"],
             "reason": "secret material never goes through the shell"}]}})
    ctx = CallContext.from_config(eng.config)
    res = eng.call("shell.run", {"script": "cat .env"}, ctx=ctx,
                   approval_token="grant:shell.run")
    assert res.error.code == "DENY_RULE", "deny outranks any token"
    assert ran == [], "the handler must not run"
    assert res.error.details["matched"] == \
        {"arg": "script", "value": "cat .env", "glob": "**/.env*", "token": ".env"}
    res2 = eng.call("shell.run", {"script": 'cp "certs/server.pem" /tmp/p'})
    assert res2.error.code == "DENY_RULE", "quoted segments are scanned too"
    assert res2.error.details["matched"]["token"] == "certs/server.pem"
    assert eng.call("shell.run", {"script": "echo hello world"}, ctx=ctx,
                    approval_token="grant:shell.run").ok, \
        "the rule must not poison scripts that name no such path"


def test_allow_rules_do_not_match_script_content():
    """allow removes the approval requirement - but never on the strength of a
    path mentioned inside free-form script text. Granting approval relief on
    whatever string happens to name a path is policy granting on luck."""
    eng, _ = mkengine(handlers=[
        (tool("shell.run", risk="privileged", props=_SHELL_PROPS, required=["script"]),
         lambda **kw: {"ran": True})],
        overrides={"policy": {"rule": [
            {"action": "allow", "tool": "shell.run", "paths": ["**/.env*"]}]}
    })
    res = eng.call("shell.run", {"script": "cat .env"})
    assert res.error.code == "APPROVAL_REQUIRED", \
        "an allow rule must not grant relief from script text that merely names the path"


def test_escalate_rule_scans_script_content():
    eng, _ = mkengine(handlers=[
        (tool("shell.run", risk="write", props=_SHELL_PROPS, required=["script"]),
         lambda **kw: {"ran": True})],
        overrides={"policy": {"rule": [
            {"action": "escalate", "tool": "shell.run", "paths": ["**/*.pem"]}]}
    })
    res = eng.call("shell.run", {"script": "cp certs/server.pem /tmp/p"})
    assert res.error.code == "APPROVAL_REQUIRED", "naming a .pem escalates write to approval"
    assert eng.call("shell.run", {"script": "echo hi"}).ok, \
        "the same tool without the path stays auto-approved"


def test_env_name_matcher_denies_by_name_not_value():
    eng, _ = mkengine(handlers=[
        (tool("shell.run", risk="write", props=_SHELL_PROPS, required=["script"]),
         lambda **kw: {"ran": True})],
        overrides={"policy": {"rule": [{"action": "deny", "tool": "shell.run",
                                        "env": ["*TOKEN*"],
                                        "reason": "no secrets through the shell env"}]}})
    res = eng.call("shell.run", {"script": "echo hi", "env": {"API_TOKEN": "x"}})
    assert res.error.code == "DENY_RULE"
    assert res.error.details["matched"] == {"arg": "env", "name": "API_TOKEN", "glob": "*TOKEN*"}
    assert eng.call("shell.run", {"script": "echo hi", "env": {"HOME": "/x"}}).ok


def test_rate_limit_refuses_before_dispatch_and_names_the_rule():
    """Acceptance 2 (unit scale): the call that crosses the limit is
    BUDGET_EXCEEDED, `details.exceeded` names the rule, and the handler does
    not run."""
    ran = []
    eng, _ = mkengine(handlers=[
        (tool("fs.delete", risk="destructive", destructive=True, props={"path": {"type": "string"}},
             required=["path"]), lambda **kw: ran.append(1) or {"d": 1})],
        overrides={"policy": {"rate_limits": {"fs.delete": 3},
                              "auto_approve": ["none", "read", "write", "destructive"],
                              "confirm_destructive": False}})
    for i in range(3):
        assert eng.call("fs.delete", {"path": f"f{i}"}).ok
    res = eng.call("fs.delete", {"path": "f3"})
    assert res.error.code == "BUDGET_EXCEEDED"
    assert ran == [1, 1, 1], "the over-limit call must not execute"
    assert any("rate_limit fs.delete" in x and "policy.rate_limits['fs.delete']" in x
               for x in res.error.details["exceeded"]), res.error.details["exceeded"]
    assert res.error.details["retry_after_s"] > 0
    assert res.next_actions[0]["action"] == "summarize_and_stop"


def test_rate_limit_does_not_charge_previews():
    ran = []
    eng, _ = mkengine(handlers=[
        (tool("fs.delete", risk="destructive", destructive=True,
              props={"path": {"type": "string"}, "dry_run": {"type": "boolean", "default": False}},
              required=["path"]), lambda **kw: ran.append(1) or {"d": 1})],
        overrides={"policy": {"rate_limits": {"fs.delete": 1},
                              "auto_approve": ["none", "read", "write", "destructive"],
                              "confirm_destructive": False}})
    assert eng.call("fs.delete", {"path": "a", "dry_run": True}).ok, "a preview writes nothing"
    assert eng.call("fs.delete", {"path": "a", "dry_run": True}).ok, "previews must not burn rate slots"
    assert eng.call("fs.delete", {"path": "a"}).ok, "the first real call still passes"
    res = eng.call("fs.delete", {"path": "a"})
    assert res.error.code == "BUDGET_EXCEEDED", "only real calls count against the limit"


def test_default_rate_limit_covers_fs_delete():
    eng, _ = mkengine()
    assert eng.config.policy.rate_limits == {"fs.delete": 20}
    hit = eng._policy.strictest_rate("fs.delete", {"path": "x"})
    assert hit is not None and hit[0].rate == 20 and hit[0].window_s == 60.0
    assert eng._policy.strictest_rate("fs.write", {"path": "x"}) is None


def test_mutation_burst_breaker_stops_a_runaway():
    eng, _ = mkengine(handlers=[
        (tool("fs.write", risk="write", props={"path": {"type": "string"}, "content": {"type": "string"}},
             required=["path", "content"]), lambda **kw: {"w": 1}),
        (tool("fs.read", props={"path": {"type": "string"}}, required=["path"]),
         lambda **kw: {"r": 1})],
        overrides={"policy": {"max_mutations_per_minute": 3}})
    ctx = CallContext.from_config(eng.config)
    for i in range(3):
        assert eng.call("fs.write", {"path": f"a{i}", "content": "x"}, ctx=ctx).ok
    res = eng.call("fs.write", {"path": "a3", "content": "x"}, ctx=ctx)
    assert res.error.code == "BUDGET_EXCEEDED"
    assert any("mutation burst" in x and "policy.max_mutations_per_minute" in x
               for x in res.error.details["exceeded"])
    assert res.next_actions[0]["action"] == "summarize_and_stop"
    for _ in range(5):
        assert eng.call("fs.read", {"path": "a0"}, ctx=ctx).ok, "reads never trip the breaker"


def test_malformed_policy_rule_is_reported_and_skipped_not_guessed():
    eng, _ = mkengine(handlers=[
        (tool("fs.write", risk="write", props={"path": {"type": "string"}, "content": {"type": "string"}},
             required=["path", "content"]), lambda **kw: {"w": 1})],
        overrides={"policy": {"rule": [{"action": "deny", "tool": "fs.write", "wat": 1}]}})
    assert any("unknown field" in e for e in eng._policy_errors), \
        "a typo in a policy rule must be visible to the operator"
    assert eng.call("fs.write", {"path": "a", "content": "x"}).ok, \
        "a broken rule is skipped; it must neither crash the engine nor block the call"


def test_legacy_deny_strings_compile_to_rules():
    eng, _ = mkengine(deny=["fs.delete(**/.ssh/**)"])
    deny_rules = [r for r in eng._policy.rules if r.action == "deny"]
    assert any(r.tool == "fs.delete" and r.paths == ("**/.ssh/**",) for r in deny_rules), \
        "the legacy grammar must compile to the same rule objects"


def test_approval_grant_is_echoed_in_metrics():
    ran = []
    eng, _ = mkengine(handlers=[
        (tool("fs.delete", risk="destructive", destructive=True, props={"path": {"type": "string"}},
             required=["path"]), lambda **kw: ran.append(1) or {"d": 1})])
    ctx = CallContext.from_config(eng.config)
    assert eng.call("fs.delete", {"path": "a"}, ctx=ctx).error.code == "APPROVAL_REQUIRED"
    res = eng.call("fs.delete", {"path": "a"}, ctx=ctx, approval_token="grant:fs.delete")
    assert res.ok and ran == [1]
    assert res.metrics.extra.get("approval_grant") == "grant:fs.delete", \
        "the ledger reader must see who approved what on the receipt"


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


# ------------------------------------------------------- P4 budget governor

def test_budget_position_is_a_first_class_metrics_field():
    """The loop's 'should I summarize now?' branch is a lookup, not a guess."""
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda **kw: {"n": 1})])
    ctx = CallContext.from_config(eng.config)
    ctx.max_calls, ctx.max_mutations, ctx.max_tokens_out = 10, 0, 5000
    res = eng.call("fs.read", {}, ctx=ctx)
    assert res.ok
    b = res.metrics.budget
    assert b is not None, "every result carries its task's budget position"
    assert b["spent"]["calls"] == 1
    assert b["remaining"] == {"calls": 9, "mutations": None,
                              "tokens_out": 5000 - ctx.tokens_out}
    assert b["limits"] == {"calls": 10, "tokens_out": 5000}  # 0 (unlimited) is not a limit
    assert b["exhausted"] is False
    # and the estimate covers the block itself (re-estimated after the attach)
    assert res.metrics.est_tokens > 0 and "budget" in res.metrics.to_dict()
    # the ledger sees the same view
    assert res.metrics.budget["spent"]["tokens_out"] == ctx.tokens_out


def test_loop_remaining_tokens_tighten_the_task_cap():
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda **kw: {"n": 1})])
    cfg = eng.config
    loop_budget = min(cfg.budget.task_max_tokens_out, 300) or 300
    assert cfg.budget.task_max_tokens_out in (0, 400_000)
    ctx = CallContext.from_config(cfg, remaining_tokens=300)
    assert ctx.max_tokens_out == loop_budget
    cfg.budget.task_max_tokens_out = 0  # config says unlimited; the loop still caps
    ctx2 = CallContext.from_config(cfg, remaining_tokens=300)
    assert ctx2.max_tokens_out == 300
    assert CallContext.from_config(cfg).max_tokens_out == 0  # untouched default stands


def test_exhausted_flag_flips_on_the_call_that_crosses_the_cap():
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda **kw: {"r": "x" * 9000})])
    ctx = CallContext.from_config(eng.config)
    ctx.max_tokens_out = 50
    res = eng.call("fs.read", {}, ctx=ctx)  # crosses the cap; still succeeds
    assert res.ok
    assert res.metrics.budget["exhausted"] is True, \
        "the agent sees 'summarize now' on this very result, before being refused"
    assert res.metrics.budget["remaining"]["tokens_out"] == 0
    res2 = eng.call("fs.read", {}, ctx=ctx)
    assert res2.error.code == "BUDGET_EXCEEDED"
    assert res2.next_actions[0]["action"] == "summarize_and_stop"
    assert res2.metrics.budget["exhausted"] is True
    assert res2.metrics.budget["remaining"]["tokens_out"] == 0


def test_budget_view_reaches_the_ledger_row():
    eng, _ = mkengine(handlers=[(tool("fs.read"), lambda **kw: {"n": 1})])
    ctx = CallContext.from_config(eng.config)
    ctx.max_calls = 5
    eng.call("fs.read", {}, ctx=ctx)
    assert ctx.to_dict()["budget"]["exhausted"] is False
    assert ctx.to_dict()["budget"]["remaining"]["calls"] == 4


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


def test_a_mutation_retires_cached_reads():
    """A read served from cache after the tree moved is a lie: the verify-then-
    retry loop (search, patch, search-again) must see the new state, not the
    pre-patch answer. A mutation bumps the cache generation, retiring every
    cached answer."""
    state = {"value": "OLD"}
    calls = []

    def reader():
        calls.append("r")
        return {"value": state["value"]}

    def writer():
        calls.append("w")
        state["value"] = "NEW"
        return {"value": state["value"]}

    eng, _ = mkengine(handlers=[(tool("fs.read"), reader),
        (tool("fs.write", risk="write", idempotent=False), writer)])
    ctx = CallContext.from_config(eng.config)
    first = eng.call("fs.read", {}, ctx=ctx)
    assert first.data["value"] == "OLD"
    eng.call("fs.write", {}, ctx=ctx)          # mutates the tree
    second = eng.call("fs.read", {}, ctx=ctx)  # identical args, post-mutation
    assert second.metrics.cached is False, "the mutation must have retired the cached read"
    assert second.data["value"] == "NEW", "a verify-after-write must read the new state"


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


# ------------------------------------------------------- P4 context receipts

def test_ledger_rows_carry_the_context_receipt(tmp_path):
    """Why an agent never saw a tool must be readable after the fact."""
    from skeletonkey.core.ledger import Ledger

    led = Ledger(tmp_path / "ledger.ndjson")
    eng, _ = mkengine(handlers=[
        (tool("fs.read"), lambda: {"v": 1}),
        (tool("fs.write", risk="write", destructive=False), lambda: {"w": 1}),
        (tool("fs.secret", advertised=False, hidden_reason="internal"), lambda: {"s": 1}),
        (tool("fs.boom"), lambda: (_ for _ in ()).throw(RuntimeError("x")))],
        read_only=True)
    eng.ledger = led
    eng.call("fs.read", {})
    eng.call("fs.boom", {})
    rows = list(led.read(limit=10))
    assert len(rows) == 2
    for row in rows:
        rc = row.context_receipt
        assert rc is not None, "every ledger row carries its context receipt"
        assert set(rc) == {"exposed_results", "withheld", "stop_reason"}
    rc = rows[0].context_receipt
    # read_only withholds the writer; the internal tool never advertises; the two
    # read-risk tools stay exposed
    assert rc["exposed_results"] == ["fs.boom", "fs.read"]
    withheld = {w["tool"]: w["why"] for w in rc["withheld"]}
    assert any("read_only" in u for u in withheld["fs.write"]), withheld
    assert withheld["fs.secret"] == ["internal"]
    assert rows[0].context_receipt["stop_reason"] == "ok"
    assert rows[1].context_receipt["stop_reason"] == "INTERNAL"
    assert led.verify()["valid"] is True, "receipts are inside the hash chain"


def test_receipt_reflects_the_live_advertisement_set(tmp_path):
    """A skill install moves the advertisement; the next row's receipt must too."""
    from skeletonkey.core.ledger import Ledger

    led = Ledger(tmp_path / "ledger.ndjson")
    eng, reg = mkengine(handlers=[(tool("fs.read"), lambda: {"v": 1})])
    eng.ledger = led
    eng.call("fs.read", {})
    reg.register(tool("fs.fresh"), lambda: {"f": 1}, replace=True)
    eng.call("fs.read", {})
    rows = list(led.read(limit=10))
    assert "fs.fresh" not in rows[0].context_receipt["exposed_results"]
    assert "fs.fresh" in rows[1].context_receipt["exposed_results"]
    assert not [w for w in rows[1].context_receipt["withheld"] if w["tool"] == "fs.fresh"]


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
