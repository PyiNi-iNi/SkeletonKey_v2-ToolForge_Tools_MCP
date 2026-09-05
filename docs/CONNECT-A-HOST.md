# Connect a host

SkeletonKey is a **stdio MCP server** (and, since this session, a **streamable-http**
one). Any MCP host - Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, your own
autopilot - reaches the same toolkit through the same manifests, the same envelopes and
the same policy. This page is the shortest path from `pip install` to a host that lists
the tools, and the debugging path when it does not.

The one-command version:

```console
$ pip install "skeletonkey-toolforge[mcp]"   # or: pipx install "skeletonkey-toolforge[mcp]"
$ sk wire                                    # writes the entry into every detected host
$ sk doctor                                  # proves it: live server, live call, wired hosts
```

Restart the host after wiring; hosts read their config at startup.

## What `sk wire` writes

A `skeletonkey` entry in the host's server map, with the interpreter `sk` itself ran
under (so the install you wired is the install the host launches):

```jsonc
// key: "mcpServers" (VS Code uses "servers")
"skeletonkey": {
  "command": "/abs/path/to/python",
  "args": ["-m", "skeletonkey.mcp"]
}
```

Useful variants:

```console
$ sk wire --read-only                       # the host sees only the read-only surface
$ sk wire --root /srv/proj --root /tmp      # pin the sandbox roots explicitly
$ sk wire cursor vscode                     # specific hosts (ids or friendly names)
$ sk wire --check                           # what would change, writes nothing
$ sk wire --remove cursor                   # removes exactly our entry; a hand-written
                                            # entry with the same name is refused
```

Every write is atomic and leaves a one-generation backup next to the config
(`<config>.sk-wire.bak`). Foreign servers and unrelated keys in the file are preserved -
a wire is a merge, never a rewrite. A second `sk wire` is a no-op (`already`), not a
duplicate.

Per-host locations, so you can hand-edit instead:

| Host | User-scope config | Key |
| --- | --- | --- |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS), `%APPDATA%\Claude\claude_desktop_config.json` (Windows), `~/.config/Claude/claude_desktop_config.json` (Linux) | `mcpServers` |
| Claude Code | `~/.claude.json` | `mcpServers` |
| Cursor | `~/.cursor/mcp.json` | `mcpServers` |
| VS Code | `<user-config>/Code/User/mcp.json` (+ Insiders/VSCodium) | `servers` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` |

VS Code's **mcp.json** is JSONC (comments are legal). A comments-bearing file is only ever
rewritten with an explicit `--allow-jsonc` - the comments would be lost - so by default
`sk wire` answers `needs-manual` and prints the exact entry to paste.

## Drop it into this project only

Project-scope configs travel with the repo and are pinned to the project root:

```console
$ cd /path/to/project
$ sk wire --project          # .mcp.json (Claude Code), .cursor/mcp.json, .vscode/mcp.json
```

The generated command carries `--root <project>` so the sandbox starts at the project
no matter where the host launches it. Hosts without a project-scope config are skipped -
project mode never falls back to writing your home directory.

## Expose it over HTTP instead of stdio

Any client that speaks streamable-http - including non-Python projects - can use a URL:

```console
$ sk mcp --transport streamable-http --port 8765
[skeletonkey] listening on http://127.0.0.1:8765/mcp (streamable-http)
```

```console
$ sk wire --transport streamable-http        # url entry for hosts that support it
```

The same advertisements, gates and budgets apply over HTTP (`skills.install` stays
gated, read-only stays read-only). The server binds `127.0.0.1` by default (`mcp.host`)
and speaks no transport-level authentication: keep it loopback, or put an authenticating
proxy in front - do not bind it to a public interface.

## Prove it: `sk doctor`

One JSON blob (human-readable by default, `sk --json doctor` for machines), in a fixed
check order, with a documented, stable schema:

| Check | What it answers |
| --- | --- |
| `meta` / `config` | versions, which config files layered in, which overrides applied |
| `roots` | each root exists and is writable |
| `state` | state/spill/journal dirs (a never-ran workspace is healthy; partial state is the reported smell) |
| `tools` | registered vs advertised, tier, token cost, digest, the gated and why |
| `skills` / `profile` | skill load errors, capability probe receipts |
| `journal` / `ledger` | journal entries, ledger chain verification |
| mcp.stdio | **a live end-to-end test**: starts the real server on a scratch workspace, initializes, lists tools, calls `fs.stat {path}` |
| `wire` | which hosts are installed here and which point at us |

Exit code mirrors the report. `sk doctor --fix` performs only the safe repairs (create
missing state/spill/journal dirs, retire a stale profile cache) and says what it did;
diagnosis itself is read-only - it never creates your state dir as a side effect of
looking at it.

## Troubleshooting

- **Host shows no tools** - restart it; then `sk doctor`: if the mcp.stdio check passes, the
  server is fine and the host is launching something else (wrong interpreter is the
  usual cause - re-run `sk wire`, or pin one with `sk wire --python /abs/python`).
- **the mcp.stdio check fails with `ModuleNotFoundError: mcp`** - the `[mcp]` extra is missing:
  `pip install "skeletonkey-toolforge[mcp]"`. The core stays zero-dependency (ADR-0001);
  the transport is the one extra.
- **`needs-manual` on VS Code** - comments in the mcp.json file; paste the printed entry, or
  accept the comment loss with `sk wire --allow-jsonc`.
- **Undo a wire** - restore the .sk-wire.bak backup file, or `sk wire --remove <host>`.
