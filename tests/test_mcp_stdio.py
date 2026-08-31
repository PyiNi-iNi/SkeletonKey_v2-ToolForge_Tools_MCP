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

    def request(self, method: str, params: dict | None = None, *,
                collect: list | None = None) -> dict:
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
            # notifications (logging/*, list_changed) share the stream; skip them, but a
            # caller that passed `collect` is asserting on the notification, so keep it
            if collect is not None:
                collect.append(reply)

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


def test_policy_grant_over_the_wire(client, ws):
    """The grant is advertised, answers with a receipt for a safe target, and
    denies-with-reason for a destructive target on a host with no UI (the
    default stdio posture)."""
    listing = client.request("tools/list", {})
    grant = next(t for t in listing["tools"] if t["name"] == "policy.grant")
    assert set(grant["inputSchema"]["properties"]) == {"tool", "scope"}
    assert grant["inputSchema"]["required"] == ["tool"]

    safe = client.request("tools/call", {"name": "policy.grant",
                                         "arguments": {"tool": "fs.write", "scope": "task"}})
    assert safe["isError"] is False, safe
    body = _payload(safe)
    assert body["data"]["granted"] is True
    assert body["data"]["receipt"]["granted_by"] == "no approval required for this target"
    assert body["data"]["receipt"]["task_id"]

    (ws / "wired.txt").write_text("x\n", encoding="utf-8")
    guarded = client.request("tools/call", {"name": "policy.grant",
                                            "arguments": {"tool": "fs.delete", "scope": "task"}})
    assert guarded["isError"] is True, guarded
    gbody = _payload(guarded)
    assert gbody["error"]["code"] == "APPROVAL_REQUIRED", \
        "a destructive self-grant on a UI-less stdio host is refused, not a hole"
    assert (ws / "wired.txt").exists()


def test_policy_grant_unblocks_the_same_connection_with_an_approver(ws):
    """With SKELETONKEY_AUTO_APPROVE=1 (the explicit autopilot dial) the grant
    records a receipt and the destructive call it covers then passes on the
    same connection - one ctx, one ledger."""
    root = ws / "grantws"
    root.mkdir(exist_ok=True)
    (root / "victim.txt").write_text("x\n", encoding="utf-8")
    c = spawn(str(root), "--root", str(root), env={"SKELETONKEY_AUTO_APPROVE": "1"})
    try:
        c.start()
        g = c.request("tools/call", {"name": "policy.grant",
                                     "arguments": {"tool": "fs.delete", "scope": "task"}})
        assert g["isError"] is False, g
        body = _payload(g)
        assert body["data"]["granted"] is True
        assert body["data"]["receipt"]["granted_by"] == "approver callback"
        d = c.request("tools/call", {"name": "fs.delete", "arguments": {"path": "victim.txt"}})
        assert d["isError"] is False, d
        assert not (root / "victim.txt").exists(), "the grant must actually cover the call"
    finally:
        c.close()


def test_fs_redo_and_expect_sha_over_the_wire(client, ws):
    # advertised with its schema, and the full create -> undo -> redo round trip works
    listing = client.request("tools/list", {})
    redo = next(t for t in listing["tools"] if t["name"] == "fs.redo")
    assert set(redo["inputSchema"]["properties"]) == {"path", "dry_run"}
    res = client.request("tools/call", {"name": "fs.write",
                                        "arguments": {"path": "redo-wire.txt", "content": "wire\n"}})
    assert res["isError"] is False, res
    token = _payload(res)["data"]["undo_token"]
    u = client.request("tools/call", {"name": "fs.undo", "arguments": {"token": token}})
    assert u["isError"] is False, u
    assert not (ws / "redo-wire.txt").exists(), "undoing a create removes the file"
    r = client.request("tools/call", {"name": "fs.redo", "arguments": {}})
    assert r["isError"] is False, r
    body = _payload(r)
    assert body["data"]["redone"] is True and body["data"]["action"] == "create"
    assert body["data"]["undo_token"], "the redo is journaled itself - it comes with a token"
    assert (ws / "redo-wire.txt").read_text(encoding="utf-8") == "wire\n"
    # and the fresh token undoes it again, over the same connection
    back = client.request("tools/call", {"name": "fs.undo",
                                         "arguments": {"token": body["data"]["undo_token"]}})
    assert back["isError"] is False, back
    assert not (ws / "redo-wire.txt").exists()
    # expect_sha is now part of fs.undo's advertised schema, and a stale sha is a
    # tool error, not a silent overwrite
    undo_spec = next(t for t in client.request("tools/list", {})["tools"] if t["name"] == "fs.undo")
    assert "expect_sha" in undo_spec["inputSchema"]["properties"]
    res2 = client.request("tools/call", {"name": "fs.write",
                                         "arguments": {"path": "redo-wire.txt", "content": "v2\n"}})
    token2 = _payload(res2)["data"]["undo_token"]
    c = client.request("tools/call", {"name": "fs.undo",
                                      "arguments": {"token": token2, "expect_sha": "deadbeefdeadbeef"}})
    assert c["isError"] is True, "a stale sha must refuse"
    assert "CONFLICT" in json.dumps(_payload(c))
    assert (ws / "redo-wire.txt").read_text(encoding="utf-8") == "v2\n", "file untouched"


def test_os_trash_tier_over_the_wire(tmp_path):
    """fs.trash = "os-trash" from the server's own skeletonkey.toml: on a host
    without a trash API the delete refuses with UNSUPPORTED_PLATFORM and deletes
    nothing; with `gio` on PATH the file lands in the bin and the journal keeps
    a second copy that survives into a new server process."""
    root = tmp_path / "trashws"
    root.mkdir()
    (root / "victim.txt").write_text("x\n", encoding="utf-8")
    (root / "skeletonkey.toml").write_text('[fs]\ntrash = "os-trash"\n', encoding="utf-8")
    empty = tmp_path / "emptybin"
    empty.mkdir()
    c = spawn(str(root), "--root", str(root),
              env={"PATH": str(empty), "SKELETONKEY_AUTO_APPROVE": "1"})
    c.start()
    try:
        r = c.request("tools/call", {"name": "fs.delete", "arguments": {"path": "victim.txt"}})
        assert r["isError"] is True, r
        assert "UNSUPPORTED_PLATFORM" in json.dumps(_payload(r))
        assert (root / "victim.txt").exists(), "a no-trash host deletes nothing"
    finally:
        c.close()

    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    (bin_dir / "gio").write_text(
        "#!/bin/sh\n"
        'mkdir -p "$FAKE_TRASH_DIR"\n'
        'mv "$2" "$FAKE_TRASH_DIR/$(basename "$2")" || exit 1\n',
        encoding="utf-8", newline="\n")
    os.chmod(bin_dir / "gio", 0o755)
    fake_bin = tmp_path / "trashbin"
    env = {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "FAKE_TRASH_DIR": str(fake_bin), "SKELETONKEY_AUTO_APPROVE": "1"}
    c = spawn(str(root), "--root", str(root), env=env)
    c.start()
    try:
        r = c.request("tools/call", {"name": "fs.delete", "arguments": {"path": "victim.txt"}})
        assert r["isError"] is False, r
        body = _payload(r)
        assert body["data"]["deleted"] is True and body["data"]["trash"] == "recycle bin"
        assert not (root / "victim.txt").exists()
        assert (fake_bin / "victim.txt").read_text(encoding="utf-8") == "x\n"
        # the journal (the second copy) is on disk and visible to a *new* server
        assert os.path.isdir(root / ".sk" / "journal")
    finally:
        c.close()
    c = spawn(str(root), "--root", str(root))
    c.start()
    try:
        rows = json.loads(c.request("resources/read", {"uri": "skeletonkey://journal"})
                          ["contents"][0]["text"])
        assert any(r["path"] == "victim.txt" and r.get("token") for r in rows), \
            "the journal entry for the os-trash delete survives a restart"
    finally:
        c.close()


def test_fs_read_carries_via_provenance_over_the_wire(client, ws):
    res = client.request("tools/call", {"name": "fs.read",
                                        "arguments": {"path": "src/mod.py"}})
    assert res["isError"] is False, res
    via = _payload(res)["data"]["via"]
    assert via["root"] == os.path.realpath(str(ws)), \
        "the host sees which root the path resolved against"


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



# ------------------------------------------------- dynamic tools, on the wire (P2 exit gate)
_DEMO_SKILL_MD = '''---
name: demo
description: Count the words in a file, from a skill pack installed at runtime.
when_to_use: A caller needs a word count of one file.
version: "1"
tags: [demo, text]
---

# Demo

Call `skill.demo.wordcount` with a path.
'''

_DEMO_TOOL_TOML = '''[[tool]]
name = "wordcount"
title = "Count words"
description = "Count the words in one file and return the count as JSON."
capability = "demo.wordcount"
risk = "read"
idempotent = true
parallel_safe = true
expects = "json"
args_via = "flags"
handler_script = "scripts/wordcount.sh"

input_schema = """
{
  "type": "object",
  "properties": {"path": {"type": "string", "description": "File to count words in."}},
  "required": ["path"],
  "additionalProperties": false
}
"""
'''

_DEMO_SCRIPT = '''#!/usr/bin/env bash
set -euo pipefail
path=""
while [ $# -gt 0 ]; do
  case "$1" in
    --path) path="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf \'{"path":"%s","words":%s}\\n\' "$path" "$(wc -w < "$path" | tr -d \' \')"
'''


def _make_installable_pack(tmp_path, *, allow_install=True):
    """A workspace with one uninstalled pack and, optionally, the gate unlocked."""
    (tmp_path / "skills").mkdir()
    src = tmp_path / "incoming" / "demo"
    (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text(_DEMO_SKILL_MD, encoding="utf-8")
    (src / "tool.toml").write_text(_DEMO_TOOL_TOML, encoding="utf-8")
    (src / "scripts" / "wordcount.sh").write_text(_DEMO_SCRIPT, encoding="utf-8", newline="\n")
    (tmp_path / "notes.txt").write_text("one two three four\n", encoding="utf-8")
    gate = "allow_install = true\n" if allow_install else ""
    (tmp_path / "skeletonkey.toml").write_text(
        "[skills]\n" + gate + 'dirs = ["skills"]\n', encoding="utf-8")
    return src


def test_skills_install_re_advertises_on_the_same_connection(tmp_path):
    """Criterion 1, over the wire: install -> `tools/list_changed` -> the tool is listed.

    A client that has to reconnect to see a new tool does not have a dynamic tool set, so
    this asserts the notification itself, on the connection that made the call.
    """
    src = _make_installable_pack(tmp_path)
    c = spawn(str(tmp_path), "--root", str(tmp_path), env={"SKELETONKEY_AUTO_APPROVE": "1"})
    c.start()
    try:
        # prime the digest: with no listing yet there is nothing for a change to be relative to
        before = {t["name"] for t in c.request("tools/list", {})["tools"]}
        assert "skills.install" in before and "skill.demo.wordcount" not in before

        notes: list[dict] = []
        res = c.request("tools/call", {"name": "skills.install",
                                      "arguments": {"dir": str(src)}}, collect=notes)
        assert not res.get("isError"), _payload(res)
        data = _payload(res)["data"]
        assert data["skill"] == "demo" and data["installed"] is True
        assert "skill.demo.wordcount" in json.dumps(data["tools"])
        assert not data["errors"], data["errors"]
        assert "notifications/tools/list_changed" in [n.get("method") for n in notes], notes

        listed = c.request("tools/list", {})["tools"]
        names = {t["name"] for t in listed}
        assert "skill.demo.wordcount" in names, "the advertisement must have moved"
        man = next(t for t in listed if t["name"] == "skill.demo.wordcount")
        assert man["inputSchema"]["required"] == ["path"]
        assert man["_meta"]["sk"]["risk"] == "read"
        assert man["annotations"]["readOnlyHint"] is True

        # and the synthesized tool answers over the same connection
        called = c.request("tools/call", {"name": "skill.demo.wordcount",
                                         "arguments": {"path": "notes.txt"}})
        body = _payload(called)
        assert body["ok"] is True, body
        assert body["data"]["result"] == {"path": "notes.txt", "words": 4}
        assert body["data"]["argv"] == ["--path", "notes.txt"]

        gone = c.request("tools/call", {"name": "skills.uninstall",
                                       "arguments": {"name": "demo"}})
        assert not gone.get("isError"), _payload(gone)
        after = {t["name"] for t in c.request("tools/list", {})["tools"]}
        assert "skill.demo.wordcount" not in after, "removal must re-advertise too"
        assert (tmp_path / "skills" / "demo").exists() is False, "the files go with it"
        assert "Traceback" not in c.stderr()
    finally:
        c.close()


def test_install_gate_closed_is_visible_over_the_wire(tmp_path):
    """`skills.allow_install = false` is an observable answer, not a silent absence.

    The tool stays registered but unadvertised, so a client sees it missing from
    `tools/list`, and if it calls the name anyway it gets the refusal with the setting
    named - and, above all, nothing written. `dry_run` still answers, because reviewing a
    plan is not the same risk as running it.
    """
    src = _make_installable_pack(tmp_path, allow_install=False)
    c = spawn(str(tmp_path), "--root", str(tmp_path), env={"SKELETONKEY_AUTO_APPROVE": "1"})
    c.start()
    try:
        names = {t["name"] for t in c.request("tools/list", {})["tools"]}
        assert "skills.install" not in names, "the gated half of the pair stays hidden"
        assert "skills.uninstall" in names, "removing capability needs no gate"

        plan = _payload(c.request("tools/call", {"name": "skills.install",
                                                 "arguments": {"dir": str(src),
                                                               "dry_run": True}}))
        assert plan["ok"] is True and plan["data"]["dry_run"] is True
        assert "skill.demo.wordcount" in json.dumps(plan["data"]["tools"])

        res = c.request("tools/call", {"name": "skills.install", "arguments": {"dir": str(src)}})
        body = _payload(res)
        assert res.get("isError") and body["ok"] is False
        err = body["error"]
        assert err["code"] == "DENY_RULE", err
        assert err["details"]["setting"] == "skills.allow_install"
        assert not (tmp_path / "skills" / "demo").exists(), "a refusal must not touch the tree"
    finally:
        c.close()
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


# ------------------------------------------------- P4 budget governor (wire)

def test_wire_metrics_carry_budget_position(tmp_path):
    (tmp_path / "skeletonkey.toml").write_text(
        "[budget]\ntask_max_tokens_out = 60\n", encoding="utf-8")
    (tmp_path / "big.txt").write_text("x" * 4000, encoding="utf-8")
    c = spawn(str(tmp_path), "--root", str(tmp_path))
    c.start()
    try:
        res = c.request("tools/call", {"name": "fs.read", "arguments": {"path": "big.txt"}})
        assert not res.get("isError"), _payload(res)
        env = _payload(res)
        b = env["metrics"]["budget"]
        # spent is the charged (pre-block) estimate; est_tokens also covers the
        # budget block itself, so it is larger by exactly that block's cost
        assert 0 < env["metrics"]["est_tokens"] - b["spent"]["tokens_out"] <= 100
        assert b["exhausted"] is True, \
            "the read that crossed the cap flags it in the same envelope"
        assert b["remaining"]["tokens_out"] == 0

        res2 = c.request("tools/call", {"name": "fs.read", "arguments": {"path": "big.txt"}})
        env2 = _payload(res2)
        assert env2["ok"] is False and env2["error"]["code"] == "BUDGET_EXCEEDED"
        assert env2["next_actions"][0]["action"] == "summarize_and_stop"
        assert env2["metrics"]["budget"]["exhausted"] is True
    finally:
        c.close()
