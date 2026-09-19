"""The viewport cross-link between the two servers (ADR-0017).

The catalog (`sk viz --serve`) is structurally read-only; the live panel
executes code. They are cross-linked by URL and never merged. These tests pin
the boundary, because the tempting refactor - "just serve both from one
server" - would silently delete the catalog's strongest property.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

import pytest

from skeletonkey.viz import build_viz_model, render_html
from skeletonkey.viz.server import VizServer


def _free_port() -> int:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------
# the link renders, and only when asked for
# --------------------------------------------------------------------------

def test_no_panel_link_by_default(toolkit):
    """The offline page and `sk export viz-html` must be unchanged by V7."""
    model = build_viz_model(toolkit.engine.registry)
    # the CSS rule is module-level and always ships; the ANCHOR must not
    assert '<a class="panel-link"' not in render_html(model)


def test_the_page_is_byte_identical_without_a_peer(toolkit):
    model = build_viz_model(toolkit.engine.registry)
    assert render_html(model, title="x") == render_html(model, title="x", panel_url=None)


def test_panel_link_renders_when_supplied(toolkit):
    model = build_viz_model(toolkit.engine.registry)
    page = render_html(model, panel_url="http://127.0.0.1:8010/")
    assert 'class="panel-link"' in page
    assert 'href="http://127.0.0.1:8010/"' in page


# --------------------------------------------------------------------------
# the link is a string and nothing more
# --------------------------------------------------------------------------

def test_the_catalog_never_fetches_the_panel():
    """A read-only projection that makes outbound requests is a confused
    deputy. The viz server must own no HTTP client at all."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "skeletonkey" / "viz" / "server.py").read_text(encoding="utf-8")
    import re as _re
    for forbidden in ("urllib.request", "urlopen", "http.client",
                      "import requests", "create_connection"):
        assert not _re.search(r"\b" + _re.escape(forbidden), src), \
            f"viz server must not reach out: {forbidden}"


def test_the_panel_url_is_not_iframed(toolkit):
    model = build_viz_model(toolkit.engine.registry)
    page = render_html(model, panel_url="http://127.0.0.1:8010/")
    assert "<iframe" not in page.lower()
    assert "<embed" not in page.lower()


def test_a_hostile_panel_url_is_escaped(toolkit):
    """The URL comes from CLI/config: treat it as untrusted like everything else."""
    model = build_viz_model(toolkit.engine.registry)
    page = render_html(model, panel_url='http://x/" onmouseover="alert(1)')
    assert 'onmouseover="alert(1)"' not in page
    assert "&quot;" in page or "&#34;" in page


# --------------------------------------------------------------------------
# the structural guarantee survives the link
# --------------------------------------------------------------------------

def test_the_linked_catalog_still_has_no_write_verbs(toolkit):
    """The whole reason ADR-0017 refuses to merge the servers."""
    viz = VizServer(toolkit.engine.registry, panel_url="http://127.0.0.1:8010/")
    handler = viz._handler_class()
    for verb in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH"):
        assert not hasattr(handler, verb), f"catalog grew a {verb}"


def test_a_post_to_the_linked_catalog_is_refused(toolkit):
    port = _free_port()
    viz = VizServer(toolkit.engine.registry, port=port,
                    panel_url="http://127.0.0.1:8010/")
    with viz:
        req = urllib.request.Request(viz.url, data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code in (405, 501)


def test_healthz_reports_the_peer(toolkit):
    port = _free_port()
    viz = VizServer(toolkit.engine.registry, port=port,
                    panel_url="http://127.0.0.1:8010/")
    with viz:
        status, body = _get(viz.url + "healthz")
        assert status == 200
        assert json.loads(body)["panel_url"] == "http://127.0.0.1:8010/"


def test_served_page_carries_the_link(toolkit):
    port = _free_port()
    viz = VizServer(toolkit.engine.registry, port=port,
                    panel_url="http://127.0.0.1:8010/")
    with viz:
        status, body = _get(viz.url)
        assert status == 200
        assert 'class="panel-link"' in body


def test_peer_is_null_when_unlinked(toolkit):
    port = _free_port()
    with VizServer(toolkit.engine.registry, port=port) as viz:
        _status, body = _get(viz.url + "healthz")
        assert json.loads(body)["panel_url"] is None


# --------------------------------------------------------------------------
# the panel side of the link
# --------------------------------------------------------------------------

def test_panel_accepts_and_reports_a_viz_url():
    from skeletonkey.live.panel import Panel

    class _FakeManager:
        def __init__(self) -> None:
            self.programs: dict = {}

        def add_frame_listener(self, fn):     # pragma: no cover - unused here
            pass

    panel = Panel(_FakeManager(), port=_free_port(),
                  viz_url="http://127.0.0.1:9999/")
    try:
        assert panel.status()["viz_url"] == "http://127.0.0.1:9999/"
    finally:
        panel.stop()


def test_panel_page_hides_the_link_until_one_is_known():
    """The static page ships the anchor hidden; /version turns it on."""
    from skeletonkey.live import panel as panel_mod

    src = (__import__("pathlib").Path(panel_mod.__file__)).read_text(encoding="utf-8")
    assert 'id="vizlink"' in src
    assert "display:none" in src
    assert "v.viz_url" in src


def test_live_serve_advertises_the_knob(toolkit):
    """The tool schema must document viz_url, or agents cannot use it."""
    manifest = toolkit.engine.registry.get("live.serve")
    props = manifest.input_schema["properties"]
    assert "viz_url" in props
    assert "link" in props["viz_url"]["description"].lower()


def test_the_cli_exposes_panel_flag():
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-m", "skeletonkey.cli", "viz", "--help"],
                         capture_output=True, text=True, cwd=repo, timeout=120).stdout
    assert "--panel" in out
