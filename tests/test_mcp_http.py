"""The streamable-http endpoint, driven by a real MCP client over real HTTP.

The stdio wire test (test_mcp_stdio.py) keeps the stdio contract honest with raw
JSON-RPC; this one keeps the *network* exposure honest with the SDK's own client:
a subprocess server bound to an ephemeral port, `initialize` -> `tools/list` ->
a read-only `tools/call`, asserting our envelope arrives in `structuredContent` over
the wire. This is the transport a non-Python project can point at with nothing but a
URL - the "exposed and callable from anywhere" story, executed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOCOL = "2025-06-18"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _up(url: str, deadline: float) -> bool:
    # the streamable-http endpoint answers POST with 4xx until initialized; any HTTP
    # answer at all means uvicorn is listening - that is all this poll asserts
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url, data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True                                  # listening; protocol errors are fine
        except (OSError, urllib.error.URLError):
            time.sleep(0.15)
    return False


@pytest.fixture()
async def http_server():
    port = _free_port()
    ws = tempfile.mkdtemp(prefix="sk-http-test-")
    with open(os.path.join(ws, "note.txt"), "w", encoding="utf-8") as fh:
        fh.write("http probe\n")
    e = dict(os.environ)
    e["PYTHONPATH"] = REPO + os.pathsep + e.get("PYTHONPATH", "")
    e["SKELETONKEY_LOG_LEVEL"] = "ERROR"
    proc = subprocess.Popen(
        [sys.executable, "-m", "skeletonkey.mcp", "--transport", "streamable-http",
         "--port", str(port), "--cwd", ws],
        cwd=ws, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=e)
    url = f"http://127.0.0.1:{port}/mcp"
    assert _up(url, time.monotonic() + 30), "server did not start listening"
    try:
        yield url, ws
    finally:
        proc.kill()
        proc.wait(timeout=10)


async def test_streamable_http_serves_the_same_surface(http_server):
    url, _ws = http_server
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read, write), \
            ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.server_info.name == "SkeletonKey ToolForge"

        listed = await session.list_tools()
        names = {t.name for t in listed.tools}
        assert {"fs.stat", "fs.patch", "shell.run", "registry.route"} <= names
        assert "skills.install" not in names               # the gate holds over http too

        res = await session.call_tool("fs.stat", {"path": "note.txt"})
        assert not res.is_error
        sc = res.structured_content or {}
        env = sc.get("envelope") or sc
        assert env.get("ok") is True
        assert env["data"]["size"] == len("http probe\n")
        # the text channel still carries the envelope for hosts that only read text
        text = "".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        assert json.loads(text)["ok"] is True

        absent = await session.call_tool("fs.stat", {"path": "outside/root.txt"})
        sc2 = (absent.structured_content or {}).get("envelope") or absent.structured_content or {}
        assert sc2.get("ok") is True and sc2["data"]["exists"] is False   # stat answers; it does not error

        unknown = await session.call_tool("no.such.tool", {})
        assert unknown.is_error                                          # a tool error, not a crash
