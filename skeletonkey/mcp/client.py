"""P5b mcp client connector (ADR-0013): remote servers as local manifests.

Servers configured under ``[mcp.remotes.<name>]`` are enrolled at build time
(``toolkit.build``) as ``remote.<name>.<tool>`` manifests: risk inherited from
the remote tool's annotations (unannotated => ``write``, never lowered),
``reversible: false``, ``stateful: "host"``, ``source: "remote:<name>"``. A
skeletonkey-shaped remote result is passed through un-wrapped - a remote
``BAD_ARGS`` stays ``BAD_ARGS`` - and a non-skeletonkey remote error maps to the
``REMOTE`` code. A server that fails to connect or handshake is an entry in the
registry's ``load_errors`` (and the build report), never a silent absence.

Threading: each server gets one thread + its own asyncio event loop, so the
*synchronous* engine can call it (if a remote tool handler were async, every
engine call would need a loop it does not have). The ``mcp`` package is imported
lazily inside the worker: the core stays importable without the optional extra,
and a toolkit with no ``mcp.remotes`` never imports it at all.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from ..core.envelope import ToolError, ToolResult
from ..core.errors import E, SkeletonKeyError
from ..core.manifest import ToolManifest

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# The engine's schema validator is a JSON-schema subset (docs: subset
# validator); a remote server may send us anything, so only the keys the
# validator understands are kept - a foreign `$defs` must not break gating.
_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties",
                "items", "description", "default", "enum", "minimum", "maximum",
                "minLength", "maxLength", "pattern", "oneOf", "anyOf", "title",
                "minItems", "maxItems", "const"}
_SPEC_KEYS = {"command", "args", "url", "enabled", "timeout_s"}


@dataclass
class RemoteSpec:
    """One configured remote server (validated, never guessed from a raw dict)."""

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    enabled: bool = True
    timeout_s: float = 30.0

    @classmethod
    def from_config(cls, name: str, data: dict[str, Any] | None) -> RemoteSpec:
        data = dict(data or {})
        if not _NAME_RE.match(name):
            raise ValueError(f"remote server name {name!r} must match [a-z0-9][a-z0-9_-]{{0,31}}")
        unknown = set(data) - _SPEC_KEYS
        if unknown:
            raise ValueError(f"unknown keys for remote {name!r}: {sorted(unknown)}")
        spec = cls(
            name=name,
            command=str(data.get("command") or ""),
            args=[str(a) for a in data.get("args") or []],
            url=str(data.get("url") or ""),
            enabled=bool(data.get("enabled", True)),
            timeout_s=float(data.get("timeout_s") or 30.0),
        )
        if not spec.command and not spec.url:
            raise ValueError(f"remote {name!r} needs a `command` (stdio) or a `url`")
        if spec.command and spec.url:
            raise ValueError(f"remote {name!r}: `command` and `url` are mutually exclusive")
        if spec.command and (not spec.args or not spec.args[0]):
            raise ValueError(f"remote {name!r}: command needs its argv[0] in `args`")
        return spec

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "command": self.command or None,
                "url": self.url or None, "enabled": self.enabled,
                "timeout_s": self.timeout_s, "tools": 0}


class RemoteServer:
    """One live connection to one remote server (thread + its own event loop)."""

    def __init__(self, spec: RemoteSpec) -> None:
        self.spec = spec
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._closing = threading.Event()

    # --------------------------------------------------------------- lifecycle
    def connect(self) -> None:
        """Blocking connect + handshake; raises SkeletonKeyError on failure."""
        self._closing.clear()
        self._thread = threading.Thread(target=self._run, name=f"sk-remote-{self.spec.name}",
                                        daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=self.spec.timeout_s):
            self._closing.set()
            raise SkeletonKeyError(
                E.DEPENDENCY_MISSING,
                f"remote server {self.spec.name!r} did not handshake within "
                f"{self.spec.timeout_s:.0f}s",
                details={"server": self.spec.name, "timeout_s": self.spec.timeout_s,
                         "command": self.spec.command or self.spec.url})
        if self._error is not None:
            raise SkeletonKeyError(
                E.DEPENDENCY_MISSING,
                f"remote server {self.spec.name!r} failed: {self._error}",
                details={"server": self.spec.name, "command": self.spec.command or self.spec.url})
        if self._session is None:  # pragma: no cover - defensive
            raise SkeletonKeyError(E.DEPENDENCY_MISSING,
                                   f"remote server {self.spec.name!r} has no session",
                                   details={"server": self.spec.name})

    def close(self) -> None:
        self._closing.set()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        finally:
            self._loop = None
            loop.close()

    async def _main(self) -> None:
        try:
            async with self._session_ctx() as (read, write):
                from mcp.client.session import ClientSession

                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()
                    # asyncio.sleep, NOT a threading Event wait: this thread IS the
                    # loop, and run_coroutine_threadsafe calls must keep being served
                    while not self._closing.is_set():
                        await asyncio.sleep(0.25)
        except BaseException as exc:
            self._error = exc
            self._ready.set()

    def _session_ctx(self):
        if self.spec.url:
            # mcp renamed the client factory between minors (streamablehttp_client ->
            # streamable_http_client); accept both so a pinned older extra still connects
            import mcp.client.streamable_http as _http

            factory = (getattr(_http, "streamable_http_client", None)
                       or getattr(_http, "streamablehttp_client", None))
            if factory is None:
                raise SkeletonKeyError(E.DEPENDENCY_MISSING,
                                       "installed mcp package has no streamable-http client",
                                       details={"server": self.spec.name})
            return factory(self.spec.url)
        from mcp.client.stdio import StdioServerParameters, stdio_client

        return stdio_client(StdioServerParameters(command=self.spec.command,
                                                  args=self.spec.args))

    # ------------------------------------------------------------------- calls
    def _submit(self, coro: Any) -> Any:
        if self._loop is None or self._session is None:
            raise SkeletonKeyError(E.DEPENDENCY_MISSING,
                                   f"remote server {self.spec.name!r} is not connected",
                                   details={"server": self.spec.name})
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=self.spec.timeout_s)
        except TimeoutError as exc:
            raise SkeletonKeyError(
                E.TIMEOUT, f"remote server {self.spec.name!r} timed out",
                details={"server": self.spec.name, "timeout_s": self.spec.timeout_s},
            ) from exc
        except SkeletonKeyError:
            raise
        except Exception as exc:  # transport / protocol failure
            raise SkeletonKeyError(
                E.DEPENDENCY_MISSING,
                f"remote server {self.spec.name!r} call failed: {exc}",
                details={"server": self.spec.name},
            ) from exc

    def list_tools(self) -> list[Any]:
        result = self._submit(self._session.list_tools())
        return list(result.tools or [])

    def call(self, local_id: str, tool: str, args: dict[str, Any]) -> ToolResult:
        """Pass-through one call; returns a ToolResult with the remote outcome."""
        try:
            raw = self._submit(self._session.call_tool(tool, args))
        except SkeletonKeyError as exc:
            return ToolResult.failure(exc.err, tool=local_id)
        text = _first_text(raw)
        payload = _parse_json(text)
        if isinstance(payload, dict) and ("ok" in payload or "error" in payload):
            # skeletonkey-shaped remote envelope: passthrough, code verbatim
            if payload.get("ok"):
                return ToolResult.success(data=payload.get("data"),
                                          warnings=list(payload.get("warnings") or []),
                                          hints=list(payload.get("hints") or []),
                                          metrics=_remote_metrics(payload))
            err = payload.get("error") or {}
            return ToolResult.failure(
                ToolError(code=str(err.get("code") or "REMOTE"),
                          error_class=str(err.get("class") or E.REMOTE.cls.value),
                          message=str(err.get("message") or "remote tool failed"),
                          retryable=bool(err.get("retryable", False)),
                          hint=str(err.get("hint") or ""),
                          details=dict(err.get("details") or {})),
                tool=local_id)
        # foreign server: what it says is what we say - never INTERNAL, never guessed
        detail = text[:600] or (f"{len(raw.content or [])} content block(s), no text")
        return ToolResult.failure(
            ToolError(code=E.REMOTE.code, error_class=E.REMOTE.cls.value,
                      message=f"remote server {self.spec.name!r} reported an error",
                      retryable=E.REMOTE.retryable,
                      details={"server": self.spec.name, "remote_message": detail}),
            tool=local_id)


def _first_text(raw: Any) -> str:
    for block in raw.content or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return ""


def _parse_json(text: str) -> Any:
    if not text.strip().startswith("{"):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _remote_metrics(payload: dict[str, Any]) -> Any:
    from ..core.envelope import Metrics

    m = dict(payload.get("metrics") or {})
    return Metrics(provider=str(m.get("provider") or "remote"),
                   duration_ms=int(m.get("duration_ms") or 0))


def _sanitize_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "additionalProperties": False}
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k == "properties" and isinstance(v, dict):
            out["properties"] = {pk: _sanitize_schema(pv) for pk, pv in v.items()}
        elif k == "required" and isinstance(v, list):
            out["required"] = [str(x) for x in v]
        elif k in _SCHEMA_KEYS:
            out[k] = v
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    out.setdefault("additionalProperties", False)
    return out


def _risk(annotations: Any) -> str:
    """Inherit the remote's hints; an unannotated tool is `write` (ADR-0013:
    we cannot verify what a foreign server does with a call, so approval gates)."""
    if annotations is None:
        return "write"
    read_only = bool(getattr(annotations, "read_only_hint", False))
    return "read" if read_only else "write"


def _manifest(spec: RemoteSpec, server: RemoteServer, raw: Any) -> ToolManifest:
    tool = str(raw.name)
    local_id = f"remote.{spec.name}.{tool}"

    def handler(ctx: Any = None, engine: Any = None, dry_run: bool | None = None,
                **kwargs: Any) -> ToolResult:
        return server.call(local_id, tool, dict(kwargs))

    schema = _sanitize_schema(getattr(raw, "input_schema", None) or {})
    desc = (str(raw.description or "")).strip() or f"Remote tool {tool} on server {spec.name}."
    return ToolManifest(
        id=local_id, version="1", title=str(raw.title or tool),
        description=desc, group="remote", input_schema=schema,
        capability=f"remote.{spec.name}.{tool}", provider=f"remote:{spec.name}",
        priority=50, requirements=[], tags=["remote", spec.name, *tool.split(".")[:2]],
        risk=_risk(getattr(raw, "annotations", None)), idempotent=False,
        open_world=True, parallel_safe=False, reversible=False,
        typical_latency_ms=150, typical_output_bytes=4_000,
        timeout_s=spec.timeout_s + 5.0, stateful="host", advertised=True,
        tier="full", source=f"remote:{spec.name}", handler=handler,
    )


class RemoteConnector:
    """Enroll every configured remote server into a registry (build-time)."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.specs: list[RemoteSpec] = []
        self.errors: list[dict[str, Any]] = []
        for name, data in sorted(config.items()):
            try:
                self.specs.append(RemoteSpec.from_config(name, data))
            except ValueError as exc:
                self.errors.append({"name": name, "error": str(exc), "stage": "config"})

    def enroll(self, registry: Any) -> dict[str, Any]:
        report: dict[str, Any] = {"servers": [], "registered": [], "errors": list(self.errors)}
        for spec in self.specs:
            if not spec.enabled:
                report["errors"].append(
                    {"name": spec.name, "enabled": False,
                     "error": "server disabled in config; not enrolled", "stage": "config"})
                continue
            server = RemoteServer(spec)
            try:
                server.connect()
            except SkeletonKeyError as exc:
                row = {"name": spec.name, "error": str(exc), "stage": "connect",
                       **getattr(exc.err, "details", {})}
                report["errors"].append(row)
                registry.load_errors.append({"server": spec.name, "stage": "remote",
                                             "error": str(exc)[:400]})
                continue
            except Exception as exc:  # pragma: no cover - defensive
                row = {"name": spec.name, "error": f"{type(exc).__name__}: {exc}",
                       "stage": "connect"}
                report["errors"].append(row)
                registry.load_errors.append({"server": spec.name, "stage": "remote",
                                             "error": str(exc)[:400]})
                continue
            try:
                raw_tools = server.list_tools()
            except SkeletonKeyError as exc:
                row = {"name": spec.name, "error": str(exc), "stage": "list",
                       **getattr(exc.err, "details", {})}
                report["errors"].append(row)
                registry.load_errors.append({"server": spec.name, "stage": "remote-list",
                                             "error": str(exc)[:400]})
                continue
            # keep the server alive for the toolkit's lifetime
            registry._remote_servers = getattr(registry, "_remote_servers", {})
            registry._remote_servers[spec.name] = server
            added = 0
            for raw in raw_tools:
                try:
                    man = _manifest(spec, server, raw)
                    registry.register(man, replace=False)
                    added += 1
                except SkeletonKeyError as exc:
                    report["errors"].append(
                        {"name": spec.name, "tool": str(getattr(raw, "name", "?")),
                         "error": str(exc), "stage": "register"})
            report["registered"].extend(
                f"remote.{spec.name}.{t}" for t in
                sorted(str(x.name) for x in raw_tools))
            report["servers"].append({**spec.describe(), "tools": added,
                                      "raw": len(raw_tools)})
        return report
