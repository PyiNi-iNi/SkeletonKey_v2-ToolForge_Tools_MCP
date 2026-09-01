"""The live.* group: Python HMR over a LiveREPL (docs/LIVE-HMR.md).

These tests pin the behaviour that makes the subsystem worth having:

* in-place code swap - held references and existing instances see new code;
* the 3-way state merge - file edits land, REPL mutations survive saves;
* transactional reloads - a broken save changes nothing and reports a line;
* the watcher is step-able (no sleeps) and debounced;
* the panel serves the frame, the scene JSON, the overlay, and POST /repl;
* the whole group obeys the engine's policy surface (read_only / deny).
"""

from __future__ import annotations

import json
import os
import socket
import textwrap
import urllib.request

import pytest

from skeletonkey.live.patcher import patch_namespace, three_way_decision
from skeletonkey.live.runtime import LiveManager, LiveProgram
from skeletonkey.live.scene import Scene
from skeletonkey.live.watcher import FileWatcher, PollBackend
from skeletonkey.toolkit import build

# ------------------------------------------------------------------ fixtures

PROG_V1 = '''color = "red"
ticks = 0


def render():
    global ticks
    ticks += 1
    canvas.rect(10, 10, 60, 30, fill=color, id="box")
    canvas.text(40, 70, f"t={ticks}", anchor="start", font_size=11, id="t")
'''

PROG_V2 = '''color = "green"
ticks = 0


def render():
    global ticks
    ticks += 1
    canvas.circle(40, 30, 20, fill=color, id="ball")
    canvas.text(40, 70, f"t={ticks}", anchor="start", font_size=11, id="t")
'''


@pytest.fixture
def live_ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "app.py").write_text(PROG_V1, encoding="utf-8")
    tk = build(overrides={"roots": [str(root)],
                          "state": {"dir": str(root / ".sk")},
                          "shell": {"tempdir": str(root / ".sk" / "shell")},
                          "log_level": "ERROR"})
    try:
        yield tk, root
    finally:
        tk.close()


def _start(tk, root, **kw):
    res = tk.engine.call("live.start", {"path": "app.py", **kw})
    assert res.ok, res.to_dict()
    return res.to_dict()["data"]


def _data(res):
    d = res.to_dict()
    assert res.ok, d.get("error")
    return d["data"]


# -------------------------------------------------------------- patcher unit


class TestPatcher:
    def test_in_place_swap_keeps_identity_and_globals(self):
        ns: dict = {}
        exec(compile("def draw():\n    return 'box'\n", "m", "exec"), ns)
        old = ns["draw"]
        scratch: dict = {}
        exec(compile("def draw():\n    return 'circle'\n", "m", "exec"), scratch)
        report, _base = patch_namespace(ns, scratch, base={}, keep=set(), module_marker="")
        assert report.patched_functions == ["draw"]
        assert ns["draw"] is old                      # identity preserved
        assert ns["draw"]() == "circle"               # behaviour replaced
        assert ns["draw"].__globals__ is ns           # globals still live

    def test_known_reference_sees_new_code(self):
        ns: dict = {}
        exec(compile("def draw():\n    return 1\nref = draw\n", "m", "exec"), ns)
        scratch: dict = {}
        exec(compile("def draw():\n    return 2\n", "m", "exec"), scratch)
        patch_namespace(ns, scratch, base={}, keep=set(), module_marker="")
        assert ns["ref"]() == 2    # the HMR property: held refs follow the patch

    def test_class_methods_patch_existing_instances(self):
        ns: dict = {}
        exec(compile("class Ship:\n    def __init__(self):\n"
                     "        self.fuel = 9\n"
                     "    def thrust(self):\n        return 1\n", "m", "exec"), ns)
        inst = ns["Ship"]()
        scratch: dict = {}
        exec(compile("class Ship:\n    def thrust(self):\n        return 2\n", "m", "exec"), scratch)
        report, _ = patch_namespace(ns, scratch, base={}, keep=set(), module_marker="")
        assert report.patched_methods == ["Ship.thrust"]
        assert inst.thrust() == 2          # existing instance, new behaviour
        assert inst.fuel == 9              # ...with its state intact

    def test_new_function_binds_live_globals(self):
        ns = {"rate": 3}
        scratch: dict = {}
        exec(compile("def read():\n    return rate\n", "m", "exec"), scratch)
        patch_namespace(ns, scratch, base={}, keep=set(), module_marker="")
        ns["rate"] = 42
        assert ns["read"]() == 42          # new def reads the LIVE namespace

    def test_three_way_decisions(self):
        assert three_way_decision("a", {}, {"a": 1}, {"a": 1}, set()) == "source"
        assert three_way_decision("a", {"a": 1}, {"a": 1}, {"a": 2}, set()) == "source"
        assert three_way_decision("a", {"a": 1}, {"a": 9}, {"a": 2}, set()) == "live"
        assert three_way_decision("a", {"a": 1}, {"a": 1}, {"a": 2}, {"a"}) == "live"
        assert three_way_decision("a", {"a": 1}, {"a": 1}, {}, set()) == "absent"

    def test_removed_function_is_dropped_removed_data_kept_if_moved(self):
        ns: dict = {}
        exec(compile("def dead():\n    pass\nmoved = []\n", "m", "exec"), ns)
        base = {"moved": []}
        ns["moved"].append("made at runtime")
        scratch: dict = {}
        exec(compile("other = 1\n", "m", "exec"), scratch)
        report, _ = patch_namespace(ns, scratch, base=base, keep=set(), module_marker="")
        assert "dead" not in ns and "dead" in report.removed
        assert ns["moved"] == ["made at runtime"] and "moved" in report.removed_kept


# ---------------------------------------------------------------- scene unit


class TestScene:
    def test_deterministic_svg(self):
        a, b = Scene(100, 80), Scene(100, 80)
        for s in (a, b):
            s.rect(1.0, 2.5, 10, 10, fill="#fff")
            s.text(5, 5, "hi <there>", id="t")
        assert a.to_svg() == b.to_svg()
        assert 'fill="#fff"' in a.to_svg() and "hi &lt;there&gt;" in a.to_svg()
        assert a.version == 2

    def test_upsert_edits_in_place(self):
        s = Scene()
        s.circle(1, 1, 5, id="sun", fill="red")
        s.circle(2, 2, 9, id="sun", fill="blue")
        assert len(s.nodes()) == 1
        n = s.nodes()[0]
        assert n["r"] == 9 and n["fill"] == "blue"

    def test_3d_projection_projects(self):
        s = Scene(200, 200)
        s.cube3d(0, 0, 0, 50, id="cube", spin=15)
        svg = s.to_svg()
        assert s.to_dict()["nodes"][0]["type"] == "cube3d"
        assert svg.count("<line") == 12                       # wireframe cube
        s.orbit(theta=1.2)
        assert s.to_svg() != svg                               # camera moved the frame

    def test_version_bumps_on_mutation(self):
        s = Scene()
        v = s.version
        s.rect(0, 0, 1, 1)
        assert s.version == v + 1


# --------------------------------------------------------------- watcher unit


class TestWatcher:
    def test_poll_detects_modify_create_delete(self, tmp_path):
        d = tmp_path / "w"
        d.mkdir()
        f = d / "a.py"
        f.write_text("x = 1\n")
        bk = PollBackend([str(d)])
        assert bk.scan() == []                       # baseline never reports
        f.write_text("x = 2\n")
        assert bk.scan() == [str(f)]
        assert bk.scan() == []                       # no phantom re-fire
        g = d / "b.py"
        g.write_text("y = 1\n")
        assert bk.scan() == [str(g)]
        g.unlink()
        assert str(g) in bk.scan()

    def test_poll_ignores_noise(self, tmp_path):
        d = tmp_path / "w"
        (d / "__pycache__").mkdir(parents=True)
        bk = PollBackend([str(d)])
        bk.scan()
        (d / "__pycache__" / "a.pyc").write_bytes(b"")
        (d / "notes.txt").write_text("nope")
        assert bk.scan() == []

    def test_watcher_thread_delivers_batches(self, tmp_path):
        import time

        d = tmp_path / "w"
        d.mkdir()
        f = d / "x.py"
        f.write_text("a = 1\n")
        got: list[list[str]] = []
        w = FileWatcher([str(f)], got.append, interval_s=0.05, debounce_s=0.02)
        w.start()
        try:
            # A write that lands before the watcher's baseline scan is not an
            # event - so keep bumping until one lands. Deterministic: poll
            # interval is 50ms, each write races nothing but the baseline.
            for i in range(2, 400):
                f.write_text(f"a = {i}\n")
                if got:
                    break
                time.sleep(0.03)
            else:
                pytest.fail("watcher thread never delivered a batch")
            assert str(f) in got[0]
        finally:
            w.stop()
        assert w.status()["backend"] in ("poll", "watchfiles")


# --------------------------------------------------------------- program unit


class TestProgram:
    def _mk(self, tmp_path, body: str) -> LiveProgram:
        p = tmp_path / "prog.py"
        p.write_text(textwrap.dedent(body), encoding="utf-8")
        prog = LiveProgram("prog", str(p))
        prog.load_initial()
        return prog

    def test_repl_eval_and_last_value(self, tmp_path):
        prog = self._mk(tmp_path, "color = 'red'\n")
        out = prog.repl("color")
        assert out["mode"] == "eval" and out["value"] == "'red'"
        prog.repl("color = 'blue'")
        # CPython convention: statements don't touch `_`; the last EXPRESSION keeps it.
        assert prog.ns["_"] == "red"
        assert prog.repl("color")["value"] == "'blue'"

    def test_repl_exec_captures_stdout(self, tmp_path):
        prog = self._mk(tmp_path, "x = 1\n")
        out = prog.repl("print('hello repl', x)")
        assert out["stdout"].strip() == "hello repl 1"

    def test_repl_error_does_not_kill_program(self, tmp_path):
        prog = self._mk(tmp_path, "x = 1\n")
        out = prog.repl("raise ValueError('boom')")
        assert out["ok"] is False and "ValueError" in out["error"]["message"]
        assert prog.repl("x")["value"] == "1"

    def test_repl_defined_functions_survive_reload(self, tmp_path):
        prog = self._mk(tmp_path, "x = 1\n")
        prog.repl("def helper():\n    return 7\n")
        path = tmp_path / "prog.py"
        path.write_text("x = 2\n", encoding="utf-8")
        prog.reload(reason="test")
        assert prog.ns["x"] == 2
        assert prog.ns["helper"]() == 7                # REPL-made def kept
        assert "helper" in prog.keep

    def test_reload_keeps_live_mutations(self, tmp_path):
        prog = self._mk(tmp_path, "count = 0\ntags = []\n")
        prog.repl("count = 41")
        prog.repl("tags.append('made live')")
        (tmp_path / "prog.py").write_text("count = 0\ntags = []\nextra = 'new'\n",
                                          encoding="utf-8")
        rep = prog.reload(reason="test")
        d = rep.to_dict()
        assert prog.ns["count"] == 41 and prog.ns["tags"] == ["made live"]
        assert set(d["preserved"]) == {"count", "tags"}
        assert prog.ns["extra"] == "new" and "extra" in d["added"]

    def test_reload_broken_source_is_transactional(self, tmp_path):
        prog = self._mk(tmp_path, "x = 1\n")
        (tmp_path / "prog.py").write_text("def broken(:\n", encoding="utf-8")
        rep = prog.reload(reason="test")
        assert rep.ok is False and rep.error["code"] == "PARSE"
        assert prog.ns["x"] == 1                        # old code untouched
        assert prog.failed_reloads == 1
        assert prog.frame_error and prog.frame_error.get("line") == 1

    def test_force_source_reclaims_a_name(self, tmp_path):
        prog = self._mk(tmp_path, "color = 'red'\n")
        prog.repl("color = 'blue'")
        (tmp_path / "prog.py").write_text("color = 'green'\n", encoding="utf-8")
        prog.reload(reason="test", force_source=["color"])
        assert prog.ns["color"] == "green"

    def test_render_hook_frame_and_overlay(self, tmp_path):
        prog = self._mk(tmp_path, "count = 0\n"
                                  "def render():\n"
                                  "    global count\n"
                                  "    count += 1\n"
                                  "    canvas.text(10, 10, f'c={count}')\n")
        assert "<text" in prog.frame_svg and prog.frame_error is None
        prog.repl("def render():\n    raise RuntimeError('paint broke')\n")
        # repl keep-lists render, then the frame render fails into the overlay
        assert prog.frame_error and "paint broke" in prog.frame_error["message"]
        assert prog.render()["error"]["code"] == "INTERNAL"

    def test_execution_guard_interrupts_a_spin(self, tmp_path):
        prog = self._mk(tmp_path, "x = 1\n")
        out = prog.repl("while True:\n    pass\n", guard_s=0.2)
        assert out["ok"] is False and out["error"]["code"] == "TIMEOUT"
        assert prog.repl("x + 1")["value"] == "2"       # program survived

    def test_snapshot_restore_round_trip(self, tmp_path):
        prog = self._mk(tmp_path, "color = 'red'\nboxes = [1]\n")
        prog.snapshot("s1")
        prog.repl("color = 'black'\nboxes.clear()\n")
        out = prog.restore("s1")
        assert set(out["restored"]) == {"color", "boxes"}
        assert prog.ns["color"] == "red" and prog.ns["boxes"] == [1]
        # restore hands ownership back: the next save may edit it
        (tmp_path / "prog.py").write_text("color = 'purple'\nboxes = [1]\n", encoding="utf-8")
        rep = prog.reload(reason="test")
        assert prog.ns["color"] == "purple" and "color" in rep.data_updated

    def test_dependency_module_hot_reload(self, tmp_path):
        dep = tmp_path / "palette.py"
        dep.write_text('SHADE = "#333"\ndef swatch():\n    return SHADE\n', encoding="utf-8")
        prog = self._mk(tmp_path, "import palette\nfrom palette import swatch\n"
                                  "def render():\n    canvas.rect(1, 1, 9, 9, fill=swatch())\n")
        assert "palette" in prog.deps
        assert prog.ns["swatch"]() == "#333"
        dep.write_text('SHADE = "#aef"\ndef swatch():\n    return SHADE\n', encoding="utf-8")
        rep = prog.reload_dependency("palette")
        assert rep.ok
        assert prog.ns["swatch"]() == "#aef"            # from-import re-pointed
        import palette as sys_mod
        assert sys_mod.SHADE == "#aef"                  # the module itself patched


# ------------------------------------------------------------ manager + tools


class TestTools:
    def test_full_hmr_story(self, live_ws):
        tk, root = live_ws
        started = _start(tk, root)
        assert started["status"]["hooks"] == ["render"]
        assert tk.live.watch_status()["watching"]

        repl = _data(tk.engine.call("live.repl", {"code": "color = 'blue'"}))
        assert repl["frame"]["error"] is None
        svg = _data(tk.engine.call("live.render", {"svg": True}))["svg"]
        # three renders have run (start, repl, this one): state ticks across mutation
        assert 'fill="blue"' in svg and "t=3" in svg

        (root / "app.py").write_text(PROG_V2, encoding="utf-8")
        rel = _data(tk.engine.call("live.reload", {}))
        assert rel["patched_functions"] == ["render"]
        assert set(rel["preserved"]) == {"color", "ticks"} or "color" in rel["preserved"]
        svg = _data(tk.engine.call("live.render", {"svg": True}))["svg"]
        assert "<circle" in svg and 'fill="blue"' in svg  # edit landed, REPL kept

    def test_patch_tool_swaps_one_name(self, live_ws):
        tk, root = live_ws
        _start(tk, root)
        out = _data(tk.engine.call("live.patch", {
            "name": "render",
            "code": "def render():\n    canvas.line(0, 0, 99, 99, stroke=color, id='beam')\n"}))
        assert out["patched_functions"] == ["render"]
        assert "<line" in tk.live.get(None).frame_svg

    def test_state_view_ownership(self, live_ws):
        tk, root = live_ws
        _start(tk, root)
        _data(tk.engine.call("live.repl", {"code": "color = 'blue'; made = 5"}))
        names = _data(tk.engine.call("live.state", {}))["names"]
        assert names["color"]["owned_by"] == "repl"     # source name, moved live
        assert names["made"]["owned_by"] == "repl"
        assert names["ticks"]["type"] == "int"
        assert names["canvas"]["owned_by"] == "keep-list"

    def test_snapshot_via_tool(self, live_ws):
        tk, root = live_ws
        _start(tk, root)
        _data(tk.engine.call("live.snapshot", {"op": "save", "name": "before"}))
        _data(tk.engine.call("live.repl", {"code": "color = 'hotpink'"}))
        _data(tk.engine.call("live.snapshot", {"op": "restore", "name": "before"}))
        names = _data(tk.engine.call("live.state", {"keys": ["color"]}))["names"]
        assert names["color"]["repr"] == "'red'" and names["color"]["owned_by"] == "source"

    def test_envelope_errors(self, live_ws):
        tk, _root = live_ws
        res = tk.engine.call("live.repl", {"code": "1"})
        assert not res.ok and res.to_dict()["error"]["code"] == "ENOENT"   # no program yet
        _start(tk, _root)
        res = tk.engine.call("live.start", {"path": "nope.py"})
        assert not res.ok and res.to_dict()["error"]["code"] == "ENOENT"
        res = tk.engine.call("live.repl", {"program": "ghost", "code": "1"})
        assert not res.ok and res.to_dict()["error"]["code"] == "ENOENT"
        res = tk.engine.call("live.scene", {"op": "upsert", "node": {"type": "hyperdodecahedron"}})
        assert not res.ok and res.to_dict()["error"]["code"] == "BAD_ARGS"

    def test_scene_tool_drives_frame(self, live_ws):
        tk, root = live_ws
        (root / "bare.py").write_text("count = 0\n", encoding="utf-8")
        _data(tk.engine.call("live.start", {"path": "bare.py", "program": "bare",
                                            "watch": False}))
        out = _data(tk.engine.call("live.scene", {"program": "bare", "op": "upsert",
                                                  "node": {"type": "circle", "id": "sun",
                                                           "cx": 50, "cy": 50, "r": 12,
                                                           "fill": "#f2cc60"}}))
        assert out["frame"]["error"] is None
        svg = _data(tk.engine.call("live.render", {"program": "bare", "svg": True}))["svg"]
        assert "#f2cc60" in svg
        out = _data(tk.engine.call("live.scene", {"program": "bare", "op": "remove", "id": "sun"}))
        svg = _data(tk.engine.call("live.render", {"program": "bare", "svg": True}))["svg"]
        assert "#f2cc60" not in svg

    def test_repl_history_and_status(self, live_ws):
        tk, root = live_ws
        _start(tk, root)
        _data(tk.engine.call("live.repl", {"code": "color"}))
        st = _data(tk.engine.call("live.status", {"history": True}))
        assert st["count"] == 1
        assert st["history"]["repl"] and st["history"]["repl"][-1]["code"] == "color"
        assert st["watch"]["backend"] in ("poll", "watchfiles")
        caps = {m.capability for m in tk.registry.all() if m.id.startswith("live.")}
        assert "live.repl" in caps

    def test_read_only_policy_refuses_mutations(self, tmp_path):
        root = tmp_path / "ws"
        root.mkdir()
        (root / "app.py").write_text(PROG_V1, encoding="utf-8")
        tk = build(overrides={"roots": [str(root)], "state": {"dir": str(root / ".sk")},
                              "policy": {"read_only": True}, "log_level": "ERROR"})
        try:
            for tool, a in (("live.start", {"path": "app.py"}),
                            ("live.repl", {"code": "1"}),
                            ("live.patch", {"name": "render", "code": "def render():\n    pass\n"})):
                res = tk.engine.call(tool, a)
                assert not res.ok
                assert res.to_dict()["error"]["code"] == "READ_ONLY_MODE", tool
            assert tk.engine.call("live.status", {}).ok        # reads still work
        finally:
            tk.close()

    def test_disabled_group_refuses_cleanly(self, tmp_path):
        root = tmp_path / "ws"
        root.mkdir()
        (root / "app.py").write_text(PROG_V1, encoding="utf-8")
        tk = build(overrides={"roots": [str(root)], "state": {"dir": str(root / ".sk")},
                              "live": {"enabled": False}, "log_level": "ERROR"})
        try:
            res = tk.engine.call("live.start", {"path": "app.py"})
            assert not res.ok
            err = res.to_dict()["error"]
            assert err["code"] == "TOOL_NOT_ADVERTISED"
            assert err["details"]["disabled_by"] == "live.enabled"
        finally:
            tk.close()

    def test_sandbox_still_walls_paths(self, live_ws, tmp_path):
        tk, _root = live_ws
        outside = tmp_path / "elsewhere.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        res = tk.engine.call("live.start", {"path": str(outside)})
        assert not res.ok
        assert res.to_dict()["error"]["code"] == "SANDBOX_VIOLATION"

    def test_stop_tears_down_watcher(self, live_ws):
        tk, root = live_ws
        _start(tk, root)
        assert tk.live.watcher is not None
        _data(tk.engine.call("live.stop", {}))
        assert tk.live.watcher is None and tk.live.programs == {}

    def test_manager_without_sandbox(self, tmp_path):
        """A bare LiveManager (no engine) is the library-embedding story."""
        mgr = LiveManager()
        p = tmp_path / "m.py"
        p.write_text("x = 5\n", encoding="utf-8")
        out = mgr.start(str(p), watch=False)
        assert out["program"] == "m"
        assert mgr.get(None).repl("x * 2")["value"] == "10"
        mgr.close()


# ------------------------------------------------------------------- the panel


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestPanel:
    def test_serve_frame_state_and_repl(self, live_ws):
        tk, root = live_ws
        _start(tk, root)
        port = _free_port()
        out = _data(tk.engine.call("live.serve", {"port": port}))
        assert out["serving"] and out["url"].endswith(f":{port}/")
        base = f"http://127.0.0.1:{port}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as r:
                return r.read()

        try:
            page = get("/").decode()
            assert "LiveREPL" in page and "/frame.svg" in page
            svg = get("/frame.svg").decode()
            assert "<svg" in svg and 'fill="red"' in svg
            scene = json.loads(get("/scene.json"))
            assert scene["nodes"] and scene["nodes"][0]["id"] in {"box", "t"}
            version = json.loads(get("/version"))
            assert version["count"] == 1 and "version" in version

            # the in-page console: POST /repl mutates the program, bumps the frame
            req = urllib.request.Request(
                base + "/repl",
                data=json.dumps({"code": "color = 'magenta'"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                repl = json.loads(r.read())
            assert repl["ok"] and repl["frame"]["error"] is None
            svg2 = get("/frame.svg").decode()
            assert 'fill="magenta"' in svg2               # the HMR beat, over HTTP
            version2 = json.loads(get("/version"))
            assert version2["version"] > version["version"]
        finally:
            stop = _data(tk.engine.call("live.serve", {"op": "stop"}))
            assert stop["serving"] is False

    def test_panel_honours_repl_off(self, tmp_path):
        root = tmp_path / "ws"
        root.mkdir()
        (root / "app.py").write_text(PROG_V1, encoding="utf-8")
        tk = build(overrides={"roots": [str(root)], "state": {"dir": str(root / ".sk")},
                              "live": {"panel_repl": False}, "log_level": "ERROR"})
        try:
            _start(tk, root)
            port = _free_port()
            _data(tk.engine.call("live.serve", {"port": port}))
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/repl",
                data=json.dumps({"code": "1"}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with pytest.raises(urllib.error.HTTPError) as ei:
                urllib.request.urlopen(req, timeout=5)
            assert ei.value.code == 403
        finally:
            tk.close()


# ---------------------------------------------------------------- demopage sync


def test_demo_program_constant_and_example_file_agree():
    from skeletonkey.live.demos import ORBITAL_SRC

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(repo, "examples", "live_hmr", "orbital.py"),
              encoding="utf-8") as fh:
        example = fh.read()
    assert example.endswith(ORBITAL_SRC), "examples/live_hmr/orbital.py drifted from demos.py"

    # the canned program compiles and is itself loadable through the runtime
    assert "def render(" in ORBITAL_SRC and "__live_keep__" in ORBITAL_SRC
    compile(ORBITAL_SRC, "orbital.py", "exec")


# ------------------------------------------- blueprint contract (hooks/reload)


class TestHMRContracts:
    def test_export_import_state_hooks_round_trip(self, tmp_path):
        src = '''calls = 0

def __hmr_export_state__():
    return {"calls": calls}

def __hmr_import_state__(state):
    global calls
    calls = state["calls"] * 10   # migration happens in the hook, on new code
'''
        p = tmp_path / "m.py"
        p.write_text(src, encoding="utf-8")
        prog = LiveProgram("m", str(p))
        prog.auto_render = False
        prog.load_initial()
        prog.repl("calls = 5")
        p.write_text(src, encoding="utf-8")     # identical save still runs the hooks
        rep = prog.reload(reason="test")
        notes = rep.to_dict()["notes"]
        assert prog.ns["calls"] == 50
        assert any("__hmr_export_state__" in n for n in notes)
        assert any("__hmr_import_state__" in n for n in notes)

    def test_export_hook_failure_aborts_transaction(self, tmp_path):
        # The export hook runs on the OLD code, pre-patch. When IT decides the
        # state cannot leave (here: over some threshold), the whole reload must
        # abort with nothing applied - that is the transactional property.
        prog_path = tmp_path / "m.py"
        prog_path.write_text(
            "calls = 0\ngood = 1\n"
            "def __hmr_export_state__():\n"
            "    if calls > 10:\n"
            "        raise RuntimeError('cannot serialize state')\n"
            "    return {'calls': calls}\n"
            "def __hmr_import_state__(state):\n"
            "    global calls\n"
            "    calls = state['calls']\n",
            encoding="utf-8")
        prog = LiveProgram("m", str(prog_path))
        prog.auto_render = False
        prog.load_initial()
        prog.repl("calls = 50")                      # over the hook's threshold
        prog_path.write_text(prog_path.read_text().replace("good = 1", "good = 2"),
                             encoding="utf-8")
        rep = prog.reload(reason="test")
        assert rep.ok is False and "aborted" in rep.error["message"]
        assert prog.ns["good"] == 1                  # nothing was patched
        prog.repl("calls = 3")                       # back under: reload recovers
        rep2 = prog.reload(reason="test")
        assert rep2.ok and prog.ns["good"] == 2 and prog.ns["calls"] == 3

    def test_registry_rebinding_after_reload(self, tmp_path):
        v1 = ('HANDLERS = {}\n__live_registries__ = ["HANDLERS"]\n'
              "def run_job():\n    return 'v1'\n"
              'HANDLERS["job"] = run_job\n')
        v2 = v1.replace("'v1'", "'v2'")
        p = tmp_path / "m.py"
        p.write_text(v1, encoding="utf-8")
        prog = LiveProgram("m", str(p))
        prog.auto_render = False
        prog.load_initial()
        old_entry = prog.ns["HANDLERS"]["job"]
        assert old_entry() == "v1"
        p.write_text(v2, encoding="utf-8")
        rep = prog.reload(reason="test")
        assert rep.ok
        entry = prog.ns["HANDLERS"]["job"]
        assert entry() == "v2"
        # in-place patch means the registry entry may be the same object, patched;
        # a rebind would be reported and is equally acceptable - the OUTCOME is v2.
        if entry is not old_entry:
            assert any("registry rebind" in n for n in rep.to_dict()["notes"])

    def test_on_reload_observer_fires(self, tmp_path):
        seen = []
        p = tmp_path / "m.py"
        p.write_text("x = 1\n", encoding="utf-8")
        prog = LiveProgram("m", str(p))
        prog.auto_render = False
        prog.load_initial()
        prog.ns["__live_on_reload__"] = lambda report: seen.append(report)
        prog.keep.add("__live_on_reload__")
        p.write_text("x = 2\n", encoding="utf-8")
        prog.reload(reason="test")
        assert seen and seen[0]["ok"] is True and seen[0]["data_updated"] == ["x"]


class TestMesh3D:
    def test_mesh_shaded_faces_painter_sorted(self):
        s = Scene(300, 240)
        s.mesh3d([[-60, 0, -40], [0, -50, 0], [60, 10, 40], [90, -10, -20]],
                 [[0, 1, 2], [1, 3, 2]], id="m", fill="#58a6ff")
        svg = s.to_svg()
        assert svg.count("<path") == 2
        # lambert shading darkens or leaves the fill: either way both faces painted
        assert "M" in svg and svg.count("Z") == 2

    def test_mesh_rejects_bad_index(self):
        s = Scene()
        with pytest.raises(ValueError):
            s.mesh3d([[0, 0, 0]], [[0, 1]], id="bad")

    def test_cube_with_fill_renders_faces_then_edges(self):
        s = Scene(200, 200)
        s.cube3d(0, 0, 0, 40, id="c", fill="#1f6feb", spin=12)
        svg = s.to_svg()
        assert svg.count("<path") == 6 and svg.count("<line") == 12

    def test_polygon_is_closed_and_wired(self):
        s = Scene(100, 100)
        s.poly3d([(0, 0, 0), (10, 0, 5), (10, 10, 0)], id="tri", close=True)
        svg = s.to_svg()
        assert svg.count(" L") >= 3


class TestDebugPanelRoutes:
    def test_view3d_and_agents_pages_and_apis(self, live_ws):
        tk, root = live_ws
        # program with an agent-ish handler registry
        (root / "agentic.py").write_text(
            'calls = 0\n'
            'HANDLERS = {}\n'
            '__live_registries__ = ["HANDLERS"]\n'
            "def plan():\n    global calls\n    calls += 1\n    return f'plan {calls}'\n"
            'HANDLERS["plan"] = plan\n'
            "def render():\n    canvas.circle(50, 50, 20, fill='#3fb950', id='ok')\n",
            encoding="utf-8")
        _data(tk.engine.call("live.start", {"path": "agentic.py", "program": "agentic",
                                            "watch": False}))
        port = _free_port()
        _data(tk.engine.call("live.serve", {"port": port}))
        base = f"http://127.0.0.1:{port}"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as r:
                return r.read()

        def post(path, payload):
            req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:      # 4xx bodies carry the error JSON
                return json.loads(e.read())

        try:
            assert b"3D preview" in get("/view3d") or b"Live 3D" in get("/view3d")
            page = get("/agents").decode()
            assert "agent debugger" in page and "/api/control" in page and "/api/activity" in page

            prog = json.loads(get("/api/program?program=agentic"))
            assert prog["id"] == "agentic"
            assert "HANDLERS" in prog["registries"] and any("plan ->" in e
                                                            for e in prog["registries"]["HANDLERS"])

            state = json.loads(get("/state?program=agentic"))
            assert state["names"]["calls"]["type"] == "int"
            assert state["registries"]["HANDLERS"]

            hist = json.loads(get("/api/history?program=agentic"))
            assert hist["program"] == "agentic" and "repl" in hist and "patches" in hist

            # invoke the agent handler through the panel REPL (the debugger move)
            out = post("/repl", {"program": "agentic", "code": "HANDLERS['plan']()"})
            assert out["ok"] and out["value"] == "'plan 1'"
            state2 = json.loads(get("/state?program=agentic"))
            assert state2["names"]["calls"]["repr"] == "1"

            # control endpoint: reload runs the same code path as live.reload
            ctl = post("/api/control", {"action": "reload", "program": "agentic"})
            assert ctl["ok"] is True and ctl["program"] == "agentic"
            bad = post("/api/control", {"action": "definitely-not"})
            assert "error" in bad
        finally:
            _data(tk.engine.call("live.serve", {"op": "stop"}))

    def test_activity_feed_shape(self, live_ws):
        tk, root = live_ws
        _start(tk, root)                       # engine.call rows land in the ledger
        port = _free_port()
        _data(tk.engine.call("live.serve", {"port": port}))
        base = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(base + "/api/activity", timeout=5) as r:
                act = json.loads(r.read())
            assert isinstance(act["engine"], list)
            # ledger rows, when present, are tool-shaped
            for row in act["engine"]:
                assert "tool" in row and "ok" in row
        finally:
            tk.engine.call("live.serve", {"op": "stop"})
