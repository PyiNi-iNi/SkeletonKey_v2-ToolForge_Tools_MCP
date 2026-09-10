# SkeletonKey & ToolForge v2 — MCP
Dime’s Custom Toolkit

An adaptive toolset + skills + MCP server built for **autopilot and autonomous agents**:
dynamic tool dispatch, three shell dialects (bash, PowerShell, python), a sandboxed
filesystem with journal-and-undo, and budgets that keep the context window intact.

Phase 0 and Phase 1 are implemented; the roadmap for P2–P7 is [`PLAN.md`](PLAN.md).

```bash
pip install -e .            # core has zero mandatory dependencies (ADR-0001)
pip install -e ".[mcp,dev]" # + MCP server + pytest/ruff
                            # install with -e: some tests shell out to `sk`
                            # see CONTRIBUTING.md if the suite is not green
```

## Drop it into any project

Install with the MCP extra, wire every detected host, and prove it — three commands:

```console
$ pip install "skeletonkey-toolforge[mcp]"     # or pipx; the core alone has zero deps
$ sk wire                                      # writes the mcpServers entry into every
                                               # detected host (merge, backup, idempotent)
$ sk doctor                                    # live end-to-end proof: starts the real
                                               # server, lists tools, calls fs.stat
```

`sk wire --project` drops project-scope configs (`.mcp.json`, `.cursor/mcp.json`,
`.vscode/mcp.json`) with the sandbox pinned to the project; `sk wire --read-only` exposes
only the read-only surface; `sk wire --remove` takes back exactly what it wrote. Prefer a
URL? `sk mcp --transport streamable-http --port 8765` serves the same toolkit to any
streamable-http client, and `sk wire --transport streamable-http` points URL-capable
hosts at it. Per-host stanzas, the doctor's schema and the troubleshooting table live in
[`docs/CONNECT-A-HOST.md`](docs/CONNECT-A-HOST.md).

## Two ways to drive it

```bash
sk profile                      # what can this host actually run, with probe receipts
sk tools search "rename a symbol across the repo"
sk call fs.patch '{"path":"src/app.py","edits":[{"old_text":"x = 1","new_text":"x = 2"}]}'
sk shell "Get-Process | Select-Object -First 3" --dialect pwsh   # one protocol, any dialect
sk fs undo-task turn-7          # retract everything one turn touched
sk call shell.quote '{"args":["/tmp/a b","$HOME"],"dialect":"pwsh"}'   # literal tokens per dialect
```

```jsonc
// mcpServers entry for any stdio client
{ "mcpServers": { "skeletonkey": { "command": "python", "args": ["-m", "skeletonkey.mcp"] } } }
```

```python
from skeletonkey.toolkit import build
tk = build()
tk.engine.call("fs.search", {"pattern": "TODO", "path": "src"})
tk.engine.advertise()          # the tool list a host is allowed to see, with a digest
tk.skills.context_block("fix the flaky windows path test")
tk.engine.call("shell.run", {"script": 'printf "[%s]" "$@"', "dialect": "bash",
                             "argv": ["a b", "it's", "$HOME"]})   # argv never meets a shell
```

## What is here

| Group | Tools | Notes |
| --- | --- | --- |
| `fs.*` | 16 | read/write/patch/search/list/glob/stat/sniff/move/delete/mkdir/chmod + journal_list, undo, redo, undo_task |
| `shell.*` | 11 | run (with `argv`)/quote/quote_check/available/jobs/job_wait/job_kill/job_watch/sessions/session_reset/selftest; bash, sh, zsh, fish, pwsh, powershell 5.1+7, python |
| `registry.*` | 7 | describe/list/search/stats/route/expand/explain_all — the agent's view of its own capabilities, ranked for a task (`route`), tiered (`expand`), and with every gate explained (`explain_all`) |
| `capabilities.*` | 1 | explain — why a capability is gated here, with the provider receipt |
| `skills.*` | 5 | list/load/match/install/uninstall — progressive disclosure, plus a skill that ships a script becoming a callable tool |
| `pub.*` | 9 | publishing: write-only credential store (store_put/store_list/store_delete), `{{PUB.<id>}}` placeholder scan + journaled injection (placeholders/inject), platform/payment/packaging knowledge (platforms/payments/packaging), AI-executable release test plans (testers) |
| `live.*` | 11 | Python HMR over a LiveREPL: start/stop, transactional hot-reload (in-place function/class patch, 3-way state merge), repl/state/snapshot, retained 2D+3D scene render, and an HTTP preview panel with an in-page REPL + agent debugger |
| `policy.grant` | 1 | record an approval grant; returns a receipt, and a self-grant is itself gated |
| `profile.probe` | 1 | host capability detection with receipts |
| `remote.*` | 0 by default | other MCP servers under `[mcp.remotes.<name>]`, enrolled as `remote.<server>.<tool>` (risk inherited, error codes pass through) |

62 registered, 60 advertised, ~6.6 k tokens of advertisement at the default `full` tier
(12 tools / 1.1 k at `core`, 39 / 3.6 k at `task`). Every call returns the same
envelope and the same error taxonomy: see
[`docs/TOOL-CONTRACT.md`](docs/TOOL-CONTRACT.md). The two not advertised are
`shell.selftest` (declared `advertised = false` by its skill) and `skills.install`
(gated until `skills.allow_install = true`) — both stay *registered* and
`registry.describe`/`capabilities.explain` say exactly why.

```console
$ sk live demo                        # materialize a playground program + watch it + serve the panel
$ sk live repl 'hue = "#f2cc60"' --via-panel --port 8010   # one-shot mutate of the running program
$ sk live patch --name render --file new_render.py --via-panel   # hot-swap code without touching disk
$ sk live reload --force-source hue --via-panel   # hand one name back to the file after REPL experiments
```

Two of the 62 are **synthesized from a skill pack**, which is the part a toolset usually makes
you code: `skills/shell-crossplatform/tool.toml` declares `shell.quote_check` with an inline
handler body and `shell.selftest` with `scripts/selftest.sh` (+ a PowerShell sibling), and the
compiler turns each into a manifest whose handler runs one script through `shell.run`'s
executor. Arguments bind to argv — never into the script text — and a declaration that would
produce a callable-but-broken tool is a load error visible in `skills.list` instead. An agent
can write a pack with `fs.write` and install it in the same process:

```console
$ sk skills install ./my-skill --dry-run      # files, tool ids, the argv that would run
$ sk skills install ./my-skill                # needs skills.allow_install = true
$ sk call skill.my-skill.wordcount '{"path": "notes.txt"}'
```

## The dev viewport

```bash
sk dev app.py       # live HMR panel + read-only tool catalog, cross-linked
```

One server, two surfaces: the panel runs your code and hot-reloads it on save
at `/`, and the read-only tool catalog is mounted at `/tools`. Keep it on
loopback - the REPL executes code, and the catalog now shares its bind
([ADR-0018](docs/adr/0018-mount-the-catalog-in-the-panel.md)). `sk dev
--two-servers` splits them back apart.

## See the toolset

`sk viz` renders every registered tool — advertised or not — as one self-contained
page: no npm, no CDN, no build step, opens from `file://` with the network off.

```console
$ sk viz -o tools.html      # catalog, graph, token treemap, matrix, latency, runtime
$ sk viz --serve            # the same page, and it notices when the toolset changes
$ sk viz --why-not          # every withheld tool, the rule that withheld it, the fix
$ sk viz --drift            # lint the manifests against the ledger; non-zero if drifted
```

Four things it answers that nothing else did:

- **what is here** — 81 registered / 79 advertised, 11 groups, 74 capabilities, all
  filterable, with schemas and worked examples one click away;
- **why you cannot call this** — a 10-step decision chain mirroring
  `Registry.gate()`, each step carrying its evidence and an actionable fix
  (`sk viz --explain shell.run`);
- **what another host would see** — `--as-host windows` derives a profile without
  re-probing, and `--diff` compares two hosts or two commits;
- **whether a manifest still tells the truth** — the runtime overlay reads the
  ledger and flags tools whose measured p95 outruns their declared
  `typical_latency_ms` by more than 3×. `sk viz --drift` exits non-zero, so manifest
  accuracy can be a CI gate.

It is a read-only projection of `advertise()` and holds no facts of its own
([ADR-0016](docs/adr/0016-visualizer-is-a-projection.md)); the server has no POST
handler at all. Details in [`docs/TOOL-VISUALIZER.md`](docs/TOOL-VISUALIZER.md).

## Documentation

| Doc | Read it when |
| --- | --- |
| [`PLAN.md`](PLAN.md) | you want the phase plan, risk register, non-goals |
| [`docs/TOOL-CONTRACT.md`](docs/TOOL-CONTRACT.md) | you are adding or calling a tool |
| [`docs/LIVE-HMR.md`](docs/LIVE-HMR.md) | you are driving a live program (HMR semantics, state merge law, panel routes) |
| [`docs/LIVE-IMPL-PLAN.md`](docs/LIVE-IMPL-PLAN.md) | you want the blueprint → repo build order for the live subsystem |
| [`docs/SHELL-DIALECTS.md`](docs/SHELL-DIALECTS.md) | bash vs pwsh vs python behaviour |
| [`docs/SKILLS-SPEC.md`](docs/SKILLS-SPEC.md) | you are writing a `SKILL.md` |
| [`docs/CONNECT-A-HOST.md`](docs/CONNECT-A-HOST.md) | you are wiring a host or debugging an install (`sk wire`, `sk doctor`) |
| [`docs/TOOL-VISUALIZER.md`](docs/TOOL-VISUALIZER.md) | you want to *see* the toolset — the catalog, the graph, why a tool is withheld, whether a manifest still tells the truth (`sk viz`) |
| [`docs/ROADMAP-V10-V16.md`](docs/ROADMAP-V10-V16.md) | you want to know what is planned next, what is deliberately declined, and why |
| [`docs/SECURITY-MODEL.md`](docs/SECURITY-MODEL.md) | you need to know what is and is *not* enforced |
| [`docs/adr/`](docs/adr) | you are about to disagree with a decision |
| [`config/skeletonkey.example.toml`](config/skeletonkey.example.toml) | every knob, with its default |
| [`PLAN.md`](PLAN.md) §6 | the CI pipeline spec — deliberately **not** committed as a workflow yet |
| [`schemas/`](schemas) | machine-checkable contract shapes |

`pytest tests/` covers the whole surface, including a raw JSON-RPC client that speaks to
the real stdio server (`tests/test_mcp_stdio.py`). Apache-2.0 — see [`LICENSE`](LICENSE).