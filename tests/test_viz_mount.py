"""The catalog mounted inside the live panel (ADR-0018).

Merging the two servers cost the catalog its structural read-only guarantee:
it used to be enforced by the *absence* of `do_POST` on its handler class, and
the panel has a `do_POST` because `/repl` executes code.

These tests are what replaces that guarantee. They are therefore load-bearing
in a way most tests are not - if this file is ever weakened or skipped, the
property it protects is simply gone, with nothing in the type system or the
class shape left to notice. Read ADR-0018 before changing anything here.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from skeletonkey.live.panel import Panel
from skeletonkey.viz.mount import ROUTES, SAFE_METHODS, VizMount

REPO = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _FakeManager:
    """Enough manager for a Panel to serve. No programs, no REPL surface."""

    def __init__(self) -> None:
        self.programs: dict = {}
        self._engine = None

    def add_frame_listener(self, fn) -> None:
        pass

    def watch_status(self) -> dict:
        return {}


@pytest.fixture
def mounted(toolkit):
    """A panel serving the catalog at /tools, on a real socket."""
    reg = toolkit.engine.registry
    mount = VizMount(lambda: reg)
    panel = Panel(_FakeManager(), port=_free_port(), viz_mount=mount)
    panel.start()
    # the server thread needs a beat before the first connect on some boxes
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", panel.port), 0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    yield panel
    panel.stop()


def _raw(port: int, request_line: str, body: str = "{}") -> str:
    """Speak HTTP by hand: urllib normalises paths, and path normalisation is
    exactly the thing a fence bypass would hide behind."""
    s = socket.create_connection(("127.0.0.1", port), 5)
    req = (f"{request_line}\r\nHost: x\r\n"
           f"Content-Length: {len(body)}\r\n"
           f"Content-Type: application/json\r\n\r\n{body}")
    s.sendall(req.encode())
    s.settimeout(10)
    buf = b""
    try:
        # read headers, then exactly Content-Length bytes: a fixed cap would
        # truncate the model JSON and produce a fake "malformed" failure
        while b"\r\n\r\n" not in buf:
            b = s.recv(65536)
            if not b:
                break
            buf += b
        head, _, rest = buf.partition(b"\r\n\r\n")
        want = 0
        for line in head.decode("latin1").split("\r\n"):
            if line.lower().startswith("content-length:"):
                want = int(line.split(":", 1)[1])
        while len(rest) < want:
            b = s.recv(65536)
            if not b:
                break
            rest += b
        buf = head + b"\r\n\r\n" + rest
    except (TimeoutError, OSError):
        pass
    s.close()
    return buf.decode("latin1")


def _status(raw: str) -> int:
    return int(raw.split("\r\n")[0].split()[1])


# --------------------------------------------------------------------------
# the fence: the whole reason this file exists
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/tools",
    "/tools/",
    "/tools/api/model",
    "/tools/api/digest",
    "/tools/api/runtime",
    "/tools/api/tool/fs.delete",
    "/tools/healthz",
    "/tools/anything/else",
])
def test_post_to_the_mount_is_refused(mounted, path):
    assert _status(_raw(mounted.port, f"POST {path} HTTP/1.1")) == 405


@pytest.mark.parametrize("verb", ["PUT", "DELETE", "PATCH"])
def test_other_write_verbs_never_reach_the_mount(mounted, verb):
    # the panel defines no handler for these at all, so they 501
    assert _status(_raw(mounted.port, f"{verb} /tools HTTP/1.1")) in (405, 501)


@pytest.mark.parametrize("path", [
    "/tools/../repl",
    "/tools/%2e%2e/repl",
    "/tools/..%2frepl",
    "/tools/./../repl",
])
def test_traversal_out_of_the_mount_cannot_reach_the_repl(mounted, path):
    """The dangerous shape: a path the mount claims that resolves toward a
    write route. It must fail closed - refused, never forwarded."""
    got = _status(_raw(mounted.port, f"POST {path} HTTP/1.1"))
    assert got in (400, 404, 405), f"traversal reached something: {got}"


def test_the_mount_refuses_unsafe_methods_itself(toolkit):
    """Not merely fenced by the host: the mount will not serve a write even if
    a future host wires it into do_POST by mistake."""
    reg = toolkit.engine.registry
    mount = VizMount(lambda: reg)
    for method in ("POST", "PUT", "DELETE", "PATCH", "TRACE"):
        status, body, _ctype = mount.dispatch(method, "/tools/api/model")
        assert status == 405
        assert "read-only" in json.loads(body)["error"]


def test_safe_methods_are_exactly_get_and_head():
    assert frozenset({"GET", "HEAD"}) == SAFE_METHODS


def test_the_mount_holds_no_handle_to_anything_mutable(toolkit):
    """ADR-0018 consequence 3: it gets a registry provider, not an engine, so
    there is no object graph to walk toward a write."""
    reg = toolkit.engine.registry
    mount = VizMount(lambda: reg)
    for attr in ("engine", "toolkit", "manager", "call", "_engine"):
        assert not hasattr(mount, attr), f"mount reached {attr}"


def test_the_route_allowlist_is_data_not_control_flow():
    """A reviewer should be able to read the surface as a tuple."""
    assert isinstance(ROUTES, tuple)
    assert all(isinstance(r, str) for r in ROUTES)
    assert "/api/tool/" in ROUTES


# --------------------------------------------------------------------------
# it still actually works
# --------------------------------------------------------------------------

def test_the_page_serves_with_and_without_a_trailing_slash(mounted):
    for path in ("/tools", "/tools/"):
        raw = _raw(mounted.port, f"GET {path} HTTP/1.1")
        assert _status(raw) == 200
        assert "SkeletonKey" in raw


def test_api_base_makes_polling_prefix_correct(mounted):
    """Relative "api/digest" resolves differently at /tools vs /tools/ - the
    bug an explicit base exists to prevent."""
    raw = _raw(mounted.port, "GET /tools HTTP/1.1")
    assert '"apiBase":"/tools/"' in raw.replace(" ", "")


def test_the_model_api_answers_under_the_prefix(mounted):
    raw = _raw(mounted.port, "GET /tools/api/model HTTP/1.1")
    assert _status(raw) == 200
    doc = json.loads(raw.split("\r\n\r\n", 1)[1])
    assert doc["nodes"] and doc["model_digest"]


def test_healthz_declares_itself_read_only(mounted):
    raw = _raw(mounted.port, "GET /tools/healthz HTTP/1.1")
    doc = json.loads(raw.split("\r\n\r\n", 1)[1])
    assert doc["read_only"] is True
    assert doc["mounted_at"] == "/tools"


def test_unknown_mount_route_404s_with_the_allowlist(mounted):
    raw = _raw(mounted.port, "GET /tools/nope HTTP/1.1")
    assert _status(raw) == 404
    doc = json.loads(raw.split("\r\n\r\n", 1)[1])
    assert any(r.startswith("/tools") for r in doc["routes"])


def test_the_panel_reports_where_the_catalog_is(mounted):
    assert mounted.status()["viz_mounted_at"] == "/tools"
    raw = _raw(mounted.port, "GET /version HTTP/1.1")
    doc = json.loads(raw.split("\r\n\r\n", 1)[1])
    assert doc["viz_mounted_at"] == "/tools"


def test_a_lookalike_prefix_is_not_claimed(toolkit):
    """/toolsX must not be swallowed by a /tools mount."""
    reg = toolkit.engine.registry
    mount = VizMount(lambda: reg)
    assert mount.owns("/tools")
    assert mount.owns("/tools/")
    assert mount.owns("/tools/api/model")
    assert not mount.owns("/toolsX")
    assert not mount.owns("/repl")
    assert not mount.owns("/")


def test_an_unmounted_panel_is_unchanged():
    """Mounting is opt-in; the default panel must be exactly as it was."""
    panel = Panel(_FakeManager(), port=_free_port())
    try:
        assert panel.viz_mount is None
        assert panel.status()["viz_mounted_at"] is None
    finally:
        panel.stop()


def test_a_mount_failure_cannot_take_down_the_host(mounted, monkeypatch):
    """A broken projection must 500 that one route, not kill the panel."""
    def boom():
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(mounted.viz_mount, "registry_provider", boom)
    assert _status(_raw(mounted.port, "GET /tools/api/model HTTP/1.1")) == 500
    # the host is still alive and serving its own routes
    assert _status(_raw(mounted.port, "GET /version HTTP/1.1")) == 200


def test_concurrent_reads_do_not_corrupt_each_other(mounted):
    """The mount rebuilds per request; two in-flight rebuilds must not interleave
    into a torn response."""
    results: list[int] = []
    lock = threading.Lock()

    def hit():
        raw = _raw(mounted.port, "GET /tools/api/digest HTTP/1.1")
        with lock:
            results.append(_status(raw))

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert results == [200] * 8
