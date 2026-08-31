"""P4: replay and eval harness (acceptance criteria 1, 2 and 4)."""

from __future__ import annotations

import hashlib
import json
import os

from skeletonkey.core.config import Config
from skeletonkey.core.engine import CallContext
from skeletonkey.core.replay import RunRecorder, eval_suite, load_recording, normalize, replay
from skeletonkey.toolkit import build

REFACTOR_STEPS = [
    ("fs.search", {"pattern": "OLD_NAME"}),
    ("fs.read", {"path": "src/a.py"}),
    ("fs.read", {"path": "src/b.py"}),
    ("fs.read", {"path": "src/c.py"}),
    ("fs.patch", {"path": "src/a.py",
                  "edits": [{"old_text": "OLD_NAME", "new_text": "NEW_NAME", "replace_all": True}]}),
    ("fs.patch", {"path": "src/b.py",
                  "edits": [{"old_text": "OLD_NAME", "new_text": "NEW_NAME", "replace_all": True}]}),
    ("fs.patch", {"path": "src/c.py",
                  "edits": [{"old_text": "OLD_NAME", "new_text": "NEW_NAME", "replace_all": True}]}),
    ("fs.search", {"pattern": "OLD_NAME"}),
    ("fs.search", {"pattern": "NEW_NAME"}),
    ("fs.list", {"path": "src"}),
    ("fs.journal_list", {}),
    ("fs.glob", {"pattern": "src/*.py", "sort": "name"}),  # pinned order: the diff asserts it
]


def _tree_digest(root: str) -> str:
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in sorted(os.walk(root)):
        dirnames.sort()
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            h.update(os.path.relpath(p, root).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def _make_ws(ws: os.PathLike) -> None:
    (ws / "src").mkdir(parents=True)
    for name in ("a.py", "b.py", "c.py"):
        (ws / "src" / name).write_text(
            "VALUE = 'OLD_NAME'\nprint(OLD_NAME)\n", encoding="utf-8")


def test_replay_reproduces_a_recorded_12_step_refactor(tmp_path):
    """Acceptance 1: same data for every step except timestamps/run_id."""
    ws = tmp_path / "orig"
    _make_ws(ws)
    cfg = Config.load(cwd=str(ws), overrides={
        "roots": [str(ws)], "state": {"dir": str(ws / ".sk")}, "log_level": "ERROR"})
    tk = build(config=cfg)
    rec_path = tmp_path / "refactor.jsonl"
    ctx = CallContext.from_config(cfg, task_id="rename-12")
    with RunRecorder(rec_path, meta={"task_id": "rename-12",
                                     "workspace": str(ws), "state_dir": str(ws / ".sk")}) as rec:
        assert len(REFACTOR_STEPS) == 12
        for tool, args in REFACTOR_STEPS:
            res = tk.engine.call(tool, args, ctx=ctx)
            assert res.ok, res.error
            rec.record(tool, args, res)
    tk.close()

    before = _tree_digest(str(ws))  # the recording run mutated orig; replay must not
    report = replay(rec_path)
    after = _tree_digest(str(ws))
    assert before == after, "replay runs in a scratch copy; the original is untouched"
    assert report["ok"], report
    assert len(report["steps"]) == 12
    assert all(s["match"] for s in report["steps"])
    assert [s["tool"] for s in report["steps"]] == [t for t, _ in REFACTOR_STEPS]
    # acceptance 2, asserted across the replay: exactly one ledger row per call
    assert report["ledger_rows"] == 12 and report["ledger_one_row_per_call"]
    assert os.path.isdir(report["scratch"])
    # and the scratch copy actually carries the refactored names
    with open(os.path.join(report["scratch"], "ws", "src", "a.py"), encoding="utf-8") as fh:
        assert "NEW_NAME" in fh.read()


def test_replay_detects_a_divergent_step(tmp_path):
    ws = tmp_path / "orig"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("VALUE = 'OLD_NAME'\n", encoding="utf-8")
    cfg = Config.load(cwd=str(ws), overrides={
        "roots": [str(ws)], "state": {"dir": str(ws / ".sk")}, "log_level": "ERROR"})
    tk = build(config=cfg)
    rec_path = tmp_path / "two.jsonl"
    ctx = CallContext.from_config(cfg, task_id="two")
    with RunRecorder(rec_path, meta={"task_id": "two", "workspace": str(ws),
                                     "state_dir": str(ws / ".sk")}) as rec:
        for tool, args in [("fs.write", {"path": "src/b.py", "content": "x = 1\n"}),
                           ("fs.read", {"path": "src/b.py"})]:
            res = tk.engine.call(tool, args, ctx=ctx)
            assert res.ok
            rec.record(tool, args, res)
    tk.close()

    meta, steps = load_recording(rec_path)
    steps[1]["result"]["data"]["content"] = "x = 999\n"  # tamper the recording
    with open(rec_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": meta}) + "\n")
        for s in steps:
            fh.write(json.dumps(s) + "\n")

    report = replay(rec_path)
    assert not report["ok"]
    bad = [r for r in report["steps"] if not r["match"]]
    assert len(bad) == 1 and bad[0]["tool"] == "fs.read"
    assert any("content" in d for d in bad[0]["diffs"]), bad[0]["diffs"]


def test_replay_stateful_tools_are_compared_on_ok_and_error_code(tmp_path):
    """A stateful tool's live data may differ; its ok/error code may not."""
    ws = tmp_path / "orig"
    ws.mkdir()
    cfg = Config.load(cwd=str(ws), overrides={
        "roots": [str(ws)], "state": {"dir": str(ws / ".sk")}, "log_level": "ERROR"})
    tk = build(config=cfg)
    rec_path = tmp_path / "stateful.jsonl"
    ctx = CallContext.from_config(cfg, task_id="st")
    with RunRecorder(rec_path, meta={"task_id": "st", "workspace": str(ws),
                                     "state_dir": str(ws / ".sk")}) as rec:
        res = tk.engine.call("registry.stats", {}, ctx=ctx)
        assert res.ok
        rec.record("registry.stats", {}, res)
        # diverge the live state: a real call lands in the recording-side stats
        tk.engine.call("fs.read", {}, ctx=ctx)
        res2 = tk.engine.call("registry.stats", {}, ctx=ctx)
        rec.record("registry.stats", {}, res2)
    tk.close()

    report = replay(rec_path)
    assert all(s["stateful"] for s in report["steps"])
    assert report["ok"], "stateful data may differ; ok + error code is the contract"


def test_normalize_is_explicit_and_not_fuzzy():
    roots = [("/ws", "<WS>"), ("/ws/.sk", "<STATE>")]
    obj = {"run_id": "r1", "ts": 1.0, "path": "/ws/src/a.py",
           "shadow": "/ws/.sk/journal/shadow/x", "token": "und_abcd1234ef56",
           "keep": "value", "nested": {"duration_ms": 3, "data": ["/ws/a", "/else/b"]}}
    out = normalize(obj, roots)
    assert "run_id" not in out and "ts" not in out
    assert out["path"] == "<WS>/src/a.py"
    assert out["shadow"] == "<STATE>/journal/shadow/x"
    assert out["token"] == "<JOURNAL_TOKEN>"
    assert out["keep"] == "value"
    assert "duration_ms" not in out["nested"]
    assert out["nested"]["data"] == ["<WS>/a", "/else/b"]  # only declared roots are rewritten
    assert normalize({"a": 1}, roots) == {"a": 1}


def test_eval_suite_scores_a_scripted_suite(tmp_path):
    suite = tmp_path / "suite.jsonl"
    tasks = [
        {"id": "rename-symbol", "task": "rename a symbol",
         "setup": {"src/a.py": "X = OLD\n"},
         "steps": [{"tool": "fs.patch", "args": {"path": "src/a.py",
                                                 "edits": [{"old_text": "OLD", "new_text": "NEW"}]}}],
         "expect": {"ok": True, "no_warnings": True}},
        {"id": "read-verify", "task": "read and verify",
         "setup": {"src/a.py": "PORT = 8080\n"},
         "steps": [{"tool": "fs.read", "args": {"path": "src/a.py"}}],
         "expect": {"ok": True, "data": {"content": "PORT = 8080\n", "newline": "lf"},
                    "no_warnings": True}},
        {"id": "refused-then-recovered", "task": "hit a refusal, recover",
         "setup": {"src/a.py": "1\n"},
         "steps": [{"tool": "fs.read", "args": {"path": "missing.txt"}},
                   {"tool": "fs.read", "args": {"path": "src/a.py"}}],
         "expect": {"ok": True, "no_warnings": True}},
    ]
    with open(suite, "w", encoding="utf-8") as fh:
        for t in tasks:
            fh.write(json.dumps(t) + "\n")
    rep = eval_suite([str(suite)])
    assert rep["tasks"] == 3 and rep["passed"] == 3
    assert rep["refusals"] == 1 and rep["refusal_then_recovery"] == 1
    by_id = {r["id"]: r for r in rep["results"]}
    assert by_id["refused-then-recovered"]["recovered"]
    assert by_id["refused-then-recovered"]["refused"] == ["fs.read:ENOENT"]
    assert rep["median_calls_per_task"] == 1


def test_eval_suite_reports_a_failing_expectation(tmp_path):
    suite = tmp_path / "suite.jsonl"
    with open(suite, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "id": "wrong-data", "task": "expect the wrong content",
            "setup": {"src/a.py": "A\n"},
            "steps": [{"tool": "fs.read", "args": {"path": "src/a.py"}}],
            "expect": {"ok": True, "data": {"content": "B\n"}}}) + "\n")
        fh.write(json.dumps({
            "id": "failed-task", "task": "end on a failure",
            "setup": {},
            "steps": [{"tool": "fs.read", "args": {"path": "nope.txt"}}],
            "expect": {"ok": True}}) + "\n")
    rep = eval_suite([str(suite)])
    assert rep["passed"] == 0
    by_id = {r["id"]: r for r in rep["results"]}
    assert "data" in by_id["wrong-data"]["fail"]
    assert "ok" in by_id["failed-task"]["fail"]


def test_shipped_eval_suite_meets_the_acceptance_bar():
    """Acceptance 4: >= 20 tasks, per-task assertions, median calls/task <= 6."""
    suite = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tests", "eval", "suite.jsonl")
    rep = eval_suite([suite])
    assert rep["tasks"] >= 20
    assert rep["passed"] == rep["tasks"], [r for r in rep["results"] if not r["ok"]]
    assert rep["median_calls_per_task"] <= 6
