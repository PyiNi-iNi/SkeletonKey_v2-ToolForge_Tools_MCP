"""Optional local REST surface for the private DB.

Binds to ``SK_DB_HOST``/``SK_DB_PORT`` (default 127.0.0.1:8770) and, when
``SK_DB_TOKEN`` is set, requires ``Authorization: Bearer <token>``.

Endpoints (OpenAI-style envelope, JSON):
  POST /v1/ingest      {namespace, texts, metadatas?, ids?} -> {ids, count}
  POST /v1/query       {namespace, query, n?, where?}       -> {hits: [...]}
  POST /v1/query_vec   {namespace, embedding, n?, where?}   -> {hits: [...]}
  GET  /v1/namespaces  -> {namespaces: [...]}
  GET  /health         -> {ok, backend, dim}
"""

from __future__ import annotations

import asyncio

from features.common.errors import AuthError, ValidationError
from features.common.http import Handler, HttpServer, Request, Response, json_response

from .store import DBManager

__all__ = ["make_db_server", "run_db_server"]


def make_db_server(db: DBManager, token: str | None = None) -> Handler:
    async def handler(req: Request) -> Response:
        if token and req.bearer_token() != token:
            raise AuthError("missing or invalid bearer token")
        route = (req.method, req.path)
        if route == ("GET", "/health"):
            return json_response({"ok": True, "backend": db.backend, "dim": db.dim})
        if route == ("GET", "/v1/namespaces"):
            return json_response({"namespaces": db.namespaces()})
        if route == ("POST", "/v1/ingest"):
            body = req.json() or {}
            texts = body.get("texts") or []
            if isinstance(texts, str):
                texts = [texts]
            ns = body.get("namespace", "default")
            ids = await db.collection(ns).aadd_texts(
                texts,
                metadatas=body.get("metadatas"),
                ids=body.get("ids"),
            )
            return json_response({"ids": ids, "count": len(ids), "namespace": ns})
        if route == ("POST", "/v1/query"):
            body = req.json() or {}
            ns = body.get("namespace", "default")
            hits = await db.collection(ns).aquery_texts(
                body.get("query", ""), n=int(body.get("n", 5)), where=body.get("where")
            )
            return json_response({"hits": hits, "namespace": ns})
        if route == ("POST", "/v1/query_vec"):
            body = req.json() or {}
            ns = body.get("namespace", "default")
            embedding = body.get("embedding")
            if not isinstance(embedding, list) or not embedding:
                raise ValidationError("embedding must be a non-empty list of numbers")
            hits = db.collection(ns).query(embedding, n=int(body.get("n", 5)),
                                           where=body.get("where"))
            return json_response({"hits": [h.to_dict() for h in hits], "namespace": ns})
        return json_response({"error": {"code": "not_found",
                                        "message": f"no route for {req.method} {req.path}"}},
                             status=404)

    return handler


async def run_db_server(db: DBManager, host: str = "127.0.0.1", port: int = 8770,
                        token: str | None = None) -> HttpServer:
    server = HttpServer(make_db_server(db, token), host=host, port=port)
    await server.start()
    return server
