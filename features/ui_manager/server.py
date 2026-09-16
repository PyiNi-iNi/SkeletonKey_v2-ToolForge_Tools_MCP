"""HTTP surface for ui_manager auth (native login + OAuth device grant).

Endpoints:
  POST /auth/register   {username, password}            -> 201
  POST /auth/login      {username, password}            -> {token, username}
  POST /auth/logout     (Bearer token)                  -> 204
  GET  /auth/me         (Bearer token)                  -> {username}
  POST /device/code     {username}                      -> {device_code, user_code}
  POST /device/approve  {user_code}                     -> 200
  POST /device/token    {device_code}                   -> {status, token?}
                                                             424 while pending
  GET  /health                                                 -> {ok}
"""

from __future__ import annotations

from features.common.errors import AuthError, ValidationError
from features.common.http import Handler, HttpServer, Request, Response, json_response

from .auth import DeviceFlow, NativeAuth

__all__ = ["make_auth_server", "run_auth_server"]


def make_auth_server(auth: NativeAuth, devices: DeviceFlow | None = None) -> Handler:
    devices = devices or DeviceFlow(auth)

    async def handler(req: Request) -> Response:
        route = (req.method, req.path)
        if route == ("GET", "/health"):
            return json_response({"ok": True})
        if route == ("POST", "/auth/register"):
            body = req.json() or {}
            auth.add_user(body.get("username", ""), body.get("password", ""))
            return json_response({"status": "created"}, status=201)
        if route == ("POST", "/auth/login"):
            body = req.json() or {}
            token = auth.login(body.get("username", ""), body.get("password", ""))
            return json_response({"token": token, "username": body["username"]})
        if route == ("POST", "/auth/logout"):
            token = req.bearer_token()
            if token:
                auth.logout(token)
            return Response(status=204)
        if route == ("GET", "/auth/me"):
            username = auth.whoami(req.bearer_token() or "")
            if username is None:
                raise AuthError("invalid or expired session")
            return json_response({"username": username})
        if route == ("POST", "/device/code"):
            body = req.json() or {}
            device_code, user_code = devices.create_code(body.get("username", ""))
            return json_response({"device_code": device_code, "user_code": user_code,
                                  "interval": 1, "expires_in": devices.ttl})
        if route == ("POST", "/device/approve"):
            body = req.json() or {}
            devices.approve(body.get("user_code", ""))
            return json_response({"status": "approved"})
        if route == ("POST", "/device/token"):
            body = req.json() or {}
            result = devices.poll(body.get("device_code", ""))
            if result["status"] == "approved":
                return json_response(result)
            if result["status"] == "expired":
                raise ValidationError("device code expired")
            # pending -> HTTP 424 (RFC 8628 'authorization_pending')
            return json_response(result, status=424)
        return json_response({"error": {"code": "not_found",
                                        "message": f"no route for {req.method} {req.path}"}},
                             status=404)

    return handler


async def run_auth_server(auth: NativeAuth, devices: DeviceFlow | None = None,
                          host: str = "127.0.0.1", port: int = 8771) -> HttpServer:
    server = HttpServer(make_auth_server(auth, devices), host=host, port=port)
    await server.start()
    return server
