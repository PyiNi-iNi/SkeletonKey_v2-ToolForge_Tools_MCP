"""V2 of the tool visualizer: the served catalog.

What a served page must get right, and what these tests hold it to:

* the API and the page agree with `sk viz --json` - one model, three surfaces;
* it notices drift: install a tool and `/api/digest` changes on the next poll,
  without a restart;
* it is read-only *structurally* - no `do_POST` exists, so no mutating route
  can be found, and serving never perturbs the registry;
* start/stop is clean - no leaked thread, no held socket, so an operator can
  run it in a loop and a test suite can too;
* a bad request gets an answer, not a corpse: unknown routes 404 with the route
  list, and a broken model 500s while the server keeps serving.

Everything binds to port 0 on loopback and is torn down by a fixture.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from skeletonkey.core.manifest import ToolManifest
from skeletonkey.viz import build_viz_model
from skeletonkey.viz.server import VizServer, serve


@pytest.fixture
def server(toolkit):
    srv = VizServer(toolkit.engine.registry, port=0, poll_ms=50)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


def get(server: VizServer, route: str) -> tuple[int, bytes, dict]:
    try:
        with urllib.request.urlopen(server.url.rstrip("/") + route, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def get_json(server: VizServer, route: str) -> tuple[int, dict]:
    status, body, _ = get(server, route)
    return status, json.loads(body)


# ------------------------------------------------------------------ lifecycle

def test_starts_on_a_free_port_and_reports_itself(server):
    st = server.status()
    assert st["serving"] is True
    assert st["port"] == server.port > 0
    assert server.url.startswith("http://127.0.0.1:")
    assert st["uptime_s"] >= 0


def test_stop_releases_the_thread_and_the_port(toolkit):
    before = {t.name for t in threading.enumerate()}
    srv = VizServer(toolkit.engine.registry, port=0)
    srv.start()
    port = srv.port
    assert get(srv, "/healthz")[0] == 200
    srv.stop()

    assert srv.status()["serving"] is False
    assert {t.name for t in threading.enumerate()} <= before | {"sk-viz"}
    assert not any(t.name == "sk-viz" and t.is_alive() for t in threading.enumerate())
    # The port is genuinely free again: rebinding must not raise. SO_REUSEADDR
    # is set because the *client* socket from the /healthz call above sits in
    # TIME_WAIT for a minute or so - that is the kernel being careful about a
    # closed connection, not the server still holding the listener, and
    # ThreadingHTTPServer sets the same flag so a real restart works too.
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))
    finally:
        probe.close()


def test_start_is_idempotent_and_context_manager_stops(toolkit):
    with VizServer(toolkit.engine.registry, port=0) as srv:
        first = srv.port
        assert srv.start()["port"] == first, "a second start must not rebind"
        assert get(srv, "/healthz")[0] == 200
    assert srv.status()["serving"] is False


def test_serve_helper_returns_a_running_server(toolkit):
    srv = serve(toolkit.engine.registry, port=0)
    try:
        assert srv.status()["serving"] is True
        assert get(srv, "/healthz")[0] == 200
    finally:
        srv.stop()


def test_binds_loopback_by_default(server):
    assert server.host == "127.0.0.1"


# --------------------------------------------------------------------- routes

def test_healthz(server):
    status, doc = get_json(server, "/healthz")
    assert status == 200
    assert doc["ok"] is True
    assert doc["serving"] is True


def test_api_model_is_the_same_document_as_the_library_call(server, toolkit):
    status, doc = get_json(server, "/api/model")
    assert status == 200
    expected = build_viz_model(toolkit.engine.registry)
    assert doc["model_digest"] == expected.digest()
    assert len(doc["nodes"]) == len(expected.nodes)
    assert len(doc["edges"]) == len(expected.edges)
    assert doc["schema_version"] == 1


def test_api_digest_is_cheap_and_consistent_with_the_model(server):
    status, dig = get_json(server, "/api/digest")
    assert status == 200
    _, model_doc = get_json(server, "/api/model")
    assert dig["model_digest"] == model_doc["model_digest"]
    assert dig["registered"] == model_doc["aggregates"]["registered"]
    assert dig["advertised"] == model_doc["aggregates"]["advertised"]
    assert len(json.dumps(dig)) < 400, "the poll route must stay small"


def test_api_tool_returns_the_node_and_its_edges(server):
    status, doc = get_json(server, "/api/tool/fs.read")
    assert status == 200
    assert doc["tool"]["id"] == "fs.read"
    assert doc["tool"]["input_schema"]
    for edge in doc["edges"]:
        assert "fs.read" in (edge["source"], edge["target"])


def test_unknown_tool_and_unknown_route_are_answered_not_crashed(server):
    status, doc = get_json(server, "/api/tool/nope.nothing")
    assert status == 404
    assert doc["id"] == "nope.nothing"

    status, doc = get_json(server, "/wat")
    assert status == 404
    assert "/api/model" in doc["routes"], "a 404 should teach the caller the real routes"
    # the server is still alive after both
    assert get(server, "/healthz")[0] == 200


def test_page_is_served_and_carries_the_live_config(server):
    status, body, headers = get(server, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert headers["Cache-Control"] == "no-store"
    page = body.decode("utf-8")
    assert page.startswith("<!doctype html>")
    cfg = json.loads(page.split('<script id="viz-live" type="application/json">', 1)[1]
                     .split("</script>", 1)[0])
    assert cfg["live"] is True
    assert cfg["pollMs"] == 50
    assert cfg["digest"]


def test_served_page_and_written_file_use_the_same_renderer(server, toolkit):
    from skeletonkey.viz import render_html

    served = get(server, "/")[1].decode("utf-8")
    offline = render_html(build_viz_model(toolkit.engine.registry))
    # identical but for the live block: same cards, same count, one renderer
    assert served.count('<article class="card') == offline.count('<article class="card')
    assert '"live":true' in served.replace(" ", "")
    assert '"live":false' in offline.replace(" ", "")


def test_trailing_slashes_are_tolerated(server):
    assert get(server, "/healthz/")[0] == 200
    assert get(server, "/api/digest/")[0] == 200


# ----------------------------------------------------------------------- drift

def test_digest_changes_when_a_tool_is_registered_without_restarting(server, toolkit):
    before = get_json(server, "/api/digest")[1]
    toolkit.engine.registry.register(
        ToolManifest(id="viz.hotplug", description="Registered while serving.",
                     risk="none"),
        handler=lambda **kw: None,
    )
    after = get_json(server, "/api/digest")[1]

    assert after["model_digest"] != before["model_digest"]
    assert after["registered"] == before["registered"] + 1
    # and the page rebuilt too - the model is derived per request, never cached
    assert "viz.hotplug" in get(server, "/")[1].decode("utf-8")
    assert get_json(server, "/api/tool/viz.hotplug")[0] == 200


def test_digest_is_stable_when_nothing_changes(server):
    a = get_json(server, "/api/digest")[1]
    b = get_json(server, "/api/digest")[1]
    assert a["model_digest"] == b["model_digest"]
    assert a["at"] != b["at"] or True   # `at` is a clock, not part of the identity


# ------------------------------------------------------------------ read-only

def test_there_is_no_mutating_route_at_all(server):
    handler = server._handler_class()
    assert not hasattr(handler, "do_POST"), "a POST handler would be a mutation surface"
    assert not hasattr(handler, "do_PUT")
    assert not hasattr(handler, "do_DELETE")
    assert not hasattr(handler, "do_PATCH")

    req = urllib.request.Request(server.url, data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=10)
    assert exc.value.code in (400, 405, 501)
    assert get(server, "/healthz")[0] == 200


def test_serving_does_not_perturb_the_registry(server, toolkit):
    reg = toolkit.engine.registry
    before_ids = sorted(m.id for m in reg.all())
    before_digest = reg.advertise().digest
    for route in ("/", "/api/model", "/api/digest", "/api/tool/fs.read", "/healthz"):
        get(server, route)
    assert sorted(m.id for m in reg.all()) == before_ids
    assert reg.advertise().digest == before_digest


def test_read_only_and_tier_projections_are_served(toolkit):
    with VizServer(toolkit.engine.registry, port=0, read_only=True) as srv:
        doc = get_json(srv, "/api/model")[1]
        assert doc["host"]["read_only"] is True
        assert all(not n["advertised"] for n in doc["nodes"] if n["mutating"])
    with VizServer(toolkit.engine.registry, port=0, tier="core") as srv:
        doc = get_json(srv, "/api/model")[1]
        assert doc["snapshot"]["tier"] == "core"


# ----------------------------------------------------------------- resilience

def test_a_failing_model_becomes_a_500_not_a_dead_server(server, monkeypatch):
    monkeypatch.setattr(server, "model",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    status, doc = get_json(server, "/api/model")
    assert status == 500
    assert doc["error"] == "RuntimeError"
    assert doc["message"] == "boom"

    monkeypatch.undo()
    assert get(server, "/api/model")[0] == 200, "the server survived the failure"


def test_concurrent_requests_are_served(server):
    results: list[int] = []
    lock = threading.Lock()

    def hit() -> None:
        status = get(server, "/api/digest")[0]
        with lock:
            results.append(status)

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert results == [200] * 8


def test_request_counter_moves(server):
    before = server.status()["requests"]
    get(server, "/healthz")
    get(server, "/api/digest")
    assert server.status()["requests"] >= before + 2
