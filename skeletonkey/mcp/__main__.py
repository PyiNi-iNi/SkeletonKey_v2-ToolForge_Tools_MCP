"""stdio entry point: `python -m skeletonkey.mcp` (or the `skeletonkey-mcp` script).

stdout is the JSON-RPC channel, so every diagnostic goes to stderr or the log
file. `run` is written so the same server object can be driven by tests in
process.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from ..core.config import Config
from ..toolkit import build
from .adapter import build_server, make_approval_callback


async def amain(args: argparse.Namespace) -> int:
    overrides: dict[str, object] = {}
    if args.root:
        overrides["roots"] = list(args.root)
    if args.read_only:
        overrides.setdefault("policy", {})["read_only"] = True  # type: ignore[index]
    cfg = Config.load(cwd=args.cwd, overrides=overrides)
    if args.transport:
        cfg.mcp.transport = args.transport
    auto = os.environ.get("SKELETONKEY_AUTO_APPROVE", "").lower() in ("1", "true", "yes")
    toolkit = build(config=cfg, approver=make_approval_callback(auto=auto), force_probe=args.probe)
    server, init_options, _bridge = build_server(toolkit)
    log = sys.stderr
    print(f"[skeletonkey] host={cfg.workspace} roots={len(cfg.roots)} "
          f"tools={len(toolkit.engine.advertise().tools)} dialects={toolkit.profile.available_dialects()}",
          file=log, flush=True)
    try:
        if cfg.mcp.transport == "stdio":
            import mcp.server.stdio as stdio

            async with stdio.stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, init_options)
        else:
            app = server.streamable_http_app()  # pragma: no cover - Phase 3 polish
            import uvicorn

            uvicorn.run(app, host=cfg.mcp.host, port=cfg.mcp.port)
    except (KeyboardInterrupt, BrokenPipeError):
        print("[skeletonkey] client disconnected", file=log, flush=True)
    finally:
        toolkit.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skeletonkey-mcp", description="SkeletonKey MCP server")
    ap.add_argument("--root", action="append", help="filesystem root (repeatable); defaults to --cwd")
    ap.add_argument("--cwd", default=None, help="workspace directory")
    ap.add_argument("--transport", default=None, choices=["stdio", "streamable-http"])
    ap.add_argument("--read-only", action="store_true", help="withhold every mutating tool")
    ap.add_argument("--probe", action="store_true", help="force a fresh capability probe")
    args = ap.parse_args(argv)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
