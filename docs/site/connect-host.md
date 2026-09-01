# Connect a host

The MCP server speaks **stdio JSON-RPC** (the default) or streamable HTTP. You
can point any MCP client at it, or drive the engine directly with Python. This
page has the three setups; the wire contract beyond them is in
[TOOL-CONTRACT §7f](../TOOL-CONTRACT.md#7f-remote-mcp-servers-mcpremotes-adr-0013).

## 0. Install once

```bash
pip install "skeletonkey-toolforge[mcp]"   # or pipx install "skeletonkey-toolforge[mcp]"
```

Two console scripts are installed: `sk` (the CLI) and `skeletonkey-mcp` (the
server). Core has **zero mandatory dependencies** (ADR 0001): `mcp` is the
only extra the server needs.

## 1. Claude Desktop (or any stdio client)

`claude_desktop_config.json`:

```jsonc
{
  "mcpServers": {
    "skeletonkey": { "command": "skeletonkey-mcp", "args": ["--cwd", "/path/to/workspace"] }
  }
}
```

Notes:

- `--cwd` is the workspace: config (`skeletonkey.toml` or `.skeletonkey.toml`)
  is read from it, and its `roots` default to it. `--root` is repeatable to
  grant extra roots.
- `--read-only` withholds every mutating tool (`policy.read_only = true`); use
  it for hosts you do not fully trust yet.
- Approval: by default mutating calls come back as `approval_required` results
  (the host decides). `SKELETONKEY_AUTO_APPROVE=1` turns the in-process
  approver on — for your own autopilot, not a public server.
- `--log-level debug` streams per-call `notifications/message` lines (hosts
  that render them); server diagnostics go to **stderr**, stdout is the
  JSON-RPC channel.
- All paths an agent passes are checked by the path sandbox **on the server
  side** (`fsx/sandbox.py`): `..`, absolute-external, symlink escapes, Windows
  device names and `\\?\` prefixes are refused with `SANDBOX_VIOLATION` /
  `BAD_ARGS` — see [security-matrix.md](../security-matrix.md).

## 2. Generic / other stdio clients

Same shape, different key path; the wire is the standard MCP `tools/list`,
`tools/call`, `resources/*`. A client that cannot run console scripts can
launch Python directly:

```jsonc
{ "mcpServers": { "skeletonkey": { "command": "python", "args": ["-m", "skeletonkey.mcp", "--cwd", "."] } } }
```

`sk mcp --help` is the same entry point with the option list.

## 3. The in-repo autopilot (`sk live serve`)

`LiveREPL` keeps an MCP server alive while the agent works interactively (see
[LIVE-HMR.md](../LIVE-HMR.md) and `sk live demo`). A host behind an HTTP
proxy or a remote client can use streamable HTTP instead:

```bash
sk mcp --transport streamable-http --host 0.0.0.0 --port 8765 --cwd /path/to/workspace
```

The same warning as any network-exposed tool: the server is an execution
boundary, not a user boundary. `--read-only`, `policy` rules and `env_mode`
defaults are what contain it — do not expose it to untrusted networks with
auto-approve on.

## 4. Remote tool servers (no host client changes)

`[mcp.remotes.<name>]` in the config connects to another MCP server as a
source of tools (see the packed example `config/skeletonkey.example.toml`).
Those tools are advertised with `source = "remote:<name>"`; a host that needs
one does nothing special — the server proxies it. If the remote cannot be
handshaked, that shows up in `sk doctor` under `remote.errors` *and* as a
`DEPENDENCY_MISSING?` result rather than a crash.

## 5. No MCP host at all — the Python API

```python
from skeletonkey.toolkit import build

tk = build(cwd="/path/to/workspace")
tk.engine.call("fs.search", {"pattern": "TODO", "path": "src"})
tk.skills.context_block("fix the flaky windows path test")
tk.close()
```

`sk doctor` is the same configuration through a stable JSON blob — use it from
a support ticket or a healthcheck, and `sk doctor --fix` there is safe by
construction (it only creates the state dirs and refreshes the probe).
