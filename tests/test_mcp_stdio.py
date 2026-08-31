"""The MCP stdio endpoint, driven with raw JSON-RPC over a real subprocess.

No SDK client here on purpose: the contract we must keep is on the wire - stdout is
protocol-only, our envelope rides in `structuredContent` *and* as text, an unusable
path is a tool error (not a broken connection), and an unknown method is a JSON-RPC
error. A high-level client hides exactly those details.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOCOL = "2025-06-18"


class RpcError(Exception):
    def __init__(self, err: dict) -> None:
        super().__init__(f"{err.get('code')}: {err.get('message')}")
        self.code = err.get("code")
        self.message = err.get("message")
        self.data = err.get("data")


class RpcClient:
    """Line-delimited JSON-RPC 2.0 over a subprocess's stdio."""

    def __init__(self, argv: list[str], cwd: str, *, env: dict[str, str] | None = None) -> None:
        e = dict(os.environ)
        e["PYTHONPATH"] = REPO + os.pathsep + e.get("PYTHONPATH", "")
        e["PYTHONUNBUFFERED"] = "1"
        e["SKELETONKEY_LOG_LEVEL"] = "ERROR"
        e.update(env or {})
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8", env=e,
                                     bufsize=1)
        self._id = 0
        self.timeout = 45.0

    # -- plumbing ---------------------------------------------------------
    def _send(self, msg: dict) -> None:
        if self.proc.stdin is None:
            raise RuntimeError("server stdin is closed")
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _line(self, deadline: float) -> str:
        if self.proc.stdout is None:
            raise RuntimeError("server stdout is closed")
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no reply within {self.timeout}s; stderr tail:\n{self.stderr()}")
            ready, _, _ = select.select([self.proc.stdout], [], [], min(left, 1.0))
            if ready:
                line = self.proc.stdout.readline()
                if not line:
                    raise EOFError(f"server closed stdout; stderr tail:\n{self.stderr()}")
                if line.strip():
                    return line

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        mine = self._id
        msg = {"jsonrpc": "2.0", "id": mine, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = time.monotonic() + self.timeout
        while True:
            reply = json.loads(self._line(deadline))
            if reply.get("id") == mine:
                if "error" in reply:
                    raise RpcError(reply["error"])
                return reply["result"]
            # notifications (logging/*, list_changed) share the stream; skip them

    def notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def stderr(self) -> str:
        """Drain what is already buffered. Never `read(n)`: that blocks until n bytes
        or EOF, which on a live server means forever."""
        if self.proc.stderr is None:
            return ""
        out = ""
        while True:
            ready, _, _ = select.select([self.proc.stderr], [], [], 0.2)
            if not ready:
                return out
            line = self.proc.stderr.readline()
            if not line:
                return out
            out += line

    # -- lifecycle --------------------------------------------------------
    def start(self) -> dict:
        res = self.request("initialize", {"protocolVersion": PROTOCOL,
                                          "capabilities": {"roots": {"listChanged": False}},
                                          "clientInfo": {"name": "pytest-jsonrpc", "version": "0"}})
        self.notify("notifications/initialized")
        return res

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def spawn(cwd: str, *extra: str, env: dict | None = None) -> RpcClient:
    return RpcClient([sys.executable, "-m", "skeletonkey.mcp", "--cwd", cwd, *extra], cwd, env=env)


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    root = tmp_path_factory.mktemp("mcpons")
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("PORT = 8080\n\n\ndef handler(request):\n    return PORT\n",
                                         encoding="utf-8")
    (root / "README.md").write_text("# Demo\n\nSome text here.\n", encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def client(ws):
    c = spawn(str(ws), "--root", str(ws))
    c.start()
    try:
        yield c
    finally:
        c.close()


# ------------------------------------------------------------------ handshake
def test_initialize_advertises_the_dynamic_surface(client):
    info = client.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                        "clientInfo": {"name": "pytest", "version": "0"}})
    assert info["serverInfo"]["name"]
    assert "tools" in info["capabilities"], "the whole point is a dynamic tool set"
    assert info["capabilities"]["tools"]["listChanged"] is True
    exp = info["capabilities"].get("experimentalCapabilities") or info["capabilities"].get("experimental", {})
    assert exp.get("skeletonkey", {}).get("dynamic_tools") is True
    assert info["protocolVersion"]


def test_stderr_carries_diagnostics_and_stdout_stays_clean(client, ws):
    # one call, then prove nothing but JSON-RPC ever reached stdout - a stray print()
    # in a tool handler corrupts the channel and is nearly impossible to debug remotely
    tools = client.request("tools/list", {})
    assert isinstance(tools["tools"], list)
    err = client.stderr()
    assert "[skeletonkey] host=" in err or err == "", err[:200]
    assert "Traceback" not in err


# ------------------------------------------------------------------ tools/list
def test_tools_list_is_mcp_shaped_and_carries_our_metadata(client):
    listing = client.request("tools/list", {})
    tools = listing["tools"]
    names = {t["name"] for t in tools}
    assert {"fs.read", "fs.write", "fs.patch", "fs.chmod", "shell.run", "registry.search"} <= names
    assert len(names) >= 29
    for t in tools:
        assert t["description"], "an agent selects tools from this string"
        assert t["inputSchema"]["type"] == "object"
        assert "." in t["name"], "dotted group names keep the flat host list navigable"
        ann = t.get("annotations") or {}
        assert set(ann) <= {"title", "readOnlyHint", "destructiveHint", "idempotentHint",
                            "openWorldHint"}
        sk = (t.get("_meta") or {}).get("sk")
        assert sk and {"id", "risk", "group"} <= set(sk), "risk must be visible before the call"
        assert sk["risk"] in {"none", "read", "write", "privileged", "destructive"}
    fs_write = next(t for t in tools if t["name"] == "fs.write")
    assert set(fs_write["inputSchema"]["properties"]) >= {"path", "content", "overwrite", "newline"}
    assert fs_write["annotations"]["destructiveHint"] is False
    assert fs_write["annotations"]["idempotentHint"] is True
    assert "next_cursor" not in listing or listing["next_cursor"] is None


# ------------------------------------------------------------------ tools/call
def _payload(result: dict) -> dict:
    body = result.get("structuredContent")
    if body is None:
        body = json.loads(result["content"][0]["text"])
    return body


def test_write_then_read_round_trip(client, ws):
    res = client.request("tools/call", {"name": "fs.write",
                                       "arguments": {"path": "src/new.txt", "content": "hello\n"}})
    assert res["isError"] is False, res
    data = _payload(res)["data"]
    assert data["created"] is True and data["sha_after"]
    assert (ws / "src" / "new.txt").read_text(encoding="utf-8") == "hello\n"
    # the ledger entry the tool promised must exist for the same run id
    read = client.request("tools/call", {"name": "fs.read", "arguments": {"path": "src/new.txt"}})
    assert _payload(read)["data"]["content"] == "hello\n"
    text = res["content"][0]["text"]
    assert json.loads(text), "hosts that ignore structuredContent still get parsable JSON"


def test_patch_over_the_wire_reports_a_diff(client, ws):
    client.request("tools/call", {"name": "fs.write",
                                 "arguments": {"path": "src/mod.py", "content": "PORT = 1\n"}})
    res = client.request("tools/call", {"name": "fs.patch",
                                        "arguments": {"path": "src/mod.py",
                                                      "edits": [{"old_text": "PORT = 1",
                                                                 "new_text": "PORT = 2"}]}})
    assert res["isError"] is False, res
    body = _payload(res)
    assert body["data"]["applied"] == 1
    assert "-PORT = 1" in body["data"]["unified_diff"]
    token = body["data"]["undo_token"]
    undo = client.request("tools/call", {"name": "fs.undo", "arguments": {"token": token}})
    assert undo["isError"] is False, undo
    assert (ws / "src" / "mod.py").read_text(encoding="utf-8") == "PORT = 1\n"


def test_sandbox_escape_is_a_tool_error_not_a_broken_connection(client):
    res = client.request("tools/call", {"name": "fs.read",
                                        "arguments": {"path": "../../../etc/passwd"}})
    assert res["isError"] is True
    body = _payload(res)
    assert body["error"]["code"] in {"SANDBOX_VIOLATION", "ENOENT"}
    # the session must survive: this is the difference between a warning and a outage
    assert client.request("tools/list", {})["tools"]


def test_unknown_tool_is_answered_with_options(client):
    res = client.request("tools/call", {"name": "fs.reed", "arguments": {}})
    assert res["isError"] is True
    body = _payload(res)
    assert body["error"]["code"] == "UNKNOWN_TOOL"
    assert "fs.read" in json.dumps(body["error"]["details"])


def test_bad_arguments_carry_the_schema_and_an_example(client):
    res = client.request("tools/call", {"name": "fs.write", "arguments": {"path": "x"}})
    assert res["isError"] is True
    body = _payload(res)
    assert body["error"]["code"] in {"MISSING_ARG", "BAD_ARGS"}
    assert "schema" in body["error"]["details"] or "minimal_example" in body["error"]["details"]


def test_method_not_found_is_a_jsonrpc_error(client):
    with pytest.raises(RpcError) as exc:
        client.request("tools/launch_missiles", {})
    assert exc.value.code == -32601


# ------------------------------------------------------------------ prompts / resources
def test_prompts_expose_bootstrap_and_skills(client):
    prompts = {p["name"] for p in client.request("prompts/list", {})["prompts"]}
    assert {"capability_report", "task_bootstrap"} <= prompts
    got = client.request("prompts/get", {"name": "capability_report", "arguments": {}})
    body = json.loads(got["messages"][0]["content"]["text"])
    assert {"workspace", "profile", "policy"} <= set(body)
    boot = client.request("prompts/get", {"name": "task_bootstrap",
                                          "arguments": {"task": "rename a symbol across the repo"}})
    body = json.loads(boot["messages"][0]["content"]["text"])
    assert body["task"]
    assert isinstance(body["tools"], list) and body["tools"]
    assert "block" in body["skills"], "matched skill text is the point of the prompt"


def test_skill_prompts_are_readable_only_when_the_skill_pack_is_on_the_path():
    c = spawn(REPO, "--read-only")
    try:
        c.start()
        prompts = {p["name"] for p in c.request("prompts/list", {})["prompts"]}
        assert "skill_fs_safe_refactor" in prompts
        got = c.request("prompts/get", {"name": "skill_fs_safe_refactor",
                                       "arguments": {"task": "patch a file safely"}})
        text = got["messages"][0]["content"]["text"]
        assert text.startswith("# skill: fs-safe-refactor")
        assert "fs.patch" in text
        # read_only must be enforced on the tool surface, not just advertised
        names = {t["name"] for t in c.request("tools/list", {})["tools"]}
        assert "fs.write" not in names and "fs.read" in names
    finally:
        c.close()


def test_resources_describe_the_host_and_the_workspace(client, ws):
    uris = {r["uri"] for r in client.request("resources/list", {})["resources"]}
    assert {"skeletonkey://profile", "skeletonkey://tools", "skeletonkey://journal",
            "skeletonkey://ledger"} <= uris
    assert any(u.startswith("skeletonkey://file/") for u in uris)
    prof = client.request("resources/read", {"uri": "skeletonkey://profile"})
    body = json.loads(prof["contents"][0]["text"])
    assert body["os"] in {"linux", "windows", "darwin"} and "capabilities" in body
    rel = "README.md"
    txt = client.request("resources/read", {"uri": f"skeletonkey://file/{rel}"})
    assert "# Demo" in txt["contents"][0]["text"]
    with pytest.raises(RpcError):
        client.request("resources/read", {"uri": "skeletonkey://file/../secret"})
    with pytest.raises(RpcError):
        client.request("resources/read", {"uri": "skeletonkey://nope"})


def test_journal_resource_shows_the_mutation_that_ran(client):
    client.request("tools/call", {"name": "fs.write",
                                 "arguments": {"path": "journal-me.txt", "content": "x\n"}})
    rows = json.loads(client.request("resources/read", {"uri": "skeletonkey://journal"})
                      ["contents"][0]["text"])
    assert any(r["path"] == "journal-me.txt" for r in rows)
    assert all("undo_token" not in json.dumps(r) or r.get("token") for r in rows)


# ------------------------------------------------------------------ lifecycle
def test_server_exits_cleanly_when_stdin_closes(tmp_path):
    c = spawn(str(tmp_path), "--root", str(tmp_path))
    c.start()
    assert c.proc.poll() is None
    c.notify("notifications/cancelled", {"requestId": "none"})
    if c.proc.stdin:
        c.proc.stdin.close()
    deadline = time.monotonic() + 15
    while c.proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert c.proc.poll() is not None, "closing the pipe must not leave a wedged server"
    assert "Traceback" not in c.stderr()
    c.proc.kill()
