# SkeletonKey & ToolForge v2 — MCP
Dime’s Custom Toolkit

An adaptive toolset + skills + MCP server built for **autopilot and autonomous agents**:
dynamic tool dispatch, three shell dialects (bash, PowerShell, python), a sandboxed
filesystem with journal-and-undo, and budgets that keep the context window intact.

Phase 0 and Phase 1 are implemented; the roadmap for P2–P7 is [`PLAN.md`](PLAN.md).

```bash
pip install -e .            # core has zero mandatory dependencies (ADR-0001)
pip install -e ".[mcp,dev]" # + MCP server + pytest/ruff
```

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
| `registry.*` | 6 | describe/list/search/stats/route/expand — the agent's view of its own capabilities, ranked for a task (`route`) and tiered (`expand`) |
| `capabilities.*` | 1 | explain — why a capability is gated here, with the provider receipt |
| `skills.*` | 5 | list/load/match/install/uninstall — progressive disclosure, plus a skill that ships a script becoming a callable tool |
| `pub.*` | 9 | publishing: write-only credential store (store_put/store_list/store_delete), `{{PUB.<id>}}` placeholder scan + journaled injection (placeholders/inject), platform/payment/packaging knowledge (platforms/payments/packaging), AI-executable release test plans (testers) |
| `live.*` | 11 | Python HMR over a LiveREPL: start/stop, transactional hot-reload (in-place function/class patch, 3-way state merge), repl/state/snapshot, retained 2D+3D scene render, and an HTTP preview panel with an in-page REPL + agent debugger |
| `policy.grant` | 1 | record an approval grant; returns a receipt, and a self-grant is itself gated |
| `profile.probe` | 1 | host capability detection with receipts |
| `sandbox.*` | 4 | isolated scratch workspaces from a skill pack: `create` (dir + template + own venv + git), `runtime` (pin python_version / install packages into its venv), `run` (command inside it, venv on PATH, time/output limits), `status` (inventory / deep inspect + log) |
| `remote.*` | 0 by default | other MCP servers under `[mcp.remotes.<name>]`, enrolled as `remote.<server>.<tool>` (risk inherited, error codes pass through) |

65 registered, 63 advertised, ~6.7 k tokens of advertisement at the default `full` tier
(11 tools / 0.9 k at `core`, 38 / 3.5 k at `task`). Every call returns the same
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

Six of the 65 are **synthesized from skill packs**, which is the part a toolset usually makes
you code: `skills/shell-crossplatform/tool.toml` declares `shell.quote_check` with an inline
handler body and `shell.selftest` with `scripts/selftest.sh` (+ a PowerShell sibling), and the
`sandbox` pack ships the four `sandbox.*` workspace tools above (shared logic in
`skills/sandbox/scripts/sandboxlib.py`, thin `handler_*.py` entry scripts). The compiler turns
each declaration into a manifest whose handler runs one script through `shell.run`'s executor.
Arguments bind to argv — never into the script text — and a declaration that would
produce a callable-but-broken tool is a load error visible in `skills.list` instead. An agent
can write a pack with `fs.write` and install it in the same process:

```console
$ sk skills install ./my-skill --dry-run      # files, tool ids, the argv that would run
$ sk skills install ./my-skill                # needs skills.allow_install = true
$ sk call skill.my-skill.wordcount '{"path": "notes.txt"}'
```

## Documentation

| Doc | Read it when |
| --- | --- |
| [`PLAN.md`](PLAN.md) | you want the phase plan, risk register, non-goals |
| [`docs/TOOL-CONTRACT.md`](docs/TOOL-CONTRACT.md) | you are adding or calling a tool |
| [`docs/LIVE-HMR.md`](docs/LIVE-HMR.md) | you are driving a live program (HMR semantics, state merge law, panel routes) |
| [`docs/LIVE-IMPL-PLAN.md`](docs/LIVE-IMPL-PLAN.md) | you want the blueprint → repo build order for the live subsystem |
| [`docs/SHELL-DIALECTS.md`](docs/SHELL-DIALECTS.md) | bash vs pwsh vs python behaviour |
| [`docs/SKILLS-SPEC.md`](docs/SKILLS-SPEC.md) | you are writing a `SKILL.md` |
| [`docs/SECURITY-MODEL.md`](docs/SECURITY-MODEL.md) | you need to know what is and is *not* enforced |
| [`docs/adr/`](docs/adr) | you are about to disagree with a decision |
| [`config/skeletonkey.example.toml`](config/skeletonkey.example.toml) | every knob, with its default |
| [`PLAN.md`](PLAN.md) §6 | the CI pipeline spec — deliberately **not** committed as a workflow yet |
| [`schemas/`](schemas) | machine-checkable contract shapes |

`pytest tests/` covers the whole surface, including a raw JSON-RPC client that speaks to
the real stdio server (`tests/test_mcp_stdio.py`). Apache-2.0 — see [`LICENSE`](LICENSE).