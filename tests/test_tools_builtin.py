"""The built-in tool surface, driven through the engine the way a host drives it.

These are the contracts the autopilot actually consumes: schemas enforced, `data`
shaped as documented, failures carrying the next move, mutations reversible.
Where a test looks picky about a key name, that is the point - a host pastes the
`next_call`/`undo` block straight back in, so a wrong key is a runtime failure.
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from skeletonkey.core.config import Config
from skeletonkey.core.engine import CallContext
from skeletonkey.toolkit import build

REPO_SKILLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")


def call(engine, tool, /, **args):
    """Positional-only `tool`, because some tools take an argument literally named `tool`."""
    return engine.call(tool, args)


@pytest.fixture
def writable_toolkit(workspace):
    """Same workspace, but destructive calls are auto-approved and skills are real.

    Separate from `toolkit` so that the default (locked) policy stays under test
    elsewhere instead of being quietly relaxed by every test that deletes a file.
    """
    cfg = Config.load(cwd=str(workspace), overrides={
        "roots": [str(workspace)],
        "state": {"dir": str(workspace / ".sk")},
        "shell": {"tempdir": str(workspace / ".sk" / "shell")},
        "skills": {"dirs": [REPO_SKILLS]},
        "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                   "confirm_destructive": False},
        "log_level": "ERROR",
    })
    tk = build(config=cfg)
    try:
        yield tk
    finally:
        tk.close()


# ------------------------------------------------------------------ fs.read
def test_fs_read_paging_contract(writable_toolkit):
    eng = writable_toolkit.engine
    r = call(eng, "fs.read", path="src/pkg/mod.py", limit_lines=3)
    assert r.ok, r.error
    d = r.data
    assert d["content"].startswith("PORT = 8080")
    assert d["lines"] == 3 and d["bytes"] > 0
    assert d["truncated"] is True and d["next_offset"] == 3
    assert "total_lines=10" in " ".join(d.get("notes", [])), "the host needs the total to plan the loop"
    assert len(d["sha256"]) == 16
    # the tool tells the host exactly how to continue, in its own vocabulary
    assert d["next_call"] == {"tool": "fs.read",
                             "args": {"path": "src/pkg/mod.py", "offset": 3, "limit_lines": 3}}
    assert d["read_hint"]
    nxt = call(eng, "fs.read", **d["next_call"]["args"])
    assert nxt.ok and nxt.data["content"].startswith("def handler")


def test_fs_read_line_range_is_inclusive_1_based(writable_toolkit):
    r = call(writable_toolkit.engine, "fs.read", path="src/pkg/mod.py", start_line=9, end_line=10)
    assert r.ok, r.error
    assert "def __init__" in r.data["content"] and "self.port" in r.data["content"]
    assert r.data["truncated"] is False if "truncated" in r.data else True


def test_fs_read_on_a_directory_points_at_fs_list(writable_toolkit):
    r = call(writable_toolkit.engine, "fs.read", path="src/pkg")
    assert not r.ok and r.error.code == "BAD_ARGS"
    assert any(a.get("tool") == "fs.list" for a in r.next_actions)


def test_fs_read_outside_the_root_is_refused(writable_toolkit):
    r = call(writable_toolkit.engine, "fs.read", path="../../etc/passwd")
    assert not r.ok and r.error.code == "SANDBOX_VIOLATION"
    assert "root" in (r.error.message + json.dumps(r.error.details)).lower()


# ------------------------------------------------------------------ fs.write / fs.patch
def test_fs_write_reports_hashes_sizes_and_undo(writable_toolkit):
    eng = writable_toolkit.engine
    r = call(eng, "fs.write", path="out/new.txt", content="one\ntwo\n")
    assert r.ok, r.error
    assert r.data["created"] is True and r.data["bytes_after"] == 8
    assert r.data["sha_after"] and r.data["changed"] is True
    assert r.data["newline"] == "lf" and r.data["encoding"] == "utf-8"
    assert r.data["undo_token"].startswith("und_")
    target = os.path.join(str(writable_toolkit.workspace), "out", "new.txt")
    assert os.path.exists(target), "create_dirs must have made the parent"


def test_fs_write_refuses_to_clobber_without_overwrite(writable_toolkit):
    eng = writable_toolkit.engine
    ws = str(writable_toolkit.workspace)
    call(eng, "fs.write", path="dup.txt", content="original\n")
    r = call(eng, "fs.write", path="dup.txt", content="replacement\n", overwrite=False)
    assert not r.ok and r.error.code == "EEXIST"
    assert "overwrite" in r.error.hint.lower()
    assert "overwrite" in json.dumps(r.error.details)  # advice, not just a message
    with open(os.path.join(ws, "dup.txt"), encoding="utf-8") as fh:
        assert fh.read() == "original\n", "a refusal must not have written anything"


def test_fs_write_stale_expect_sha_is_a_conflict_not_a_maybe(writable_toolkit):
    eng = writable_toolkit.engine
    call(eng, "fs.write", path="racy.txt", content="mine\n")
    r = call(eng, "fs.write", path="racy.txt", content="theirs\n", expect_sha="0" * 64)
    assert not r.ok and r.error.code == "CONFLICT"
    assert r.error.details["actual_sha"] != "0" * 64
    with open(os.path.join(str(writable_toolkit.workspace), "racy.txt"), encoding="utf-8") as fh:
        assert fh.read() == "mine\n"


def test_fs_patch_applies_diffs_and_undoes(writable_toolkit):
    eng = writable_toolkit.engine
    ws = str(writable_toolkit.workspace)
    call(eng, "fs.write", path="p.py", content="PORT = 1\nSERVE = True\n")
    read = call(eng, "fs.read", path="p.py")
    r = call(eng, "fs.patch", path="p.py",
             edits=[{"old_text": "PORT = 1", "new_text": "PORT = 8080"}],
             expect_sha=read.data["sha256"])
    assert r.ok, r.error
    assert r.data["applied"] == 1 and r.data["failed"] == []
    assert r.data["edits"][0]["strategy"] == "exact"
    assert "-PORT = 1" in r.data["unified_diff"] and "+PORT = 8080" in r.data["unified_diff"]
    assert r.data["write"]["undo_token"].startswith("und_")
    assert "PORT = 8080" in (pathlib.Path(ws) / "p.py").read_text(encoding="utf-8")

    undo = call(eng, "fs.undo", token=r.data["undo_token"])
    assert undo.ok, undo.error
    assert "PORT = 1" in (pathlib.Path(ws) / "p.py").read_text(encoding="utf-8")


def test_patch_result_carries_the_undo_token_at_top_level(writable_toolkit):
    """`data.undo_token` must exist - hosts copy it into fs.undo without reading docs."""
    eng = writable_toolkit.engine
    call(eng, "fs.write", path="t.py", content="a = 1\n")
    r = call(eng, "fs.patch", path="t.py", edits=[{"old_text": "a = 1", "new_text": "a = 2"}])
    assert r.ok and r.data["undo_token"] == r.data["write"]["undo_token"]


def test_fs_patch_ambiguity_lists_the_occurrences(writable_toolkit):
    eng = writable_toolkit.engine
    call(eng, "fs.write", path="amb.py", content="x = 1\ny = 1\nz = 1\n")
    r = call(eng, "fs.patch", path="amb.py", edits=[{"old_text": "= 1", "new_text": " = 2"}])
    assert not r.ok and r.error.code == "AMBIGUOUS_MATCH"
    fail = r.error.details["failures"][0]
    assert fail["matches"] >= 2
    assert "replace_all" in r.error.hint or "replace_all" in json.dumps(r.error.details)
    fixed = call(eng, "fs.patch", path="amb.py", edits=[{"old_text": "x = 1", "new_text": "x = 2"}])
    assert fixed.ok, fixed.error


def test_fs_patch_reports_a_conflict_when_the_anchor_is_gone(writable_toolkit):
    eng = writable_toolkit.engine
    call(eng, "fs.write", path="race.py", content="a = 1\n")
    with open(os.path.join(str(writable_toolkit.workspace), "race.py"), "w", encoding="utf-8") as fh:
        fh.write("a = 999\n")
    r = call(eng, "fs.patch", path="race.py", edits=[{"old_text": "a = 1", "new_text": "a = 2"}])
    assert not r.ok and r.error.code == "PATCH_CONFLICT"
    assert "read" in r.error.hint.lower()


def test_dry_run_previews_without_writing(writable_toolkit):
    eng = writable_toolkit.engine
    call(eng, "fs.write", path="dry.py", content="keep = 1\n")
    before = (writable_toolkit.workspace / "dry.py").read_text(encoding="utf-8")
    r = eng.call("fs.patch", {"path": "dry.py",
                              "edits": [{"old_text": "keep = 1", "new_text": "keep = 2"}]},
                 dry_run=True)
    assert r.ok, r.error
    assert r.data["dry_run"] is True and r.data["applied"] == 1
    assert "keep = 2" in r.data["unified_diff"]
    after = (writable_toolkit.workspace / "dry.py").read_text(encoding="utf-8")
    assert after == before == "keep = 1\n"


# ------------------------------------------------------------------ list / glob / stat / move / delete
def test_list_and_glob_skip_ignored_paths(writable_toolkit):
    eng = writable_toolkit.engine
    listing = call(eng, "fs.list", path=".")
    assert listing.ok
    names = [e["name"] for e in listing.data["entries"]]
    assert "src" in names
    assert "node_modules" not in names, "ignore rules must prune the walk, not just the files"
    g = call(eng, "fs.glob", pattern="**/*.py")
    assert g.ok and any(h["path"].endswith("mod.py") for h in g.data["matches"])
    assert not any("node_modules" in h["path"] for h in g.data["matches"])
    assert g.data["count"] == len(g.data["matches"])


def test_stat_reports_size_mode_and_writability(writable_toolkit):
    r = call(writable_toolkit.engine, "fs.stat", path="src/pkg/mod.py")
    assert r.ok, r.error
    d = r.data
    assert d["exists"] is True and d["is_file"] is True and d["is_dir"] is False
    assert d["size"] > 0 and "mtime" in d and "mode" in d
    assert d["writable"] is True
    assert d["path"] == "src/pkg/mod.py" and os.path.isabs(d["abs"])
    assert d["root"] == str(writable_toolkit.workspace)


def test_move_and_delete_are_journaled_and_reversible(writable_toolkit):
    eng = writable_toolkit.engine
    ws = str(writable_toolkit.workspace)
    call(eng, "fs.write", path="mv/src.txt", content="payload\n")
    m = call(eng, "fs.move", src="mv/src.txt", dst="mv/dst.txt")
    assert m.ok and m.data["undo_token"], m.error
    assert not os.path.exists(os.path.join(ws, "mv", "src.txt"))
    assert os.path.exists(os.path.join(ws, "mv", "dst.txt"))
    # hosts replay exactly what the tool suggested, so the suggestion must be callable
    assert m.data.get("undo", {"args": {"token": m.data["undo_token"]}})["args"]["token"]
    back = call(eng, "fs.undo", token=m.data["undo_token"])
    assert back.ok, back.error
    assert os.path.exists(os.path.join(ws, "mv", "src.txt"))

    d = call(eng, "fs.delete", path="mv/src.txt")
    assert d.ok and d.data["undo_token"], d.error
    assert not os.path.exists(os.path.join(ws, "mv", "src.txt"))
    assert call(eng, "fs.undo", **d.data["undo"]["args"]).ok
    assert (pathlib.Path(ws) / "mv" / "src.txt").read_text(encoding="utf-8") == "payload\n"


def test_undo_accepts_either_argument_name(writable_toolkit):
    eng = writable_toolkit.engine
    w = call(eng, "fs.write", path="alias.txt", content="first\n")
    r = call(eng, "fs.undo", undo_token=w.data["undo_token"])
    assert r.ok, r.error
    assert r.data["undone"] is True
    missing = call(eng, "fs.undo", dry_run=True)
    assert not missing.ok and missing.error.code == "BAD_ARGS"


def test_delete_dir_without_recursive_is_refused_with_advice(writable_toolkit):
    r = call(writable_toolkit.engine, "fs.delete", path="src")
    assert not r.ok and r.error.code == "BAD_ARGS"
    blob = (r.error.message + json.dumps(r.error.details)).lower()
    assert "recursive" in blob and "undo" in blob


def test_mkdir_is_idempotent_and_undoes_only_empty_dirs(writable_toolkit):
    eng = writable_toolkit.engine
    ws = str(writable_toolkit.workspace)
    r = call(eng, "fs.mkdir", path="deep/a/b")
    assert r.ok and r.data["created"] is True, r.error
    assert r.data["created_dirs"][-1].endswith(os.path.join("deep", "a", "b"))
    again = call(eng, "fs.mkdir", path="deep/a/b")
    assert again.ok and again.data["created"] is False and again.data["already"] is True
    leaf = os.path.join(ws, "deep", "a", "b")
    assert os.path.isdir(leaf)
    call(eng, "fs.write", path="deep/a/b/f.txt", content="x")
    undo = call(eng, "fs.undo", token=r.data["undo_token"])
    assert undo.ok, undo.error
    assert os.path.isdir(leaf), "undo must never delete a directory that now has content"


def test_mkdir_dry_run_reports_without_creating(writable_toolkit):
    eng = writable_toolkit.engine
    r = call(eng, "fs.mkdir", path="preview/dir", dry_run=True)
    assert r.ok and r.data["dry_run"] is True
    assert not os.path.exists(os.path.join(str(writable_toolkit.workspace), "preview"))


# ------------------------------------------------------------------ fs.search
def test_search_finds_content_and_explains_zero_matches(writable_toolkit):
    eng = writable_toolkit.engine
    r = call(eng, "fs.search", pattern="handler", glob="**/*.py")
    assert r.ok, r.error
    assert any(m["path"].endswith("mod.py") for m in r.data["matches"]), \
        "rg output parsing must survive absolute paths and colons"
    assert r.data["files_matched"] >= 1 and r.data["provider"] in {"ripgrep", "python"}
    none = call(eng, "fs.search", pattern="qqqzzz_nothing")
    assert none.ok and none.data["count"] == 0
    assert none.data["zero_match_advice"], "a zero-hit search must suggest the next move"


@pytest.mark.parametrize("prefer", [None, "python"])
def test_search_regex_context_and_glob_agree_across_providers(writable_toolkit, prefer):
    eng = writable_toolkit.engine
    args = {"pattern": r"PORT\b", "regex": True, "context": 1}
    if prefer:
        args["prefer"] = prefer
    r = eng.call("fs.search", args)
    assert r.ok, r.error
    hits = {(m["path"], m["line"]) for m in r.data["matches"]}
    assert ("src/pkg/mod.py", 1) in hits
    assert r.data["total_matches"] >= 1
    located = [m for m in r.data["matches"] if m["path"].endswith("mod.py") and m["line"] == 5]
    assert ("src/pkg/mod.py", 5) in hits, "regex hits on every occurrence, not just the first"
    assert located and located[0]["before"] == ["def handler(request):"], \
        "context must be real lines, not an ignored flag"


def test_search_files_with_matches_lists_files(writable_toolkit):
    r = call(writable_toolkit.engine, "fs.search", pattern="def ", files_with_matches=True)
    assert r.ok, r.error
    paths = {m["path"] for m in r.data["matches"]}
    assert {"src/pkg/mod.py", "src/pkg/util.py"} <= paths
    assert r.data["files_matched"] == len(paths)


def test_sniff_describes_the_bytes_before_the_agent_edits_them(writable_toolkit):
    eng = writable_toolkit.engine
    d = call(eng, "fs.sniff", path="windows.txt").data
    assert d["newline"] == "crlf" and d["encoding"] == "utf-8" and d["binary"] is False
    assert d["readable_as_text"] is True and d["first_line"] == "line one"


# ------------------------------------------------------------------ undo listing
def test_journal_list_is_scoped_by_task(writable_toolkit):
    eng = writable_toolkit.engine
    task = "pytest-task"
    ctx = CallContext(task_id=task, cwd=str(writable_toolkit.workspace))
    w = eng.call("fs.write", {"path": "jl.txt", "content": "1\n"}, ctx=ctx)
    assert w.ok, w.error
    assert w.data["path"] == "jl.txt"
    r = call(eng, "fs.journal_list", task_id=task)
    assert r.ok and r.data["entries"], "the write must be attributable to the task"
    assert all(e["task_id"] == task for e in r.data["entries"])
    assert {"entries", "summary"} <= set(r.data)
    assert r.data["summary"]["by_action"]["create"] >= 1


def test_undo_task_rewinds_a_whole_turn(writable_toolkit):
    eng = writable_toolkit.engine
    ws = str(writable_toolkit.workspace)
    ctx = CallContext(task_id="turn7", cwd=ws)
    for name, body in (("u1.txt", "one\n"), ("u2.txt", "two\n")):
        assert eng.call("fs.write", {"path": name, "content": body}, ctx=ctx).ok
    plan = eng.call("fs.undo_task", {"task_id": "turn7", "dry_run": True}, ctx=ctx)
    assert plan.ok and plan.data["dry_run"] is True
    assert os.path.exists(os.path.join(ws, "u1.txt")), "a dry run must not touch the tree"
    done = eng.call("fs.undo_task", {"task_id": "turn7"}, ctx=ctx)
    assert done.ok and done.data["undone"] == 2, done.error
    assert not os.path.exists(os.path.join(ws, "u1.txt"))
    assert not os.path.exists(os.path.join(ws, "u2.txt"))


# ------------------------------------------------------------------ shell tools
def test_shell_run_keep_script_leaves_a_replayable_artifact(writable_toolkit, bash_available):
    r = call(writable_toolkit.engine, "shell.run", script="exit 7", dialect="bash", keep_script=True)
    path = r.data.get("script_path")
    assert path and os.path.exists(path), "keep_script must hand back the bytes that ran"
    # it is inside the workspace root, so the agent can read it with our own tools
    back = call(writable_toolkit.engine, "fs.read", path=os.path.relpath(path, writable_toolkit
                                                                         .config.workspace))
    assert back.ok and "<<<SK1|" in back.data["content"]
    os.unlink(path)


def test_shell_run_without_keep_script_exposes_no_path(writable_toolkit, bash_available):
    r = call(writable_toolkit.engine, "shell.run", script="echo hi", dialect="bash")
    assert "script_path" not in r.data, "a path to a deleted file is noise"


def test_shell_run_reports_a_structured_result(writable_toolkit, bash_available):
    r = call(writable_toolkit.engine, "shell.run", script="echo hi-from-toolkit", dialect="bash")
    assert r.ok, r.error
    d = r.data
    assert d["stdout"].strip() == "hi-from-toolkit"
    assert d["exit_code"] == 0 and d["completed"] is True and d["timed_out"] is False
    assert d["dialect"] == "bash" and d["duration_ms"] >= 0
    assert d["stdout_lines"] == 1


def test_shell_run_failure_keeps_stdout_as_evidence(writable_toolkit, bash_available):
    r = call(writable_toolkit.engine, "shell.run", script="echo partial; exit 4", dialect="bash")
    assert not r.ok and r.error.code == "NONZERO_EXIT"
    assert r.error.details["exit_code"] == 4
    assert "partial" in r.error.details["stdout_tail"]
    assert "stderr_tail" in r.error.hint.lower()


def test_shell_run_rejects_a_cwd_outside_the_sandbox(writable_toolkit, bash_available):
    r = call(writable_toolkit.engine, "shell.run", script="pwd", cwd="/etc", dialect="bash")
    assert not r.ok and r.error.code == "SANDBOX_VIOLATION"


def test_shell_run_env_and_clean_mode(writable_toolkit, bash_available):
    eng = writable_toolkit.engine
    r = call(eng, "shell.run", script="echo $SK_GREETING", dialect="bash",
             env={"SK_GREETING": "hello"})
    assert r.ok and r.data["stdout"].strip() == "hello"
    leak = call(eng, "shell.run", script="echo [${SK_GREETING}]", dialect="bash", env_mode="clean")
    assert leak.ok and leak.data["stdout"].strip() == "[]"


def test_background_job_round_trip(writable_toolkit, bash_available):
    eng = writable_toolkit.engine
    started = call(eng, "shell.run", script="sleep 0.1; echo bg-done", dialect="bash", background=True)
    assert started.ok, started.error
    job_id = started.data["job_id"]
    assert job_id
    wait = call(eng, "shell.job_wait", job_id=job_id, timeout_s=30)
    assert wait.ok, wait.error
    assert "bg-done" in wait.data["stdout_tail"]
    assert wait.data["running"] is False and wait.data["exit_code"] == 0
    jobs = call(eng, "shell.jobs")
    assert jobs.ok and any(j["job_id"] == job_id for j in jobs.data["jobs"])
    running = call(eng, "shell.run", script="sleep 30", dialect="bash", background=True)
    assert running.ok, running.error
    killed = call(eng, "shell.job_kill", job_id=running.data["job_id"])
    assert killed.ok, killed.error
    assert killed.data.get("killed") is True or "killed" in json.dumps(killed.data)


def test_session_persists_cwd_and_env_through_the_tool(writable_toolkit, bash_available):
    eng = writable_toolkit.engine
    ws = writable_toolkit.workspace.as_posix()
    r1 = call(eng, "shell.run", script=f"cd {ws} && export SK_T=42", dialect="bash", session="p1")
    assert r1.ok, r1.error
    r2 = call(eng, "shell.run", script="echo SK_T=$SK_T && pwd", dialect="bash", session="p1")
    assert r2.ok and "SK_T=42" in r2.data["stdout"] and ws in r2.data["stdout"]
    listed = call(eng, "shell.sessions")
    assert listed.ok and any(s["sid"] == "p1" for s in listed.data["sessions"])
    row = next(s for s in listed.data["sessions"] if s["sid"] == "p1")
    assert row["calls"] == 2 and "SK_T" in row["env_names"]
    assert "env" not in row, "a listing tool must not hand the session environment to the host"
    assert row["env_keys"] >= 1
    assert call(eng, "shell.session_reset", session="p1").ok
    assert all(s["sid"] != "p1" for s in call(eng, "shell.sessions").data["sessions"])


def test_shell_timeout_surfaces_as_timeout_not_a_crash(writable_toolkit, bash_available):
    r = call(writable_toolkit.engine, "shell.run", script="sleep 20", dialect="bash", timeout_s=1)
    assert not r.ok and r.error.code == "TIMEOUT"
    assert r.error.retryable is True
    assert "background" in (r.error.hint + json.dumps(r.error.details)).lower()


def test_shell_available_names_dialects_without_leaking_paths(writable_toolkit):
    r = call(writable_toolkit.engine, "shell.available")
    assert r.ok, r.error
    d = r.data
    assert d["preferred"] in d["available"]
    assert "bash" in d["available"] or "pwsh" in d["available"]
    assert set(d["shells"]) >= set(d["available"])
    assert d["shells"][d["preferred"]]["usable"] is True


# ------------------------------------------------------------------ profile + registry tools
def test_profile_probe_reports_facts_the_registry_gates_on(writable_toolkit):
    r = call(writable_toolkit.engine, "profile.probe", force=True)
    assert r.ok, r.error
    d = r.data
    assert d["os"] in {"windows", "linux", "darwin"}
    assert d["shells"], "the probe must report what it looked for, not only what it found"
    assert all(v["dialect"] for v in d["shells"].values())
    assert d["fingerprint"] and "capabilities" in d
    assert d["tool_availability"]["advertised"] <= d["tool_availability"]["total"]


def test_registry_list_search_describe_stats(writable_toolkit):
    eng = writable_toolkit.engine
    lst = call(eng, "registry.list", include_schema=True, limit=100)
    assert lst.ok and lst.data["count"] >= 20
    ids = {t["id"] for t in lst.data["tools"]}
    assert {"fs.read", "fs.patch", "shell.run", "skills.load"} <= ids
    assert len(lst.data["digest"]) >= 8 and lst.data["estimated_tokens"] > 0
    fs_read = next(t for t in lst.data["tools"] if t["id"] == "fs.read")
    assert fs_read["input_schema"]["properties"]["path"]["type"] == "string"

    found = call(eng, "registry.search", query="search file contents", limit=5)
    assert found.ok and found.data["results"][0]["id"] == "fs.search"
    assert found.data["results"][0]["score"] > 0 and found.data["results"][0]["available"] is True

    desc = call(eng, "registry.describe", tool="fs.patch")
    assert desc.ok and "edits" in desc.data["input_schema"]["properties"]
    assert desc.data["risk"] == "write" and desc.data["reversible"] is True

    stats = call(eng, "registry.stats", tool="registry.list")
    assert stats.ok and stats.data["stats"]["registry.list"]["calls"] >= 1
    assert stats.data["overview"]["tools"] >= 20


def test_registry_describe_unknown_tool_suggests(writable_toolkit):
    r = call(writable_toolkit.engine, "registry.describe", tool="fs.reed")
    assert not r.ok and r.error.code == "UNKNOWN_TOOL"
    assert "fs.read" in json.dumps(r.error.details["suggested"])


def test_unknown_tool_names_are_corrected(writable_toolkit):
    r = call(writable_toolkit.engine, "fs.cheksum", path="README.md")
    assert not r.ok and r.error.code == "UNKNOWN_TOOL"
    assert any(a.get("tool") == "registry.search" for a in r.next_actions) or r.error.hint


def test_missing_and_extra_arguments_are_distinguished(writable_toolkit):
    eng = writable_toolkit.engine
    r = call(eng, "fs.read")
    assert not r.ok and r.error.code == "MISSING_ARG"
    assert r.error.details["missing"] == "path"
    bad = call(eng, "shell.run", script="echo x", dialect="bash", shell="bash")
    assert not bad.ok and bad.error.code == "BAD_ARGS"
    assert "shell" in json.dumps(bad.error.details["errors"])
    assert "minimal_example" in bad.error.details


def test_nested_validation_errors_point_at_the_item(writable_toolkit):
    r = call(writable_toolkit.engine, "fs.patch", path="README.md",
             edits=[{"old_text": "# Demo", "new_text": "# Demo2"}, {"new_text": "x"}])
    assert not r.ok and r.error.code == "MISSING_ARG"
    assert "edits[1].old_text" in r.error.message or r.error.details["at"] == "edits[1].old_text"


# ------------------------------------------------------------------ policy
def test_destructive_tool_requires_approval_by_default(toolkit, workspace):
    """The default policy must stop `fs.delete` and hand the host a replayable token."""
    eng = toolkit.engine
    (workspace / "doomed.txt").write_text("x\n", encoding="utf-8")
    r = eng.call("fs.delete", {"path": "doomed.txt"}, ctx=CallContext(cwd=str(workspace)))
    assert not r.ok and r.error.code == "APPROVAL_REQUIRED"
    prompt = r.error.details["prompt"]
    assert prompt["tool"] == "fs.delete" and prompt["risk"] == "destructive"
    assert prompt["approve_token"]
    assert "once" in prompt["grant_options"] and "task" in prompt["grant_options"]
    assert (workspace / "doomed.txt").exists(), "a refusal must not have deleted anything"

    token = prompt["approve_token"]
    ok = eng.call("fs.delete", {"path": "doomed.txt"}, ctx=CallContext(cwd=str(workspace)),
                  approval_token=token)
    assert ok.ok, ok.error
    assert not (workspace / "doomed.txt").exists()


def test_read_only_mode_plans_instead_of_writing_and_still_previews(workspace):
    cfg = Config.load(cwd=str(workspace), overrides={
        "roots": [str(workspace)], "state": {"dir": str(workspace / ".sk2")},
        "policy": {"read_only": True}, "log_level": "ERROR"})
    eng = build(config=cfg).engine
    r = eng.call("fs.write", {"path": "nope.txt", "content": "x"})
    assert not r.ok and r.error.code == "READ_ONLY_MODE"
    assert r.error.details["tool"] == "fs.write" and "advice" in r.error.details
    assert not (workspace / "nope.txt").exists()
    assert eng.call("fs.read", {"path": "README.md"}).ok, "reads stay available in read_only"
    # read_only is a *plan* mode, not a gag: the preview must still answer
    prev = eng.call("fs.write", {"path": "nope.txt", "content": "x", "dry_run": True})
    assert prev.ok, prev.error
    assert prev.data["dry_run"] is True and not (workspace / "nope.txt").exists()


# ------------------------------------------------------------------ skills tools
def test_skills_tools_discover_and_load(writable_toolkit):
    eng = writable_toolkit.engine
    lst = call(eng, "skills.list")
    assert lst.ok, lst.error
    names = [s["name"] for s in lst.data["skills"]]
    assert {"fs-safe-refactor", "shell-crossplatform"} <= set(names)
    assert lst.data["count"] == len(names) and lst.data["errors"] == []

    loaded = call(eng, "skills.load", name="fs-safe-refactor")
    assert loaded.ok, loaded.error
    d = loaded.data
    assert d["skill"] == "fs-safe-refactor"
    assert d["injection"].startswith("# skill: fs-safe-refactor")
    assert d["tokens"] > 100
    assert any(r.endswith("undo-and-journal.md") for r in d["references"])
    assert "fs.patch" in d["allowed_tools"]

    with_ref = call(eng, "skills.load", name="shell-crossplatform",
                    references=["references/clixml-and-error-format.md"])
    assert with_ref.ok, with_ref.error
    assert "CLIXML" in with_ref.data["injection"] or "errorId" in with_ref.data["injection"]


def test_skills_match_returns_an_injectable_block(writable_toolkit):
    r = call(writable_toolkit.engine, "skills.match", task="edit this file safely and undo it")
    assert r.ok, r.error
    assert "fs-safe-refactor" in r.data["block"]
    assert any(s["name"] == "fs-safe-refactor" for s in r.data["skills"])
    none = call(writable_toolkit.engine, "skills.match", task="make coffee")
    assert none.ok and none.data["skills"] == [] and none.data["block"] == ""


def test_skills_load_unknown_name_is_a_clean_error(writable_toolkit):
    r = call(writable_toolkit.engine, "skills.load", name="nope-not-here")
    assert not r.ok and r.error.code == "ENOENT"
    assert "known" in r.error.details and "did_you_mean" in r.error.details


# ------------------------------------------------------------------ envelope hygiene
@pytest.mark.parametrize("tool,args", [
    ("fs.read", {"path": "src/pkg/mod.py"}),
    ("fs.list", {"path": "."}),
    ("fs.glob", {"pattern": "**/*.py"}),
    ("fs.sniff", {"path": "README.md"}),
    ("fs.search", {"pattern": "PORT"}),
    ("shell.available", {}),
    ("registry.list", {}),
    ("profile.probe", {}),
    ("skills.list", {}),
])
def test_every_result_is_json_serialisable_and_bounded(writable_toolkit, tool, args):
    cfg = writable_toolkit.config
    r = writable_toolkit.engine.call(tool, args)
    assert r.ok, r.error
    payload = r.to_dict(max_bytes=cfg.budget.max_output_bytes, spill_dir=cfg.budget.spill_dir)
    text = json.dumps(payload)
    assert len(text.encode()) <= cfg.budget.max_output_bytes + 2000
    assert payload["ok"] is True and payload["run_id"]
    assert ("data" in payload and "error" not in payload) or payload.get("error") is None
    json.loads(text)  # no stray non-serialisable objects


def test_large_results_spill_instead_of_truncating_silently(writable_toolkit):
    call(writable_toolkit.engine, "fs.write", path="big.txt", content=("line\n" * 4000))
    r = call(writable_toolkit.engine, "fs.read", path="big.txt")
    assert r.ok, r.error
    cfg = writable_toolkit.config
    payload = r.to_dict(max_bytes=4000, spill_dir=cfg.budget.spill_dir)
    assert len(json.dumps(payload).encode()) <= 6000
    spilled = payload["data"].get("spilled") or payload.get("artifacts")
    assert spilled, "an oversized payload must spill, not be cut off mid-content"


def test_dotenv_is_denied_by_default(writable_toolkit):
    """The default deny list covers secret files; reading them is not a read-only risk."""
    r = call(writable_toolkit.engine, "fs.read", path=".env")
    assert not r.ok and r.error.code == "DENY_RULE"
    assert r.error.details["path"] == ".env"


def test_secrets_are_redacted_in_the_ledger_preview(writable_toolkit, workspace):
    """A token may reach the host that asked for it; the audit trail must not keep it."""
    eng = writable_toolkit.engine
    r = call(eng, "shell.run", script="echo API_TOKEN=sk-super-secret-value", dialect="bash")
    assert r.ok and "sk-super-secret-value" in r.data["stdout"]
    path = os.path.join(str(workspace), ".sk", "ledger.ndjson")
    assert os.path.exists(path), "every call is audited"
    corpus = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    assert "sk-super-secret-value" not in corpus, "the ledger outlives the context window"
    assert "***" in corpus
    assert "API_TOKEN" in corpus, "the label survives so an operator can trace the exposure"
    lines = [json.loads(ln) for ln in corpus.splitlines() if ln.strip()]
    assert any("secrets" in json.dumps(ln) for ln in lines)

def test_deny_dialects_denies(workspace):
    """deny_dialects once *added* to the allow list. It must subtract."""
    cfg = Config.load(cwd=str(workspace), overrides={
        "roots": [str(workspace)], "state": {"dir": str(workspace / ".sk3")},
        "shell": {"allow_dialects": ["bash", "sh", "python"], "deny_dialects": ["sh"]},
        "log_level": "ERROR"})
    eng = build(config=cfg).engine
    avail = eng.call("shell.available", {}).data
    assert "sh" not in avail["available"] and "bash" in avail["available"]
    r = eng.call("shell.run", {"script": "echo x", "dialect": "sh"})
    assert not r.ok and r.error.code == "DENY_RULE"
    assert "not in the allowed set" in r.error.message
    assert r.error.details["allowed"] == ["bash", "python"]
    assert eng.call("shell.run", {"script": "echo x", "dialect": "bash"}).ok

