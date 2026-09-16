"""HTTP layer for the OpenAI-compatible proxy (features.common.http).

Endpoints:
  POST /v1/chat/completions   stream (SSE) + non-stream, tools, JSON mode
  POST /v1/completions        stream + non-stream
  POST /v1/embeddings
  GET  /v1/models
  GET  /health
"""

from __future__ import annotations

from features.common.errors import AuthError
from features.common.http import Handler, HttpServer, Request, Response, json_response
from features.common.openai_compat import sse_done, sse_event

from .proxy import OpenAIProxy

__all__ = ["make_proxy_server", "run_proxy_server"]


def make_proxy_server(proxy: OpenAIProxy, token: str | None = None) -> Handler:
    async def handler(req: Request) -> Response:
        if token and req.bearer_token() != token:
            raise AuthError("missing or invalid bearer token")
        route = (req.method, req.path)
        if route == ("GET", "/health"):
            return json_response({"ok": True, "models": proxy.engine.model_ids()})
        if route == ("GET", "/v1/models"):
            return json_response({"object": "list",
                                  "data": [m.to_dict() for m in proxy.models()]})
        if route == ("POST", "/v1/chat/completions"):
            body = req.json() or {}
            if body.get("stream"):
                async def sse():
                    async for chunk in proxy.stream_chat(body):
                        yield sse_event(chunk)
                    yield sse_done()

                return Response(status=200, stream=sse(),
                                headers=[("Content-Type", "text/event-stream"),
                                         ("X-Accel-Buffering", "no")])
            return json_response(await proxy.chat(body))
        if route == ("POST", "/v1/completions"):
            body = req.json() or {}
            if body.get("stream"):
                async def sse_c():
                    async for chunk in proxy.stream_completions(body):
                        yield sse_event(chunk)
                    yield sse_done()

                return Response(status=200, stream=sse_c(),
                                headers=[("Content-Type", "text/event-stream"),
                                         ("X-Accel-Buffering", "no")])
            return json_response(await proxy.completions(body))
        if route == ("POST", "/v1/embeddings"):
            return json_response(await proxy.embeddings(req.json() or {}))
        return json_response(
            {"error": {"message": f"unhandled route {req.method} {req.path}",
                       "type": "invalid_request_error", "code": None}}, status=404)

    return handler


async def run_proxy_server(proxy: OpenAIProxy, host: str = "0.0.0.0", port: int = 8780,
                           token: str | None = None) -> HttpServer:
    server = HttpServer(make_proxy_server(proxy, token), host=host, port=port)
    await server.start()
    return server
