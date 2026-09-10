"""Mounting the read-only tool catalog inside another HTTP server.

ADR-0018 supersedes ADR-0017's "two servers" decision: the catalog is now
served from the live panel under `/tools`, so one process and one port give
you the whole dev viewport.

That merge costs a real guarantee and this module exists to buy back as much
of it as is possible. Previously "can the catalog mutate anything?" was
answered by the *absence* of `do_POST` on its handler class - a fact a
reviewer could check without understanding the code, and that no future edit
to a routing table could quietly undo. Inside a host that does have `do_POST`
(the panel's REPL executes code), that absence is gone.

What replaces it:

1. **One explicit allowlist.** `ROUTES` is the complete set of paths this
   mount answers. It is data, not control flow, so a reviewer reads one tuple
   instead of tracing a router.
2. **GET/HEAD only, enforced here.** `dispatch()` takes the method and refuses
   anything else itself, rather than trusting the host to only call it from
   `do_GET`. A host that wires it into `do_POST` by mistake gets a 405, not a
   mutation.
3. **No handles to anything mutable.** The mount is constructed with a
   zero-argument callable returning a registry. It cannot reach the engine,
   the toolkit, the live manager or the REPL, so there is no object graph to
   walk toward a write even if a future route is added carelessly.
4. **The host must prove the guard.** `tests/test_viz_mount.py` asserts the
   panel rejects every write verb under the mount prefix.

This is weaker than structural absence. It is a promise enforced by tests and
a guard clause rather than by the shape of the class, and that difference is
recorded honestly in ADR-0018.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlparse

from .explain import explain_model
from .model import build_viz_model
from .render import render_html
from .runtime import attach_runtime

#: Every path this mount will answer, relative to its prefix. The complete
#: surface, as data: if it is not in here, it 404s.
ROUTES: tuple[str, ...] = (
    "",              # the page itself (prefix with and without a trailing /)
    "/",
    "/api/model",
    "/api/digest",
    "/api/explain",
    "/api/runtime",
    "/api/tool/",    # prefix match, the only one
    "/healthz",
)

#: Methods the catalog answers. Everything else is refused by `dispatch`
#: itself so a miswired host cannot turn a read into a write.
SAFE_METHODS = frozenset({"GET", "HEAD"})


class VizMount:
    """A read-only catalog mounted under `prefix` inside a host server.

    `registry_provider` is a zero-argument callable so the mount holds no
    reference to a toolkit or engine - only to the thing it projects. The
    model is rebuilt per request for the same reason the standalone server
    rebuilds it: a served page that can go stale is worse than no page.
    """

    def __init__(self, registry_provider: Callable[[], Any], *,
                 prefix: str = "/tools", title: str = "SkeletonKey toolset",
                 tier: str | None = None, read_only: bool = False,
                 poll_ms: int = 2000,
                 ledger_provider: Callable[[], Any] | None = None,
                 panel_url: str | None = None) -> None:
        self.registry_provider = registry_provider
        self.ledger_provider = ledger_provider
        self.prefix = "/" + prefix.strip("/")
        self.title = title
        self.tier = tier
        self.read_only = read_only
        self.poll_ms = int(poll_ms)
        self.panel_url = panel_url
        self.requests = 0

    # ------------------------------------------------------------------ model
    def model(self) -> Any:
        model = build_viz_model(self.registry_provider(), tier=self.tier,
                                read_only=self.read_only)
        if self.ledger_provider is not None:
            ledger = self.ledger_provider()
            if ledger is not None:
                try:
                    attach_runtime(model, ledger)
                except Exception:       # an overlay is never load-bearing
                    pass
        return model

    def chains(self) -> dict[str, Any]:
        try:
            return explain_model(self.registry_provider(), tier=self.tier,
                                 read_only=self.read_only)
        except Exception:
            return {}

    # ---------------------------------------------------------------- routing
    def owns(self, path: str) -> bool:
        """Does this mount claim `path`? Used by the host to route, and by the
        host's write verbs to refuse."""
        route = urlparse(path).path.rstrip("/") or "/"
        return route == self.prefix or route.startswith(self.prefix + "/")

    def _relative(self, path: str) -> str:
        route = urlparse(path).path
        rel = route[len(self.prefix):]
        if rel != "/" and rel.endswith("/"):
            rel = rel.rstrip("/")
        return rel

    def dispatch(self, method: str, path: str) -> tuple[int, bytes, str]:
        """Answer a request. Returns (status, body, content_type).

        Refuses non-safe methods itself: the host is not trusted to have
        called this only from a read verb.
        """
        self.requests += 1
        if method.upper() not in SAFE_METHODS:
            return self._json_body(405, {
                "error": "the tool catalog is read-only",
                "method": method,
                "allowed": sorted(SAFE_METHODS),
            })

        rel = self._relative(path)
        try:
            if rel in ("", "/"):
                page = render_html(self.model(), title=self.title, live=True,
                                   poll_ms=self.poll_ms, chains=self.chains(),
                                   panel_url=self.panel_url,
                                   api_base=self.prefix)
                return 200, page.encode("utf-8"), "text/html; charset=utf-8"
            if rel == "/api/model":
                model = self.model()
                doc = model.to_json()
                doc["model_digest"] = model.digest()
                return self._json_body(200, doc)
            if rel == "/api/runtime":
                return self._json_body(200, self.model().runtime or
                                       {"available": False, "drift": []})
            if rel == "/api/explain":
                return self._json_body(200, {k: v.to_json()
                                             for k, v in self.chains().items()})
            if rel == "/api/digest":
                model = self.model()
                return self._json_body(200, {"digest": model.snapshot["digest"],
                                             "model_digest": model.digest(),
                                             "registered": model.aggregates["registered"],
                                             "advertised": model.aggregates["advertised"]})
            if rel.startswith("/api/tool/"):
                tool_id = unquote(rel[len("/api/tool/"):])
                model = self.model()
                node = next((n for n in model.nodes if n.id == tool_id), None)
                if node is None:
                    return self._json_body(404, {"error": "no such tool", "id": tool_id})
                return self._json_body(200, {
                    "tool": node.to_json(),
                    "edges": [e.to_json() for e in model.edges
                              if e.source == tool_id or e.target == tool_id]})
            if rel == "/healthz":
                return self._json_body(200, {"ok": True, "mounted_at": self.prefix,
                                             "requests": self.requests,
                                             "read_only": True})
        except Exception as exc:        # a broken page beats a dead host
            return self._json_body(500, {"error": type(exc).__name__, "message": str(exc)})

        return self._json_body(404, {"error": "no such route", "path": rel,
                                     "routes": [self.prefix + r for r in ROUTES]})

    @staticmethod
    def _json_body(status: int, obj: Any) -> tuple[int, bytes, str]:
        data = json.dumps(obj, default=str).encode("utf-8", "replace")
        return status, data, "application/json; charset=utf-8"
