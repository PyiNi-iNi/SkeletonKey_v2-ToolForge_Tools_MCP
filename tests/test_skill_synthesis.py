"""P2: a skill's `tool.toml` becomes a callable tool, and an agent can author one.

Two halves. The compiler is checked as a *refuser*: every binding it accepts is one we can
explain, and every shape that would produce a callable-but-broken tool (a placeholder in a body,
a path outside the pack, a shadowed built-in, a property nobody reads) must fail at load time
with the reason an operator can act on. The runtime half is checked the way a host checks it -
through `engine.call`, in a real subprocess, over a skill written by the toolkit's own file
tools, because "the agent extended the toolset" is the claim this phase is making.
"""

from __future__ import annotations

import json
import os

import pytest

from skeletonkey.core.config import Config
from skeletonkey.skills import install as inst
from skeletonkey.skills.compiler import SkillToolError, compile_tool
from skeletonkey.toolkit import build

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
needs_bash = pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs /bin/bash")


# --------------------------------------------------------------------- helpers
def call(engine, tool, /, **args):
    return engine.call(tool, args)


def compile_one(tmp_path, decl, *, script="scripts/x.sh", body=None, **kw):
    """Compile `decl` against a skill dir, creating the script it points at by default."""
    (tmp_path / "scripts").mkdir(exist_ok=True)
    if script:
        (tmp_path / script).write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    if decl.get("handler_body") or decl.get("handler_script"):
        full = {"name": "probe"}                 # the caller says what the handler is
    elif body:
        full = {"name": "probe", "handler_body": body}
    else:
        full = {"name": "probe", "handler_script": script}
    full.update(decl)
    return compile_tool("demo", str(tmp_path), full, **kw)


@pytest.fixture
def repo_toolkit(workspace):
    """The toolkit with the *shipped* skill packs on its path, writes auto-approved."""
    cfg = Config.load(cwd=str(workspace), overrides={
        "roots": [str(workspace)], "state": {"dir": str(workspace / ".sk")},
        "shell": {"tempdir": str(workspace / ".sk" / "shell")},
        "skills": {"dirs": [os.path.join(REPO, "skills")]},
        "policy": {"auto_approve": ["none", "read", "write"], "confirm_destructive": False},
        "log_level": "ERROR"})
    tk = build(config=cfg)
    try:
        yield tk
    finally:
        tk.close()


def expect_refusal(tmp_path, decl, *, needle, handler="body", **kw):
    """Refuse `decl`. `handler` says how to make it reach the rule under test at all: a
    declaration with no handler is refused for that reason first, and the message we are checking
    for would never surface."""
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts" / "x.sh").write_text("true\n", encoding="utf-8")
    full = {"name": "probe"}
    if handler == "body" and not (decl.get("handler_body") or decl.get("handler_script")):
        full["handler_body"] = "true\n"
    elif handler == "script" and not (decl.get("handler_body") or decl.get("handler_script")):
        full["handler_script"] = "scripts/x.sh"
    full.update(decl)
    with pytest.raises(SkillToolError) as exc:
        compile_tool("demo", str(tmp_path), full, **kw)
    assert needle in str(exc.value).lower(), str(exc.value)
    return exc.value


# --------------------------------------------------------------------- the bindings
def test_flags_channel_puts_values_in_argv_not_in_the_script(tmp_path):
    man, b = compile_one(tmp_path, {"args_via": "flags",
                                    "input_schema": {"type": "object", "properties": {
                                        "path": {"type": "string"}, "re": {"type": "boolean"},
                                        "tag": {"type": "array", "items": {"type": "string"}},
                                        "count": {"type": "integer"}}},
                                    "flags": {"path": "--file"}})
    req = b.request({"path": "a; rm -rf /", "re": True, "tag": ["x", "y"], "count": 3})
    assert req["argv"] == ["--file", "a; rm -rf /", "--re", "--tag", "x", "--tag", "y",
                           "--count", "3"]
    assert req["stdin_text"] is None
    # the value never reaches the script text, so there is nothing to quote
    assert "rm -rf" not in req["script"]
    assert man["input_schema"]["properties"]["path"]["type"] == "string"


def test_argv_json_channel_is_one_element_per_call(tmp_path):
    _man, b = compile_one(tmp_path, {"args_via": "argv_json", "expects": "json",
                                     "input_schema": {"type": "object", "properties": {
                                         "path": {"type": "string"}}}})
    req = b.request({"path": "notes.txt"})
    assert len(req["argv"]) == 1
    assert json.loads(req["argv"][0]) == {"path": "notes.txt"}


def test_stdin_json_channel_sends_the_args_and_nothing_in_argv(tmp_path):
    _man, b = compile_one(tmp_path, {"args_via": "stdin_json",
                                     "input_schema": {"type": "object", "properties": {
                                         "n": {"type": "integer"}}}})
    req = b.request({"n": 7})
    assert req["argv"] is None
    assert json.loads(req["stdin_text"]) == {"n": 7}


def test_body_receives_the_positional_reference_the_dialect_actually_uses(tmp_path):
    for dialect, needle in [("bash", '"$1"'), ("pwsh", "$args[0]"), ("python", "sys.argv[1]")]:
        _man, b = compile_one(tmp_path, {"args_via": "argv_json",
                                         "handler_body": "cat $ARG_json\n"}, script=None,
                             body="cat $ARG_json\n")
        assert needle in b.script_text(dialect), (dialect, b.script_text(dialect))


def test_windows_sibling_script_is_selected_for_powershell_dialects(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "p.sh").write_text("echo posix\n", encoding="utf-8")
    (tmp_path / "scripts" / "p.ps1").write_text("Write-Output win\n", encoding="utf-8")
    _man, b = compile_tool("demo", str(tmp_path), {
        "name": "p", "handler_script": "scripts/p.sh",
        "handler_script_windows": "scripts/p.ps1"})
    assert "posix" in b.script_text("bash")
    assert "win" in b.script_text("pwsh") and "win" in b.script_text("powershell")
    assert b.to_dict()["script_windows"] is True


def test_environment_names_the_skill_and_defaults_to_clean(tmp_path):
    _man, b = compile_one(tmp_path, {})
    req = b.request({})
    assert req["env"] == {"SKELETONKEY_SKILL": "demo", "SKELETONKEY_SKILL_DIR": str(tmp_path)}
    assert req["env_mode"] == "clean", "a skill script must not inherit the operator's secrets"
    assert req["keep_script"] is False


def test_scripts_are_inlined_because_skill_dirs_sit_outside_the_sandbox(tmp_path):
    (tmp_path / "scripts").mkdir()
    outside = tmp_path / "scripts" / "x.sh"
    outside.write_text("echo from-disk\n", encoding="utf-8")
    _man, b = compile_tool("demo", str(tmp_path), {"name": "x", "handler_script": "scripts/x.sh"})
    req = b.request({})
    assert "from-disk" in req["script"]
    assert str(outside) not in req["script"], "the payload must not depend on reading the file back"


# --------------------------------------------------------------------- refusals
def test_id_may_not_shadow_a_builtin(tmp_path):
    err = expect_refusal(tmp_path, {"id": "fs.read", "handler_body": "true\n"},
                         needle="already registered", known_ids={"fs.read"})
    assert "override_builtin" in err.advice
    man, _b = compile_one(tmp_path, {"id": "fs.read", "handler_body": "true\n"},
                         known_ids={"fs.read"}, override_builtin=True)
    assert man["id"] == "fs.read"


def test_two_skills_cannot_declare_the_same_id(tmp_path):
    expect_refusal(tmp_path, {"id": "skill.a.x", "handler_body": "true\n"},
                   needle="already registered", known_ids={"skill.a.x"})


def test_placeholders_in_a_body_are_refused(tmp_path):
    err = expect_refusal(tmp_path, {"handler_body": "cat {path}\n",
                                    "input_schema": {"type": "object",
                                                     "properties": {"path": {"type": "string"}}}},
                         needle="interpolates")
    assert "argv" in err.advice


def test_a_body_may_still_use_its_own_languages_braces(tmp_path):
    # `{x}` only matters when x is a declared property; python's own f-strings are not ours
    _man, b = compile_one(tmp_path, {"args_via": "argv_json", "handler_body": "print(f'{1+1}')\n"},
                          script=None, body="print(f'{1+1}')\n")
    assert "f'{1+1}'" in b.script_text("python")


def test_the_channel_marker_and_the_channel_must_agree(tmp_path):
    expect_refusal(tmp_path, {"args_via": "flags", "handler_body": "cat $ARG_json\n"},
                   needle="but args_via = 'flags'")
    expect_refusal(tmp_path, {"args_via": "argv_json", "handler_body": "echo hi\n",
                              "input_schema": {"type": "object",
                                               "properties": {"a": {"type": "string"}}}},
                   needle="never reads the argument")


def test_paths_must_stay_inside_the_pack(tmp_path):
    expect_refusal(tmp_path, {"handler_script": "../evil.sh"}, needle="escapes the skill")
    expect_refusal(tmp_path, {"handler_script": "/etc/passwd"}, needle="relative to the skill")
    expect_refusal(tmp_path, {"handler_script": "scripts/x.c"}, needle="extension")
    expect_refusal(tmp_path, {"handler_script": "scripts/nope.sh"}, needle="not a file")


def test_a_declaration_that_is_all_thumbs_gets_an_advice_line(tmp_path):
    err = expect_refusal(tmp_path, {"args_via": "eval"}, needle="not a binding")
    assert err.to_dict() == {"skill_tool": "skill.demo.probe", "stage": "compile",
                             "error": err.reason, "advice": err.advice, "field": "args_via"}


def test_a_pinned_dialect_and_a_dialect_property_are_mutually_exclusive(tmp_path):
    # the bug this refuses: the arg would silently override the interpreter the body needs
    expect_refusal(tmp_path, {"dialect": "python", "args_via": "argv_json",
                              "input_schema": {"type": "object",
                                               "properties": {"dialect": {"type": "string"}}}},
                   needle="pinned dialect")


def test_ceiling_risk_and_type_rules(tmp_path):
    expect_refusal(tmp_path, {"destructive": True}, needle="destructive")
    expect_refusal(tmp_path, {"risk": "destructive"}, needle="above what a skill tool may claim")
    expect_refusal(tmp_path, {"expects": "yaml"}, needle="not a contract")
    expect_refusal(tmp_path, {"env_mode": "inherit_secrets"}, needle="does not exist")
    expect_refusal(tmp_path, {"args_via": "none", "input_schema": {"type": "object",
                                                                   "properties": {"x": {}}}},
                   needle="but the schema declares")
    expect_refusal(tmp_path, {"anti_patterns": [{"do": "x", "why": "y"}]},
                   needle="non-string")
    expect_refusal(tmp_path, {"handler_script": "scripts/x.sh", "handler_body": "true\n"},
                   needle="both handler_script and handler_body")
    expect_refusal(tmp_path, {}, needle="stays a declaration only", handler="none")


def test_default_identity_and_advertise_shape(tmp_path):
    man, _b = compile_one(tmp_path, {"title": "Probe", "advertised": False})
    assert man["id"] == "skill.demo.probe" and man["group"] == "skill.demo"
    assert man["risk"] == "write" and man["destructive"] is False
    assert man["advertised"] is False and "advertised = false" in man["hidden_reason"]
    assert "scripts/x.sh" in man["description"] and "flags" in man["description"]
    man2, _b2 = compile_one(tmp_path, {})
    assert man2["advertised"] is True and not man2["hidden_reason"]


# --------------------------------------------------------------------- skill tools in a live engine
@pytest.fixture
def skillws(tmp_path):
    """A workspace with its own skills root and one installable pack written by hand."""
    root = tmp_path / "ws"
    (root / "skills").mkdir(parents=True)
    pack = root / "packs" / "wordcount"
    (pack / "scripts").mkdir(parents=True)
    (pack / "SKILL.md").write_text("---\nname: wordcount\ndescription: Count words in a file.\n"
                                   "priority: 80\n---\n# wordcount\n\nUse the tool.\n",
                                   encoding="utf-8")
    (pack / "tool.toml").write_text(
        '[[tool]]\nname = "wordcount"\ntitle = "Count words"\n'
        'description = "Count the words in a file."\ncapability = "demo.wordcount"\n'
        'risk = "read"\nidempotent = true\nexpects = "json"\nargs_via = "flags"\n'
        'timeout_s = 20\nhandler_script = "scripts/wordcount.sh"\n'
        'input_schema = """\n{\n  "type": "object",\n'
        '  "properties": {"path": {"type": "string", "description": "File to count."},'
        ' "limit": {"type": "integer", "minimum": 1}},\n'
        '  "required": ["path"],\n  "additionalProperties": false\n}\n"""\n', encoding="utf-8")
    (pack / "scripts" / "wordcount.sh").write_text(
        "#!/usr/bin/env bash\nset -uo pipefail\npath=\"\"\nlimit=\"\"\n"
        "while [ $# -gt 0 ]; do\n  case \"$1\" in\n    --path) path=\"$2\"; shift 2 ;;\n"
        "    --limit) limit=\"$2\"; shift 2 ;;\n"
        "    *) echo \"wordcount: unknown argument: $1\" >&2; exit 2 ;;\n  esac\ndone\n"
        "n=$(wc -w < \"$path\" | tr -d ' ')\n"
        "printf '{\"path\":\"%s\",\"words\":%s,\"limit\":%s,\"skill\":\"%s\"}\\n' "
        "\"$path\" \"$n\" \"${limit:-null}\" \"${SKELETONKEY_SKILL:-none}\"\n", encoding="utf-8")
    (root / "notes.txt").write_text("one two three four five six\n", encoding="utf-8")
    return root


def ws_toolkit(root, *, allow_install=False, read_only=False, extra=None):
    ov = {"roots": [str(root)], "state": {"dir": str(root / ".sk")},
          "shell": {"tempdir": str(root / ".sk" / "shell")},
          "skills": {"dirs": [str(root / "skills")], "allow_install": allow_install},
          "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                     "confirm_destructive": False}, "log_level": "ERROR"}
    if read_only:
        ov["policy"] = {"read_only": True}
    ov.update(extra or {})
    cfg = Config.load(cwd=str(root), overrides=ov)
    return build(config=cfg)


@needs_bash
def test_installed_skill_tool_runs_in_a_real_subprocess(tmp_path, skillws):
    tk = ws_toolkit(skillws, allow_install=True)
    try:
        before = tk.engine.advertise().digest
        plan = call(tk.engine, "skills.install", dir=str(skillws / "packs" / "wordcount"),
                    dry_run=True)
        assert plan.ok and plan.data["installed"] is False
        assert plan.data["tools"] == ["skill.wordcount.wordcount"], plan.data["tools"]
        assert plan.data["would_run"][0]["payload_argv"] == ["--limit", "<value>",
                                                              "--path", "<value>"]
        assert not (skillws / "skills" / "wordcount").exists(), "dry_run writes nothing"

        done = call(tk.engine, "skills.install", dir=str(skillws / "packs" / "wordcount"))
        assert done.ok, done.error
        tool_id = done.data["tools"]["added"][0]
        assert tk.registry.has(tool_id), "install must register in this process"
        assert tk.engine.advertise().digest != before, "the advertisement has to move"

        res = call(tk.engine, tool_id, path="notes.txt", limit=4)
        assert res.ok, res.error
        got = res.data["result"]
        assert got == {"path": "notes.txt", "words": 6, "limit": 4, "skill": "wordcount"}
        assert res.data["args_via"] == "flags" and res.data["owner"] == "skill:wordcount"
        assert res.data["argv"] == ["--path", "notes.txt", "--limit", "4"]
    finally:
        tk.close()


@needs_bash
def test_skill_tool_survives_a_restart_and_uninstall_removes_it(tmp_path, skillws):
    tk = ws_toolkit(skillws, allow_install=True)
    try:
        done = call(tk.engine, "skills.install", dir=str(skillws / "packs" / "wordcount"))
        tool_id = done.data["tools"]["added"][0]
    finally:
        tk.close()

    tk2 = ws_toolkit(skillws)                      # gate closed again: installed tools still load
    try:
        assert tk2.registry.has(tool_id), "an installed skill must not need the install flag to load"
        assert call(tk2.engine, tool_id, path="notes.txt").data["result"]["words"] == 6
        dry = call(tk2.engine, "skills.uninstall", name="wordcount", dry_run=True)
        assert dry.ok and dry.data["uninstalled"] is False and dry.data["tools"] == [tool_id]
        out = call(tk2.engine, "skills.uninstall", name="wordcount")
        assert out.ok, out.error
        assert out.data["tools_removed"] == [tool_id]
        assert not tk2.registry.has(tool_id)
        assert (skillws / "skills").exists() and not (skillws / "skills" / "wordcount").exists()
        assert out.data["undo"]["tool"] == "fs.undo"
        undone = call(tk2.engine, "fs.undo", **out.data["undo"]["args"])
        assert undone.ok, undone.error
        assert (skillws / "skills" / "wordcount" / "tool.toml").exists(), "the delete is reversible"
    finally:
        tk2.close()


def test_install_refuses_while_the_gate_is_closed_but_answers_dry_run(tmp_path, skillws):
    tk = ws_toolkit(skillws)                        # allow_install = False
    try:
        refused = call(tk.engine, "skills.install", dir=str(skillws / "packs" / "wordcount"))
        assert not refused.ok and refused.error.code == "DENY_RULE"
        assert refused.error.details["setting"] == "skills.allow_install"
        assert refused.error.details["would_install"]["file_count"] == 3
        preview = call(tk.engine, "skills.install", dir=str(skillws / "packs" / "wordcount"),
                       dry_run=True)
        assert preview.ok and preview.data["installed"] is False
        assert not (skillws / "skills" / "wordcount").exists()
    finally:
        tk.close()


def test_git_ref_is_named_not_implemented(tmp_path, skillws):
    tk = ws_toolkit(skillws, allow_install=True)
    try:
        r = call(tk.engine, "skills.install", git_ref="git@github.com:acme/skill.git")
        assert not r.ok and r.error.code == "NOT_IMPLEMENTED"
        assert r.error.details["phase"].startswith("P6")
        assert r.next_actions[0]["tool"] == "skills.install"
    finally:
        tk.close()


def test_install_reports_what_is_not_a_skill(tmp_path, skillws):
    bogus = skillws / "packs" / "not-a-skill"
    bogus.mkdir(parents=True)
    (bogus / "README.md").write_text("nothing here\n", encoding="utf-8")
    tk = ws_toolkit(skillws, allow_install=True)
    try:
        r = call(tk.engine, "skills.install", dir=str(bogus))
        assert not r.ok and r.error.code == "BAD_ARGS"
        assert any("SKILL.md" in b for b in r.error.details["blockers"])
    finally:
        tk.close()


def test_install_skips_symlinks_and_oversized_files(tmp_path, skillws):
    pack = skillws / "packs" / "edgy"
    (pack / "scripts").mkdir(parents=True)
    (pack / "SKILL.md").write_text("---\nname: edgy\ndescription: d\n---\n# edgy\n", encoding="utf-8")
    (pack / "secret").symlink_to(skillws / ".gitignore")
    (pack / "huge.md").write_text("x" * (inst.MAX_FILE_BYTES + 10), encoding="utf-8")
    (pack / "notes.bin").write_bytes(b"\x00\x01")
    tk = ws_toolkit(skillws, allow_install=True)
    try:
        r = call(tk.engine, "skills.install", dir=str(pack), dry_run=True)
        assert r.ok, r.error
        kept = [f["path"] for f in r.data["files"]]
        assert kept == ["SKILL.md"], kept
        assert len(r.data["warnings"]) == 3 and any("symlink" in w for w in r.data["warnings"])
    finally:
        tk.close()


@needs_bash
def test_uninstall_refuses_while_a_job_from_the_skill_is_running(tmp_path, skillws, monkeypatch):
    tk = ws_toolkit(skillws, allow_install=True)
    try:
        done = call(tk.engine, "skills.install", dir=str(skillws / "packs" / "wordcount"))
        tool_id = done.data["tools"]["added"][0]
        monkeypatch.setattr(tk.shells, "jobs",
                            lambda: [{"job_id": "job_1", "running": True, "owner": "skill:wordcount"},
                                     {"job_id": "job_2", "running": True, "owner": "skill:other"}])
        r = call(tk.engine, "skills.uninstall", name="wordcount")
        assert not r.ok and r.error.code == "CONFLICT"
        assert [j["job_id"] for j in r.error.details["jobs"]] == ["job_1"]
        assert {n["tool"] for n in r.next_actions} == {"shell.job_kill", "shell.job_wait"}
        assert tk.registry.has(tool_id), "a refusal must not half-uninstall"
    finally:
        tk.close()


def test_a_broken_declaration_is_a_load_error_not_a_broken_tool(tmp_path, skillws):
    bad = skillws / "skills" / "broken"
    (bad / "scripts").mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: broken\ndescription: Broken on purpose.\n---\n"
                                  "# broken\n", encoding="utf-8")
    (bad / "tool.toml").write_text('[[tool]]\nid = "skill.broken.gone"\nhandler_script = '
                                   '"scripts/missing.sh"\n', encoding="utf-8")
    tk = ws_toolkit(skillws)
    try:
        assert not tk.registry.has("skill.broken.gone"), "no callable-but-broken tool"
        listed = call(tk.engine, "skills.list")
        errs = [e for e in listed.data["errors"] if e.get("skill_tool")]
        assert errs and errs[0]["stage"] == "compile"
        assert errs[0]["path"] == str(bad) and "missing.sh" in errs[0]["error"]
        assert tk.registry.load_errors and tk.registry.load_errors[0]["skill_tool"] == \
            "skill.broken.gone"
    finally:
        tk.close()


def test_declaration_without_a_script_stays_a_named_stub(tmp_path, skillws):
    d = skillws / "skills" / "planned"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: planned\ndescription: Not built yet.\n---\n"
                                "# planned\n", encoding="utf-8")
    (d / "tool.toml").write_text('[[tool]]\nid = "skill.planned.todo"\ndescription = "Later."\n',
                                 encoding="utf-8")
    tk = ws_toolkit(skillws)
    try:
        r = call(tk.engine, "skill.planned.todo")
        assert not r.ok and r.error.code == "NOT_IMPLEMENTED"
        assert "P2" in r.error.details["phase"] or "phase" in r.error.details
        man = tk.registry.get("skill.planned.todo")
        assert man.meta["declares"] is True
    finally:
        tk.close()


@needs_bash
def test_script_failure_keeps_the_evidence_and_the_timeout_is_honoured(tmp_path, skillws):
    d = skillws / "skills" / "slow"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: slow\ndescription: Slow and wrong.\n---\n# slow\n",
                                encoding="utf-8")
    (d / "tool.toml").write_text('[[tool]]\nname = "boom"\ntimeout_s = 0.6\n'
                                 'handler_script = "scripts/boom.sh"\n', encoding="utf-8")
    (d / "scripts" / "boom.sh").write_text("sleep 5\necho never\n", encoding="utf-8")
    tk = ws_toolkit(skillws)
    try:
        r = call(tk.engine, "skill.slow.boom")
        assert not r.ok and r.error.code == "TIMEOUT" and r.error.retryable
        assert "never" not in (r.data or {}).get("stdout", "")
        assert "timed out" in r.hints[0]
    finally:
        (d / "tool.toml").write_text('[[tool]]\nname = "boom"\n'
                                     'handler_script = "scripts/boom.sh"\n', encoding="utf-8")
        (d / "scripts" / "boom.sh").write_text('echo "bad" >&2\nexit 3\n', encoding="utf-8")
        sync = tk.sync_skills()
        assert sync["updated"] == ["skill.slow.boom"], sync
        r2 = call(tk.engine, "skill.slow.boom")
        assert not r2.ok and r2.error.code == "NONZERO_EXIT"
        assert r2.error.details["exit_code"] == 3 and "bad" in r2.error.details["stderr_tail"]
        tk.close()


@needs_bash
def test_the_engine_validates_before_the_script_runs(tmp_path, skillws):
    marker = skillws / "should-not-exist"
    d = skillws / "skills" / "writer"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: writer\ndescription: Writes.\n---\n# writer\n",
                                encoding="utf-8")
    (d / "tool.toml").write_text('[[tool]]\nname = "make"\nargs_via = "flags"\n'
                                 'handler_script = "scripts/make.sh"\ninput_schema = """\n'
                                 '{\n  "type": "object",\n  "properties": {"marker": {"type": "string"}},\n'
                                 '  "required": ["marker"],\n  "additionalProperties": false\n}\n"""\n',
                                 encoding="utf-8")
    (d / "scripts" / "make.sh").write_text('while [ $# -gt 0 ]; do case "$1" in --marker) '
                                           'touch "$2" ;; esac; shift; done\n', encoding="utf-8")
    tk = ws_toolkit(skillws)
    try:
        rel = os.path.relpath(marker, skillws).replace(os.sep, "/")
        r = call(tk.engine, "skill.writer.make", marker=rel, surprise=1)
        assert not r.ok and r.error.code == "BAD_ARGS"
        assert not marker.exists(), "an invalid call must not reach the script"
        assert "additionalProperties" in json.dumps(r.error.details)
        good = call(tk.engine, "skill.writer.make", marker=rel)
        assert good.ok, good.error
        assert marker.exists(), "the same call without the stray key does run"
    finally:
        tk.close()


def test_read_only_policy_gates_a_write_risk_skill_tool(tmp_path, skillws):
    d = skillws / "skills" / "mutating"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: mutating\ndescription: Mutates.\n---\n# mutating\n",
                                encoding="utf-8")
    (d / "tool.toml").write_text('[[tool]]\nname = "go"\nhandler_script = "scripts/go.sh"\n'
                                 'risk = "write"\n', encoding="utf-8")
    (d / "scripts").mkdir()
    (d / "scripts" / "go.sh").write_text("true\n", encoding="utf-8")
    tk = ws_toolkit(skillws, read_only=True)
    try:
        r = call(tk.engine, "skill.mutating.go")
        assert not r.ok and r.error.code == "READ_ONLY_MODE"
        assert r.error.details["tool"] == "skill.mutating.go"
        assert "cannot preview" in r.error.details["advice"], r.error.details
        # a skill tool *can* opt into previews: declaring dry_run is the author's promise that
        # the script honours it, and the engine stops guessing on its behalf
        (d / "tool.toml").write_text('[[tool]]\nname = "go"\nhandler_script = "scripts/go.sh"\n'
                                     'risk = "write"\ninput_schema = """\n{\n  "type": "object",\n'
                                     '  "properties": {"dry_run": {"type": "boolean", "default": false}},\n'
                                     '  "additionalProperties": false\n}\n"""\n', encoding="utf-8")
        tk.sync_skills()
        preview = call(tk.engine, "skill.mutating.go", dry_run=True)
        assert preview.ok, preview.error
    finally:
        tk.close()


# --------------------------------------------------------------------- the shipped skill pack
@needs_bash
def test_shipped_selftest_tool_probes_the_host(repo_toolkit):
    eng = repo_toolkit.engine
    man = repo_toolkit.registry.get("shell.selftest")
    assert man.advertised is False and "advertised = false" in man.hidden_reason
    assert man.meta["binding"]["channel"] == "none" and man.meta["skill"] == "shell-crossplatform"
    r = call(eng, "shell.selftest")
    assert r.ok, r.error
    facts = r.data["result"]
    assert facts["path_sep"] in ("/", "\\")
    assert facts["null_device"] == "/dev/null"
    assert facts["env_marker_present"] == "yes"
    # the tool runs with a clean environment: a secret of ours must not appear in the facts
    assert "SK_PROBE_SECRET" not in json.dumps(facts)


def test_shipped_selftest_tool_refuses_a_dialect_it_cannot_probe(repo_toolkit):
    # the probe scripts are bash and PowerShell, so the schema must not offer python:
    # advertising a capability the tool cannot honour is what this contract forbids
    enum = repo_toolkit.registry.get("shell.selftest").input_schema["properties"]["dialect"]["enum"]
    assert enum == ["bash", "pwsh", "powershell"]
    r = call(repo_toolkit.engine, "shell.selftest", dialect="python")
    assert not r.ok and r.error.code == "BAD_ARGS"


@needs_bash
def test_shipped_quote_check_tool_flags_hazards(repo_toolkit):
    eng = repo_toolkit.engine
    assert "shell.quote_check" in {m.id for m in eng.advertise().tools}
    r = call(eng, "shell.quote_check",
             script="cat notes.txt >nul\nif [ $x = 1 ]; then rm -rf $BUILD; fi\n")
    assert r.ok, r.error
    got = r.data["result"]
    assert got["family"] == "posix" and got["clean"] is False
    rules = {f["rule"] for f in got["findings"]}
    assert {"nul-redirect", "rm-rf-unquoted"} <= rules, rules
    assert all(f["line"] >= 1 for f in got["findings"])
    assert {n["tool"] for n in got["next_actions"]} <= {"fs.write", "shell.run"}
    for act in got["next_actions"]:            # a skill-authored next_action must be real
        assert eng.registry.has(act["tool"]), act


@needs_bash
def test_quote_check_switches_rule_sets_by_dialect(repo_toolkit):
    eng = repo_toolkit.engine
    win = call(eng, "shell.quote_check", script="Get-Content a.json | Out-File b.json",
               target_dialect="pwsh")
    assert win.ok
    rules = {f["rule"] for f in win.data["result"]["findings"]}
    assert rules == {"out-file-encoding"}, rules
    assert win.data["result"]["clean"] is False
    clean = call(eng, "shell.quote_check", script='printf "%s\\n" "$(date)"\n')
    assert clean.ok and clean.data["result"]["clean"] is True, clean.data["result"]["findings"]
    crlf = call(eng, "shell.quote_check", script="echo hi\r\n")
    assert {f["rule"] for f in crlf.data["result"]["findings"]} == {"crlf"}


# --------------------------------------------------------------------- hot reload + windows path
def test_watcher_reports_why_it_cannot_run_instead_of_crashing(tmp_path, skillws, monkeypatch):
    """PLAN P2 acceptance 5: no new mandatory dependency, and its absence is a reported state.

    `watch.available` is pinned to False so the test exercises the cannot-run *report* it is
    named for in every environment - with the `[watch]` extra installed (the `.[dev]` default)
    the unpinned call enters a real `awatch` loop with no `stop_after`, which is a hang, not a
    report. The live-watcher behaviour has its own subject; this one stays deterministic.
    """
    import asyncio

    from skeletonkey.skills import watch

    tk = ws_toolkit(skillws)
    try:
        st = watch.status(tk)
        assert st["requested"] is False and st["dirs"] == [str(skillws / "skills")]
        assert isinstance(watch.available(), bool)
        monkeypatch.setattr(watch, "available", lambda: False)
        out = asyncio.run(watch.watch_skills(tk, None))
        assert out["watching"] is False
        assert out["reason"] == "watchfiles is not installed"
        assert "[watch]" in out["install"]
    finally:
        tk.close()


def test_a_pwsh_target_builds_the_windows_payload_without_running_it(tmp_path):
    """The PowerShell half of the exit gate, asserted as text until a Windows runner exists."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "p.sh").write_text("echo posix\n", encoding="utf-8")
    (tmp_path / "scripts" / "p.ps1").write_text("Write-Output 'win'\n", encoding="utf-8")
    _man, b = compile_tool("demo", str(tmp_path), {
        "name": "p", "handler_script": "scripts/p.sh",
        "handler_script_windows": "scripts/p.ps1", "args_via": "argv_json", "expects": "json",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}})
    req = b.request({"path": "C:\\Temp\\a.txt"}, dialect="pwsh", prefer_windows=True)
    assert req["dialect"] == "pwsh" and "win" in req["script"]
    assert json.loads(req["argv"][0]) == {"path": "C:\\Temp\\a.txt"}
    assert req["env_mode"] == "clean"

    from skeletonkey.shells.base import ShellRequest, _extra_argv
    from skeletonkey.shells.dialect import RenderOptions, render

    sent = render(req["script"], shell_path="C:/Program Files/PowerShell/7/pwsh.exe",
                  shell_version=(7, 4, 2),
                  options=RenderOptions(dialect="pwsh", on_windows=True))
    assert "{script}" in sent.argv and "-NonInteractive" in " ".join(sent.argv)
    assert "Write-Output" in sent.payload
    # the runner's own two steps, asserted without a Windows host: argv is validated and then
    # appended *after* the payload path, so one JSON object stays one argument
    shell_req = ShellRequest(script=req["script"], dialect="pwsh", argv=req["argv"],
                             env=req["env"], env_mode="clean", expects="json")
    assert _extra_argv(shell_req) == [json.dumps({"path": "C:\\Temp\\a.txt"})]


# --------------------------------------------------------------------- the install planner alone
def test_planner_refuses_a_pack_already_in_place(tmp_path, skillws):
    plan = inst.plan(str(skillws / "packs" / "wordcount"),
                     skills_root=str(skillws / "packs"), engine=None, loader=None)
    assert not plan.ok and "already is the install destination" in " ".join(plan.blockers)


def test_planner_needs_a_usable_name(tmp_path, skillws):
    plan = inst.plan(str(skillws / "packs" / "wordcount"), skills_root=str(skillws / "skills"),
                     name="../escape", engine=None, loader=None)
    assert not plan.ok and "not usable" in plan.blockers[0]


def test_planner_surfaces_a_compile_error_as_a_blocker(tmp_path, skillws):
    pack = skillws / "packs" / "bad"
    (pack / "scripts").mkdir(parents=True)
    (pack / "SKILL.md").write_text("---\nname: bad\ndescription: Bad.\n---\n# bad\n",
                                   encoding="utf-8")
    (pack / "tool.toml").write_text('[[tool]]\nname = "x\nhandler_script = "scripts/x.sh"\n',
                                    encoding="utf-8")
    plan = inst.plan(str(pack), skills_root=str(skillws / "skills"), engine=None, loader=None)
    assert not plan.ok and any("TOML" in b for b in plan.blockers), plan.blockers


def test_install_and_uninstall_are_not_advertised_while_the_gate_is_closed(tmp_path, skillws):
    tk = ws_toolkit(skillws)
    try:
        ids = {m.id for m in tk.engine.advertise().tools}
        assert "skills.install" not in ids and "skills.uninstall" in ids
        man = tk.registry.get("skills.install")
        assert "allow_install" in man.hidden_reason
    finally:
        tk.close()


def test_skills_list_reports_tools_and_errors_together(tmp_path, skillws):
    tk = ws_toolkit(skillws)
    try:
        r = call(tk.engine, "skills.list")
        assert r.ok
        assert r.data["skill_tools"] == [] and r.data["errors"] == []
        assert r.data["dirs"] == [str(skillws / "skills")]
    finally:
        tk.close()


def test_match_names_its_strategy(tmp_path, skillws):
    tk = ws_toolkit(skillws)
    try:
        d = skillws / "skills" / "wordcount"
        d.mkdir()
        (d / "SKILL.md").write_text((skillws / "packs" / "wordcount" / "SKILL.md")
                                     .read_text(encoding="utf-8"), encoding="utf-8")
        tk.skills.discover(refresh=True)
        r = call(tk.engine, "skills.match", task="count the words in a file")
        assert r.ok and r.data["strategy"] == "lexical-v1"
        assert "wordcount" in json.dumps([s["name"] for s in r.data["skills"]])
    finally:
        tk.close()
