"""Regression coverage for the live/publishing remediation.

These tests deliberately use real HTTP sockets for the preview boundary.  A
unit test of the handler method would miss the request framing, cookie, origin,
and lifecycle behaviour that caused the original defects.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from skeletonkey.core.config import Config
from skeletonkey.core.http_security import HttpAccessPolicy, advertised_url
from skeletonkey.core.ledger import Ledger
from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.live.panel import Panel
from skeletonkey.live.runtime import LiveProgram
from skeletonkey.toolkit import build


class _Manager:
    def __init__(self, engine=None) -> None:
        self.programs = {}
        self.listeners = []
        self._engine = engine

    def add_frame_listener(self, fn) -> None:
        self.listeners.append(fn)

    def remove_frame_listener(self, fn) -> None:
        self.listeners = [item for item in self.listeners if item != fn]

    def watch_status(self) -> dict:
        return {}


def _post(url: str, value: bytes, *, headers: dict[str, str] | None = None):
    req = urllib.request.Request(
        url, data=value, method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_remote_http_requires_explicit_opt_in_and_authentication() -> None:
    with pytest.raises(ValueError, match="non-loopback"):
        HttpAccessPolicy(host="0.0.0.0")
    with pytest.raises(ValueError, match="auth_token"):
        HttpAccessPolicy(host="0.0.0.0", allow_remote=True)

    policy = HttpAccessPolicy(
        host="0.0.0.0", allow_remote=True, auth_token="t" * 32,
        public_base_url="https://preview.example.test/live",
        allowed_origins=("https://preview.example.test",),
    )
    assert policy.check({"Host": "preview.example.test"}, "/") == (
        401, "preview authentication required"
    )
    assert policy.check({
        "Host": "preview.example.test",
        "Authorization": "Bearer " + "t" * 32,
        "Origin": "https://evil.example.test",
    }, "/") == (403, "request origin is not allowlisted")
    assert advertised_url("0.0.0.0", 8010) is None


def test_panel_repl_false_fences_control_and_body_is_bounded() -> None:
    manager = _Manager()
    panel = Panel(manager, port=0, repl_enabled=False, max_body_bytes=64)
    panel.start()
    try:
        status, body = _post(
            panel.url + "api/control", json.dumps({"action": "render"}).encode()
        )
        assert status == 403
        assert b"PANEL_MUTATIONS_DISABLED" in body

        panel.repl_enabled = True
        status, body = _post(panel.url + "api/control", b"x" * 65)
        assert status == 413
        assert b"BODY_TOO_LARGE" in body
    finally:
        panel.stop()


def test_panel_stop_removes_listener_and_can_restart() -> None:
    manager = _Manager()
    panel = Panel(manager, port=0)
    panel.start()
    assert len(manager.listeners) == 1
    panel.stop()
    assert manager.listeners == []
    panel.start()
    try:
        assert len(manager.listeners) == 1
        assert panel.status()["serving"] is True
        # Port zero is allowed to select a fresh port, but the server must be
        # live after a stop/start cycle rather than pointing at a closed socket.
        assert panel.port > 0
    finally:
        panel.stop()
    assert manager.listeners == []


def test_activity_uses_engine_public_ledger(tmp_path: Path) -> None:
    ledger = Ledger(str(tmp_path / "ledger.ndjson"))
    ledger.append(tool="live.status", args={}, ok=True, duration_ms=3)
    manager = _Manager(SimpleNamespace(ledger=ledger))
    panel = Panel(manager, port=0)
    try:
        rows = panel._engine_activity()
        assert rows and rows[0]["tool"] == "live.status"
        assert rows[0]["ms"] == 3
    finally:
        panel.stop()
        ledger.close()


def test_live_source_cap_applies_to_same_tree_dependency_before_import(tmp_path: Path) -> None:
    app = tmp_path / "app.py"
    dep = tmp_path / "dep.py"
    app.write_text("import dep\nvalue = dep.value\n", encoding="utf-8")
    dep.write_text("value = 1\n" + "x" * 100, encoding="utf-8")
    program = LiveProgram("app", str(app), max_source_bytes=32)
    with pytest.raises(SkeletonKeyError) as caught:
        program.load_initial()
    assert caught.value.code == "TOO_LARGE"
    assert str(dep) in caught.value.details["path"]


def test_live_source_cap_applies_to_dependency_reload(tmp_path: Path) -> None:
    name = "sk_remediation_dep"
    app = tmp_path / "app.py"
    dep = tmp_path / (name + ".py")
    app.write_text(f"import {name}\nvalue = {name}.value\n", encoding="utf-8")
    dep.write_text("value = 1\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        program = LiveProgram("app", str(app), max_source_bytes=64)
        program.load_initial()
        dep.write_text("value = 1\n" + "z" * 100, encoding="utf-8")
        report = program.reload_dependency(name)
        assert report.ok is False and report.error["code"] == "TOO_LARGE"
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop(name, None)


def test_live_source_cap_applies_to_initial_and_reload(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("value = 123\n", encoding="utf-8")
    program = LiveProgram("app", str(source), max_source_bytes=4)
    with pytest.raises(SkeletonKeyError) as initial:
        program.load_initial()
    assert initial.value.code == "TOO_LARGE"

    # A program loaded under a larger cap reports the same bounded-read error
    # on reload rather than letting a changed file bypass the configuration.
    program.max_source_bytes = 100
    program.load_initial()
    source.write_text("value = " + "9" * 200 + "\n", encoding="utf-8")
    report = program.reload()
    assert report.ok is False and report.error["code"] == "TOO_LARGE"


def test_publish_multi_file_failure_rolls_back_with_sha_guard(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a.env").write_text("A={{PUB.test.token}}\n", encoding="utf-8")
    (root / "b.env").write_text("B={{PUB.test.token}}\n", encoding="utf-8")
    cfg = Config.load(cwd=str(root), overrides={
        "roots": [str(root)],
        "state": {"dir": str(root / ".sk")},
        "publish": {"store_path": str(root / "store.json")},
        "policy": {"auto_approve": ["none", "read", "write", "destructive"],
                   "confirm_destructive": False},
        "log_level": "ERROR",
    })
    toolkit = build(config=cfg)
    try:
        engine = toolkit.engine
        assert engine.call("pub.store_put", {
            "id": "test.token", "kind": "token", "value": "VALUE",
        }).ok
        original_write = toolkit.fs.write
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated second-file failure")
            return original_write(*args, **kwargs)

        toolkit.fs.write = fail_second
        result = engine.call("pub.inject", {"path": "."})
        assert result.ok is False
        assert result.error.code == "IO"
        assert result.error.details["rollback"]["complete"] is True
        assert (root / "a.env").read_text() == "A={{PUB.test.token}}\n"
        assert (root / "b.env").read_text() == "B={{PUB.test.token}}\n"
    finally:
        toolkit.close()
