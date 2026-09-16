"""Tiny async HTTP/1.1 server built on stdlib ``asyncio`` only.

Serves API-facing features (api_proxy, db_manager, ui_manager auth) with
no third-party dependency and full streaming support (chunked transfer for
SSE / audio / image pipelines). The client side uses stdlib ``http.client``
in tests — no aiohttp needed.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlsplit

from .errors import FeatureError, ValidationError, from_error

__all__ = ["Request", "Response", "Handler", "HttpServer", "json_response", "error_response"]


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]  # lower-cased keys
    body: bytes
    version: str = "HTTP/1.1"

    @property
    def query_first(self) -> dict[str, str]:
        return {k: v[0] for k, v in self.query.items()}

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValidationError(f"request body is not valid JSON: {exc.msg}") from exc

    def header(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name.lower(), default)

    def bearer_token(self) -> str | None:
        auth = self.header("authorization")
        if not auth:
            return None
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return None


@dataclass
class Response:
    status: int
    body: bytes | None = None
    headers: list[tuple[str, str]] = field(default_factory=list)
    stream: Any = None  # AsyncIterator[bytes]; when set, body is ignored

    def add_header(self, name: str, value: str) -> "Response":
        self.headers.append((name, value))
        return self

    def headers_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.headers}


Handler = Callable[[Request], Awaitable[Response]]


def json_response(payload: Any, status: int = 200) -> Response:
    return Response(
        status=status,
        body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=[("Content-Type", "application/json"), ("Cache-Control", "no-store")],
    )


def error_response(status: int, exc: Exception) -> Response:
    return json_response({"error": from_error(exc, status)}, status=status)


class HttpServer:
    """Asyncio HTTP/1.1 server with keep-alive and chunked streaming."""

    def __init__(self, handler: Handler, host: str = "0.0.0.0", port: int = 0,
                 max_body: int = 32 * 1024 * 1024):
        self._handler = handler
        self._host = host
        self._port = port
        self._max_body = max_body
        self._server: asyncio.AbstractServer | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            return self._port
        sock = self._server.sockets[0]
        return sock.getsockname()[1]

    @property
    def host(self) -> str:
        return self._host

    async def start(self) -> "HttpServer":
        self._server = await asyncio.start_server(self._on_conn, self._host, self._port)
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # -- internals ---------------------------------------------------------

    async def _read_line(self, reader: asyncio.StreamReader) -> bytes:
        line = await asyncio.wait_for(reader.readuntil(b"\n"), timeout=30)
        return line

    async def _on_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                request = await self._parse_request(reader)
                if request is None:
                    return
                response = await self._dispatch(request)
                await self._write_response(writer, response)
                keep = response.headers_dict().get("connection", "keep-alive").lower()
                if keep == "close" or reader.at_eof():
                    break
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            pass
        except ConnectionResetError:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _parse_request(self, reader: asyncio.StreamReader) -> Request | None:
        raw_line = await self._read_line(reader)
        if not raw_line:
            return None
        line = raw_line.rstrip(b"\r\n").decode("latin-1")
        parts = line.split()
        if len(parts) != 3 or not parts[2].startswith("HTTP/"):
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return None
        method, target, version = parts
        headers: dict[str, str] = {}
        while True:
            hline = await self._read_line(reader)
            if hline in (b"\r\n", b"\n"):
                break
            if b":" not in hline:
                continue
            k, v = hline.split(b":", 1)
            headers[k.decode("latin-1").strip().lower()] = v.decode("latin-1").strip()
        length = int(headers.get("content-length", "0") or "0")
        if length < 0 or length > self._max_body:
            return Request(method, target, {}, headers, b"", version)
        body = b""
        if length:
            body = await asyncio.wait_for(reader.readexactly(length), timeout=30)
        split = urlsplit(target)
        return Request(method, split.path, parse_qs(split.query), headers, body, version)

    async def _dispatch(self, request: Request) -> Response:
        try:
            return await self._handler(request)
        except FeatureError as exc:
            return error_response(exc.http_status, exc)
        except Exception as exc:  # noqa: BLE001 - final boundary
            return error_response(500, exc)

    async def _write_response(self, writer: asyncio.StreamWriter, response: Response) -> None:
        reason = {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request",
                  401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
                  405: "Method Not Allowed", 409: "Conflict", 422: "Unprocessable Entity",
                  500: "Internal Server Error", 501: "Not Implemented",
                  502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout"}.get(
            response.status, "OK"
        )
        if response.stream is not None:
            head = f"HTTP/1.1 {response.status} {reason}\r\n"
            hd = dict(response.headers)
            hd.setdefault("Transfer-Encoding", "chunked")
            hd.setdefault("Connection", "keep-alive")
            head += "".join(f"{k}: {v}\r\n" for k, v in hd.items()) + "\r\n"
            writer.write(head.encode("latin-1"))
            await writer.drain()
            try:
                async for chunk in response.stream:
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    if not chunk:
                        continue
                    writer.write(f"{len(chunk):x}\r\n".encode("latin-1") + chunk + b"\r\n")
                    await writer.drain()
            except Exception:  # noqa: BLE001 - client went away mid-stream
                return
            writer.write(b"0\r\n\r\n")
            await writer.drain()
            return
        body = response.body or b""
        hd = dict(response.headers)
        hd.setdefault("Connection", "keep-alive")
        if "content-length" not in {k.lower() for k, _ in response.headers}:
            hd["Content-Length"] = str(len(body))
        head = f"HTTP/1.1 {response.status} {reason}\r\n"
        head += "".join(f"{k}: {v}\r\n" for k, v in hd.items()) + "\r\n"
        writer.write(head.encode("latin-1") + body)
        await writer.drain()
