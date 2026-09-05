"""Server entry point: `python -m skeletonkey.mcp` (or the `skeletonkey-mcp` script).

stdio: stdout is the JSON-RPC channel, so every diagnostic goes to stderr or the log
file. streamable-http: the same server object is served as an ASGI app - any client
with nothing but a URL reaches the same toolkit, manifests and envelopes. `run` is
written so the same server object can be driven by tests in process.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from ..core.config import Config
from ..toolkit import build
from .adapter import build_server, make_approval_callback


def _config_from_args(args: argparse.Namespace) -> Config:
    overrides: dict[str, object] = {}
    if args.root:
        overrides["roots"] = list(args.root)
    if args.read_only:
        overrides.setdefault("policy", {})["read_only"] = True  # type: ignore[index]
    if args.log_level:
        overrides["log_level"] = args.log_level
    cfg = Config.load(cwd=args.cwd, overrides=overrides)
    if args.transport:
        cfg.mcp.transport = args.transport
    if args.host:
        cfg.mcp.host = args.host
    if args.port is not None:
        cfg.mcp.port = args.port
    return cfg


async def amain(args: argparse.Namespace) -> int:
    """stdio server: `asyncio.run` owns the loop, the SDK owns the pipes."""
    cfg = _config_from_args(args)
    auto = os.environ.get("SKELETONKEY_AUTO_APPROVE", "").lower() in ("1", "true", "yes")
    toolkit = build(config=cfg, approver=make_approval_callback(auto=auto), force_probe=args.probe)
    server, init_options, _bridge = build_server(toolkit)
    log = sys.stderr
    print(f"[skeletonkey] host={cfg.workspace} roots={len(cfg.roots)} "
          f"tools={len(toolkit.engine.advertise().tools)} dialects={toolkit.profile.available_dialects()}",
          file=log, flush=True)
    try:
        import mcp.server.stdio as stdio

        async with stdio.stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)
    except (KeyboardInterrupt, BrokenPipeError):
        print("[skeletonkey] client disconnected", file=log, flush=True)
    finally:
        toolkit.close()
    return 0


def serve_http(args: argparse.Namespace) -> int:
    """streamable-http server: uvicorn owns the event loop, so this path deliberately
    runs OUTSIDE `asyncio.run` - nesting them raises
    "Runner.run() cannot be called from a running event loop".

    Network exposure: any MCP client that speaks streamable-http reaches the same
    toolkit the stdio hosts get - same manifests, same envelopes, same policy. `mcp`
    already depends on uvicorn+starlette, so this adds no new dependency; the [mcp]
    extra is the only gate.
    """
    cfg = _config_from_args(args)
    auto = os.environ.get("SKELETONKEY_AUTO_APPROVE", "").lower() in ("1", "true", "yes")
    toolkit = build(config=cfg, approver=make_approval_callback(auto=auto), force_probe=args.probe)
    server, _init_options, _bridge = build_server(toolkit)
    log = sys.stderr
    print(f"[skeletonkey] host={cfg.workspace} roots={len(cfg.roots)} "
          f"tools={len(toolkit.engine.advertise().tools)} dialects={toolkit.profile.available_dialects()}",
          file=log, flush=True)
    try:
        app = server.streamable_http_app()
        import uvicorn

        print(f"[skeletonkey] listening on http://{cfg.mcp.host}:{cfg.mcp.port}/mcp "
              f"(streamable-http)", file=log, flush=True)
        uvicorn.run(app, host=cfg.mcp.host, port=cfg.mcp.port, log_level="warning")
    except KeyboardInterrupt:
        print("[skeletonkey] shutting down", file=log, flush=True)
    finally:
        toolkit.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skeletonkey-mcp", description="SkeletonKey MCP server")
    ap.add_argument("--root", action="append", help="filesystem root (repeatable); defaults to --cwd")
    ap.add_argument("--cwd", default=None, help="workspace directory")
    ap.add_argument("--transport", default=None, choices=["stdio", "streamable-http"])
    ap.add_argument("--host", default=None,
                    help="with --transport streamable-http: bind host (default mcp.host, 127.0.0.1)")
    ap.add_argument("--port", type=int, default=None,
                    help="with --transport streamable-http: bind port (default mcp.port, 8765)")
    ap.add_argument("--read-only", action="store_true", help="withhold every mutating tool")
    ap.add_argument("--probe", action="store_true", help="force a fresh capability probe")
    ap.add_argument("--log-level", default=None, choices=["debug", "info", "warning", "error"],
                    help="server log verbosity; at 'debug' every tool call also streams a "
                         "notifications/message log line to the wire for hosts that render it")
    args = ap.parse_args(argv)
    try:
        if args.transport == "streamable-http":
            return serve_http(args)
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
