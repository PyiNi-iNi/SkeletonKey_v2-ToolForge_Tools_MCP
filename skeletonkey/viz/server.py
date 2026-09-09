"""V2: serve the catalog, and notice when the toolset changes underneath it.

A static page (V1) is a photograph. This is the same page with a pulse: it
re-derives the model per request, and the browser polls a cheap digest route so
that installing a skill, reloading a module under HMR, or re-probing the host
shows up as a banner instead of silent drift.

Routes:

  GET /              the catalog page (V1's renderer, `live=True`)
  GET /api/model     the whole V0 model as JSON
  GET /api/digest    {model_digest, snapshot_digest, registered, advertised, at}
                     - the cheap poll; the page compares it to what it rendered
  GET /api/tool/<id> one node, plus its edges
  GET /healthz       {ok, uptime_s, requests}

Threading mirrors `live/panel.py`: `ThreadingHTTPServer` on a daemon thread,
`shutdown()` + `server_close()` + join on stop, so a test can start and stop
dozens of these without leaking a thread or a socket.

The server is **read-only**, and not merely by convention: there is no `do_POST`
at all, so a mutating route does not exist to be found. That is the V5 gate
(`--allow-calls`) pre-decided in the negative, which is the safe default for
something an operator might leave running.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .model import build_viz_model
from .render import render_html


class VizServer:
    """Serves one registry's toolset. Rebuilds the model per request.

    Rebuilding rather than caching is a deliberate cost: the model is a pure
    function of the registry and takes milliseconds, and a served page that can
    go stale is worse than no server at all.
    """

    def __init__(self, registry: Any, *, host: str = "127.0.0.1", port: int = 0,
                 title: str = "SkeletonKey toolset", tier: str | None = None,
                 read_only: bool = False, poll_ms: int = 2000) -> None:
        self.registry = registry
        self.host = host
        self.title = title
        self.tier = tier
        self.read_only = read_only
        self.poll_ms = int(poll_ms)
        self.started_at = time.time()
        self.requests = 0
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer((host, int(port)), self._handler_class())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ state
    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        shown = "127.0.0.1" if self.host in ("0.0.0.0", "", "::") else self.host
        return f"http://{shown}:{self.port}/"

    def start(self) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return self.status()
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="sk-viz", daemon=True,
                                        kwargs={"poll_interval": 0.3})
        self._thread.start()
        return self.status()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3.0)
        self._thread = None

    def __enter__(self) -> VizServer:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def status(self) -> dict[str, Any]:
        return {"serving": bool(self._thread and self._thread.is_alive()),
                "host": self.host, "port": self.port, "url": self.url,
                "requests": self.requests,
                "uptime_s": round(time.time() - self.started_at, 1)}

    # ------------------------------------------------------------------ model
    def model(self) -> Any:
        # The registry is mutable from other threads (skill install, HMR): build
        # under a lock so two in-flight requests cannot interleave a rebuild.
        with self._lock:
            return build_viz_model(self.registry, tier=self.tier,
                                   read_only=self.read_only)

    def digest_payload(self) -> dict[str, Any]:
        """The cheap poll. Still an honest full build - correctness over ceremony."""
        model = self.model()
        return {"model_digest": model.digest(),
                "snapshot_digest": model.snapshot["digest"],
                "registered": model.aggregates["registered"],
                "advertised": model.aggregates["advertised"],
                "tokens": model.snapshot["tokens"],
                "at": round(time.time(), 3)}

    def tool_payload(self, tool_id: str) -> dict[str, Any] | None:
        model = self.model()
        node = model.node(tool_id)
        if node is None:
            return None
        return {"tool": node.to_json(),
                "edges": [e.to_json() for e in model.edges
                          if e.source == tool_id or e.target == tool_id]}

    # ----------------------------------------------------------------- server
    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        viz = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "SkeletonKeyViz/1"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:   # quiet by design
                return

            def _send(self, status: int, body: bytes, ctype: str) -> None:
                viz.requests += 1
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                # The page is same-origin and carries no credentials; CORS open
                # so a dashboard can read /api/*. There is nothing here to
                # protect that reading the tool list does not already reveal.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, obj: Any) -> None:
                data = json.dumps(obj, default=str).encode("utf-8", "replace")
                self._send(status, data, "application/json; charset=utf-8")

            def do_GET(self) -> None:
                from urllib.parse import unquote, urlparse

                route = urlparse(self.path).path
                route = route.rstrip("/") or "/"
                try:
                    if route == "/":
                        page = render_html(viz.model(), title=viz.title,
                                           live=True, poll_ms=viz.poll_ms)
                        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                    elif route == "/api/model":
                        model = viz.model()
                        doc = model.to_json()
                        doc["model_digest"] = model.digest()
                        self._json(200, doc)
                    elif route == "/api/digest":
                        self._json(200, viz.digest_payload())
                    elif route.startswith("/api/tool/"):
                        tool_id = unquote(route[len("/api/tool/"):])
                        payload = viz.tool_payload(tool_id)
                        if payload is None:
                            self._json(404, {"error": "no such tool", "id": tool_id})
                        else:
                            self._json(200, payload)
                    elif route == "/healthz":
                        self._json(200, {"ok": True, **viz.status()})
                    else:
                        self._json(404, {"error": "no such route", "path": route,
                                         "routes": ["/", "/api/model", "/api/digest",
                                                    "/api/tool/<id>", "/healthz"]})
                except Exception as exc:            # a broken page beats a dead server
                    self._json(500, {"error": type(exc).__name__, "message": str(exc)})

            def do_HEAD(self) -> None:
                self.do_GET()

        return Handler


def serve(registry: Any, *, host: str = "127.0.0.1", port: int = 0,
          **kwargs: Any) -> VizServer:
    """Start a VizServer and return it (already serving)."""
    server = VizServer(registry, host=host, port=port, **kwargs)
    server.start()
    return server
