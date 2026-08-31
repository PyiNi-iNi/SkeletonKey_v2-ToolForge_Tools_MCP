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
| `fs.*` | 14 | read/write/patch/search/list/glob/stat/sniff/move/delete/mkdir + journal, undo, undo_task |
| `shell.*` | 10 | run (with `argv`)/quote/available/jobs/job_wait/job_kill/sessions; bash, sh, zsh, fish, pwsh, powershell 5.1+7, python |
| `registry.*` | 4 | describe/list/search/stats — the agent's view of its own capabilities |
| `skills.*` | 3 | list/load/match (progressive disclosure) |
| `profile.probe` | 1 | host capability detection with receipts |

32 registered, 30 advertised, ~1.9 k tokens of advertisement. Every call returns the same
envelope and the same error taxonomy: see
[`docs/TOOL-CONTRACT.md`](docs/TOOL-CONTRACT.md).

## Documentation

| Doc | Read it when |
| --- | --- |
| [`PLAN.md`](PLAN.md) | you want the phase plan, risk register, non-goals |
| [`docs/TOOL-CONTRACT.md`](docs/TOOL-CONTRACT.md) | you are adding or calling a tool |
| [`docs/SHELL-DIALECTS.md`](docs/SHELL-DIALECTS.md) | bash vs pwsh vs python behaviour |
| [`docs/SKILLS-SPEC.md`](docs/SKILLS-SPEC.md) | you are writing a `SKILL.md` |
| [`docs/SECURITY-MODEL.md`](docs/SECURITY-MODEL.md) | you need to know what is and is *not* enforced |
| [`docs/adr/`](docs/adr) | you are about to disagree with a decision |
| [`config/skeletonkey.example.toml`](config/skeletonkey.example.toml) | every knob, with its default |
| [`PLAN.md`](PLAN.md) §6 | the CI pipeline spec — deliberately **not** committed as a workflow yet |
| [`schemas/`](schemas) | machine-checkable contract shapes |

`pytest tests/` covers the whole surface, including a raw JSON-RPC client that speaks to
the real stdio server (`tests/test_mcp_stdio.py`). Apache-2.0 — see [`LICENSE`](LICENSE).
