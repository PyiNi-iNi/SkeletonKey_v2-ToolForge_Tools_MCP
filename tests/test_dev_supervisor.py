"""`sk dev`: the supervisor that starts both servers and links them (ADR-0017).

ADR-0017 declined port-probing auto-discovery and named a supervising command
as the right answer to the two-URL friction. These tests hold it to that: it
must *create* both URLs and hand each to the other, never guess at one.

The supervisor blocks forever by design, so these drive it as a subprocess and
kill it, rather than calling _cmd_dev() directly.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _wait_for(url: str, deadline_s: float = 25.0) -> bool:
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.2)
    return False


class _Dev:
    """Run `sk dev` in a workspace and tear it down properly."""

    def __init__(self, ws: Path, *extra: str) -> None:
        self.panel_port = _free_port()
        self.viz_port = _free_port()
        env = dict(os.environ, PYTHONPATH=str(REPO))
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "skeletonkey.cli", "--cwd", str(ws), "dev",
             "--panel-port", str(self.panel_port),
             "--viz-port", str(self.viz_port), *extra],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            env=env, cwd=str(REPO), start_new_session=True)

    @property
    def panel(self) -> str:
        return f"http://127.0.0.1:{self.panel_port}/"

    @property
    def viz(self) -> str:
        return f"http://127.0.0.1:{self.viz_port}/"

    def close(self) -> str:
        if self.proc.poll() is None:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
        try:
            out, _ = self.proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            out, _ = self.proc.communicate(timeout=10)
        return out or ""


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    w = tmp_path / "ws"
    w.mkdir()
    return w


# --------------------------------------------------------------------------
# it brings up both, and links them in both directions
# --------------------------------------------------------------------------

@pytest.mark.slow
def test_dev_mounts_the_catalog_on_one_port_by_default(ws):
    """ADR-0018: the default is one server, catalog at /tools."""
    dev = _Dev(ws)
    try:
        assert _wait_for(dev.panel + "version"), "panel never came up"

        _s, body = _get(dev.panel + "version")
        assert json.loads(body)["viz_mounted_at"] == "/tools"

        # the catalog is served from the SAME port
        status, page = _get(dev.panel + "tools")
        assert status == 200
        assert "SkeletonKey" in page

        # and nothing is listening on the separate catalog port
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(dev.viz, timeout=2)
    finally:
        dev.close()


@pytest.mark.slow
def test_the_mounted_catalog_still_refuses_writes(ws):
    """The guard that replaces the structural guarantee, end to end."""
    dev = _Dev(ws)
    try:
        assert _wait_for(dev.panel + "version")
        for path in ("tools", "tools/api/model", "tools/healthz"):
            req = urllib.request.Request(dev.panel + path, data=b"{}", method="POST")
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(req, timeout=5)
            assert exc.value.code == 405, path
    finally:
        dev.close()


@pytest.mark.slow
def test_two_servers_mode_still_works_and_links_both_ways(ws):
    """ADR-0017 mode remains supported and is still the stronger posture."""
    dev = _Dev(ws, "--two-servers")
    try:
        assert _wait_for(dev.viz + "healthz"), "catalog never came up"
        assert _wait_for(dev.panel + "version"), "panel never came up"

        _s, body = _get(dev.viz + "healthz")
        assert json.loads(body)["panel_url"] == dev.panel

        _s, body = _get(dev.panel + "version")
        assert json.loads(body)["viz_url"] == dev.viz
        assert json.loads(body)["viz_mounted_at"] is None

        _s, page = _get(dev.viz)
        assert 'class="panel-link"' in page
    finally:
        dev.close()


@pytest.mark.slow
def test_the_standalone_catalog_is_still_structurally_read_only(ws):
    """--two-servers must keep the property the merge gave up."""
    dev = _Dev(ws, "--two-servers")
    try:
        assert _wait_for(dev.viz + "healthz")
        req = urllib.request.Request(dev.viz, data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code in (405, 501)
    finally:
        dev.close()


@pytest.mark.slow
def test_no_viz_leaves_the_panel_unlinked(ws):
    dev = _Dev(ws, "--no-viz")
    try:
        assert _wait_for(dev.panel + "version")
        _s, body = _get(dev.panel + "version")
        assert json.loads(body)["viz_url"] is None
        # and nothing is listening on the catalog port
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(dev.viz, timeout=2)
    finally:
        dev.close()


@pytest.mark.slow
def test_no_panel_leaves_the_catalog_unlinked(ws):
    dev = _Dev(ws, "--no-panel")
    try:
        assert _wait_for(dev.viz + "healthz")
        _s, body = _get(dev.viz + "healthz")
        assert json.loads(body)["panel_url"] is None
        _s, page = _get(dev.viz)
        assert '<a class="panel-link"' not in page
    finally:
        dev.close()


@pytest.mark.slow
def test_both_disabled_is_an_error_not_a_hang(ws):
    """--no-panel --no-viz cancel out; that must exit, not block forever."""
    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, "-m", "skeletonkey.cli", "--cwd", str(ws), "dev",
         "--no-panel", "--no-viz"],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=60)
    assert proc.returncode == 2
    assert "nothing to serve" in (proc.stdout + proc.stderr)


@pytest.mark.slow
def test_a_missing_watch_target_fails_clean(ws):
    """Half-started is worse than not started: a bad path must not leave a
    catalog orphaned behind a non-zero exit."""
    env = dict(os.environ, PYTHONPATH=str(REPO))
    proc = subprocess.run(
        [sys.executable, "-m", "skeletonkey.cli", "--cwd", str(ws), "dev",
         str(ws / "nope.py"), "--viz-port", str(_free_port()),
         "--panel-port", str(_free_port())],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=60)
    assert proc.returncode == 1
    assert "live.start failed" in (proc.stdout + proc.stderr)


@pytest.mark.slow
def test_dev_watches_a_program_and_reloads_it(ws):
    """The whole point of the viewport: save the file, the program reloads."""
    prog = ws / "p.py"
    prog.write_text("hue = '#111111'\n\ndef render(canvas, state):\n    pass\n",
                    encoding="utf-8")
    dev = _Dev(ws, str(prog))
    try:
        assert _wait_for(dev.panel + "version")
        _s, body = _get(dev.panel + "version")
        programs = json.loads(body)["programs"]
        assert programs, "no program was started"
        before = programs[0]["reloads"]

        time.sleep(0.5)
        prog.write_text("hue = '#f2cc60'\n\ndef render(canvas, state):\n    pass\n",
                        encoding="utf-8")

        end = time.time() + 20
        after = before
        while time.time() < end:
            _s, body = _get(dev.panel + "version")
            p = json.loads(body)["programs"][0]
            after, failed = p["reloads"], p["failed_reloads"]
            if after > before:
                assert failed == 0, "the reload failed"
                break
            time.sleep(0.3)
        assert after > before, "saving the file did not reload the program"
    finally:
        dev.close()


# --------------------------------------------------------------------------
# it does not probe
# --------------------------------------------------------------------------

def test_the_supervisor_does_not_scan_for_peers():
    """ADR-0017 consequence 5. The supervisor knows both URLs because it made
    them; anything that looks like probing is the behaviour we declined."""
    src = (REPO / "skeletonkey" / "cli.py").read_text(encoding="utf-8")
    start = src.index("def _cmd_dev(")
    body = src[start:src.index("\ndef ", start + 10)]
    for forbidden in ("connect_ex", "socket.socket", "for port in", "range(80"):
        assert forbidden not in body, f"_cmd_dev must not probe: {forbidden}"


def test_dev_is_documented_in_help():
    env = dict(os.environ, PYTHONPATH=str(REPO))
    out = subprocess.run([sys.executable, "-m", "skeletonkey.cli", "dev", "--help"],
                         capture_output=True, text=True, env=env,
                         cwd=str(REPO), timeout=120).stdout
    for flag in ("--no-panel", "--no-viz", "--panel-port", "--viz-port", "--runtime"):
        assert flag in out
