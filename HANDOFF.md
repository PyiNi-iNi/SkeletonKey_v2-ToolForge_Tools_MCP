# HANDOFF — SkeletonKey / ToolForge v2 (P5 + live.* shipped → P6)

Session `arena/01a05a44-skeletonkey-v2-toolforge-tools` · 2026-09-01 (America/Chicago).
Written after merging **P5** (this session: discovery at scale + semantic stage + remote
MCP aggregation) with the **live.\*** subsystem (parallel session `arena/01a05ad1`, merged
to main as PRs #3/#4) — the tree in `main` now carries both. For the session that starts
**P6** (distribution + hardening). Read `PLAN.md` for the roadmap, `docs/` for the
contracts; this file is the *transfer* — state, next steps, and landmines. It supersedes
both previous handoffs; standing constraints are collected in §7.

**Agent / model provenance.** The harness is **Arena.ai Agent Mode** (repo-cloned sandbox,
bash + file tools, auto-saved turns). Not attributable to a single base model: Arena's
Agent Mode draws on many (Claude, ChatGPT, Gemini, Grok, Qwen, Kimi, …), and no specific
one was recorded or should be assumed. Everything below was measured against the merged
tree at handoff, not remembered: re-measure before you restate it.

---

## 1. State on handoff (merged `main`)

| | |
| --- | --- |
| Branch | `arena/01a05a44-skeletonkey-v2-toolforge-tools` — this session's branch; P5 PR against `main` is being merged (house style: `--merge`, branch kept) |
| `main` | `a9c9222` (merge PR #4). History: #2 (P3–P4b) → #3/#4 (`live.*`, branch `01a05ad1`) → **#5 = P5** (this branch) |
| Test suite | **701 passed, 3 skipped, 1 xfailed** in ~67 s; ruff clean (`ruff check .`; examples/live_hmr is per-file-ignored F821 — `canvas` is runtime-injected) |
| Tools | **61 registered / 59 advertised / 6410 tokens** at default `full` (digest `05c88f0f77b7fd74`); core 11/945 (`94f7da59a9f9937a`), task 38/3482 (`34cd2f31d4af4484`) |
| Groups | fs 16 · shell 11 · registry 6 · capabilities 1 · skills 5 · pub 9 · live 11 · policy.grant 1 · profile.probe 1 |
| Gated | `shell.selftest` (skill-declared `advertised = false`), `skills.install` (`skills.allow_install`) |
| Venv | repo `.venv`: mcp 2.1.1, pytest 9.1.1, ruff, pyyaml; **package installed editable** (`pip install -e .`) — required so remote *child* servers (`python -m skeletonkey.mcp`) import from any cwd. **No watchfiles** (absence is the tested state) |
| Route | 25/25 @ k=5; semantic stage reorders 13/25 eval tasks, hit-rate intact |
| Docs | ADR-0001…0011 (0011 = live HMR) + **0012** (semantic), **0013** (remote); TOOL-CONTRACT §7e (P5a), §7f (remote), §3 `REMOTE`; README measured 61/59; skills/fs-safe-refactor/references/discovery.md |

## 2. What the last two sessions actually shipped

**P5 (this session).** (a) Tiers (`core`/`task`/`full`, manifestation only; per-tier
budgets + honest `budget_drops`; `registry.expand` session switch; digest-driven
`list_changed` over the wire). (b) Two-stage `registry.route` (exact → lexical with
`reasons` → semantic), provider receipts in snapshots/`registry.list`/MCP `_meta`/
`capabilities.explain`; cursor pagination on `registry.list` and MCP `tools/list`. (c)
Semantic backend `lexical-tfidf` (pure stdlib TF-IDF cosine, entry-point registered,
gated by `tools.semantic`; blends 50/50 with normalized lexical; deterministic; the
*only* shipped backend — an embedding extra can be added behind the same protocol).
(d) `fs.search` provider-fallback honesty (vanished `rg` → python walker with
`metrics.provider` + a naming warning; `prefer` still raises `MISSING_BINARY`). (e)
`mcp.client` connector (ADR-0013): `[mcp.remotes.<name>]` → `remote.<server>.<tool>`,
risk inherited (unannotated ⇒ `write`), `reversible: false`/`stateful: "host"`, remote
error codes verbatim (foreign → `REMOTE`), connect/list failures are `load_errors` +
build report, stats rows carry `source` + `stats_by_source()`.

**live.\*** (parallel session). Stdlib-only Python HMR: in-place `__code__`/method
patch (identity + globals survive), transactional whole-file reload (parse + scratch-exec
first; `__hmr_export_state__`/`__hmr_import_state__` hooks; `__live_keep__`, `__live_
registries__`), per-name 3-way state merge (base/live/fresh), settrace wall-clock leash,
watched same-tree dep hot-patch; retained scene graph → SVG/`mesh3d`/`cube3d` renderers;
HTTP preview panel (`/`, `/view3d`, `/agents`) with in-page REPL + agent debugger via
`POST /api/control` and `POST /repl` (`live.panel_repl = false` makes pages read-only;
default bind loopback); `sk live <action> --via-panel` HTTP client mode. 11 tools:
`live.start/stop/status/reload/patch/repl/state/snapshot/render/scene/serve`.

## 3. What is NOT done (and why)

1. **P6 not started** (PLAN §6): wheel/sdist releases + `pipx` story, `sk doctor` +
   `--fix`, in-repo docs site (write-a-skill, connect-a-host), security pass (dependency
   audit, sentinel/path property + bypass test matrix), **Windows CI runner** (turns
   `@pytest.mark.win` skips into real checks).
2. **`ci.yml` still un-landed.** GitHub App lacks the `workflows` permission — any push
   touching `.github/` is rejected by design. Unblock: grant the App, or have the user
   push `.github/workflows/ci.yml` (it is written and untracked in the checkout). Until
   then the local repro is `ruff check . && pytest -q -m "not slow"`.
3. **L4 live queue open** (docs/LIVE-IMPL-PLAN.md): (a) cross-package dep closure, (b)
   panel scene-edits written back to source via journaled `fs.patch`, (c) per-viewer 3D
   camera channels, (d) watchfiles parity tests (`skipif`; keep watchfiles absent), (e)
   perf-budget slow-marker test.
4. **Windows-only surfaces untested anywhere** (no win runner): `live.*` on `\\?\`/CRLF;
   store CHMOD 0600's NT semantics; pwsh strict mode round-trips.
5. Optional quick wins not taken: publish task in `tests/eval/suite.jsonl` + one replay
   fixture; store `expiry`; `registry.explain_all` (whole-surface gates in one call).

## 4. Next steps (in order)

1. Confirm the P5 PR merged cleanly on `main` (`gh pr view --mergeable`, then `git
   ls-remote origin refs/heads/main`), then pull/rebase the next session's branch onto
   it.
2. Land `ci.yml` (permission or manual push) — same blocker; it gates nothing until then.
3. **P6** per PLAN §6. First sub-step that needs no network/permission: `sk doctor` +
   the zero-dep core-guarantee test (import `skeletonkey.core` with `site-packages`
   hidden), then the security bypass matrix, then packaging/docs; Windows CI last (needs
   the App permission anyway).
4. If P6 stalls: L4(a) cross-package closure (acceptance ids in LIVE-IMPL-PLAN §L4) or
   the tiny honesty wins in §3.5.

## 5. How things run here (operational)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # mcp+watch+dev; the sandbox
#                         reinstall includes mcp for test_mcp_stdio.py and -e for remote children
.venv/bin/pytest tests/ -q                       # 701 passed, 3 skipped, 1 xfailed
.venv/bin/ruff check .                           # clean (examples/live_hmr F821 ignored)
.venv/bin/python -m skeletonkey.cli live demo --host 0.0.0.0 --port 8000
.venv/bin/python -m skeletonkey.cli live repl 'hue = "#f2cc60"' --via-panel --port 8000
```

`sk` global flags (`--root`, `--json`, `--read-only`…) go **before** the subcommand
(argparse aborts otherwise). Sandbox recycles: `.venv` and `.git` reset mid-session —
recreate the venv, `git fetch origin refs/heads/<branch>:refs/remotes/origin/tip`, and
restore the branch with `git reset --mixed origin/tip` (files reappear as a patchset and
hash-match the remote tip; the delta is only new work).

## 6. Ideas (honest, prioritized — none are decided)

- `registry.explain_all`: whole-surface gates + receipts in one projection (the
  200-tool world debuggable from a prompt). Small, same data.
- `route` → compact "tool shortlist" block for the next prompt (UX decision for the
  autopilot loop; data already carries reasons).
- Remote tools are `full`-tier and count toward caps; decide whether trusted servers can
  opt into `core`/`task` tiers.
- Do **not** build `pub.run_plan` (a loop concern, not a tool) or a mini-interpreter in
  any tool. Still true after two sessions of prompting.

## 7. Standing constraints (carried, reaffirmed)

- **Licensing/identity frozen:** Apache-2.0, authorship "Dime", README title + tagline
  unchanged. No relicense/retitle/author-tidying without the owner.
- **Python 3.11+, zero mandatory deps** (ADR-0001) — a test imports `skeletonkey.core`
  with `site-packages` hidden; `mcp` and `watchfiles` are extras; `mcp.client` imports
  `mcp` lazily so a no-remotes build never pays it.
- **Windows + Linux + macOS first-class; PowerShell not optional.** Every claim backed by
  a rendered-payload assertion or a `win`-tagged self-skipping test.
- **Primary consumer is the bespoke autopilot loop; MCP surface ships and stays honest.**
  No silent reordering (rankings/gates/receipts are data); a remote server's error code
  is never re-wrapped.
- **House rule for new tools:** TOOL-CONTRACT section (or extension), skill-guidance
  entry, **wire-level** test. Spec-first + 3-ish chunks + explicit `git add`; `.github/`
  untracked.

## 8. Landmines (measured; old ones still bite)

- **Sandbox recycle mid-session** (hit twice): venv + `.git` reset; remote is the only
  durable record — push early. Recovery in §5.
- **mcp 2.1.1 lowlevel:** `tools/list` params model is `PaginatedRequestParams` directly
  (not a `ListToolsRequest` wrapper — registering the wrapper makes the cursor silently
  never arrive); result `meta` serializes as `_meta`; check `mcp_types` snake/camel fields
  (`read_only_hint`, `input_schema`, `is_error`).
- **RemoteServer keep-alive:** the worker thread IS the event loop — use
  `asyncio.sleep(0.25)`, never a threading `Event.wait` (that froze every
  `run_coroutine_threadsafe` call this session).
- **Drop-in contract is `TOOL`/`TOOLS`/`register()`** (not `TOOL_SPECS`).
- **`engine.call` returns a failure `ToolResult` for UNKNOWN_TOOL** — assert
  `r.error.code`, don't `pytest.raises`.
- **`test_policy_property.py` BURST table** names every mutating tool — adding one
  without a row fails the suite (live.* added theirs; keep it in sync).
- **`tests/test_docs.py` is the docs police** for `docs/*.md`, README, skills: every
  `` `tool.id {args}` `` must name real args, every `` `section.key` `` a real config
  field, every error-code row a real code. Namespaces now include `live.*` and `remote.*`.
  PLAN.md is deliberately exempt.
- **pyc staleness in-session:** editing `skeletonkey/live/*.py` then importing within the
  same second can serve stale bytecode (mtime granularity); clear `__pycache__` and retry.
- **Managed/demo files:** `examples/live_hmr/orbital.py` is a *mirror* of
  `skeletonkey/live/demos.py` (a test enforces sync) — edit `demos.py`, not the example;
  `canvas` is runtime-injected (F821 ignored there by design).
- `SkeletonKeyError.err.code` is the string `"BAD_ARGS"`; `registry.all()` is a method
  returning manifests; `AdSnapshot` has `.tokens`/`.digest` (no `.tokens_estimate`);
  skill inject cap for `fs-safe-refactor` ≈ 3995 tokens (detail goes in `references/`);
  remote tests need the package installed editable.
- Old unchanged: `E` namespace class; `_ledger` swallows exceptions; legacy
  `deny: ["**"]`; path denies need `tool(**/glob)`; `fs.glob` dotfile behavior;
  `ReadResult.sha256` 16 chars; `req.meta` plain dict + positional progress args; replay
  task_id match; `cmd | head` exit code; pytest from repo root.

## 9. Where things live (pointers, not contents)

- `PLAN.md` — §5 P5a/P5b (shipped), §6 P6, risk register; ADR index rows 0010–0014.
- `docs/TOOL-CONTRACT.md` — §3 errors, §7e (discovery), §7f (remote), §8 checklist.
- `docs/adr/0011-live-hmr-…`, `0012-semantic-backend.md`, `0013-remote-tools-passthrough.md`.
- `skeletonkey/core/{registry,semantic,config,errors}.py`; `skeletonkey/mcp/client.py`
  (remotes); `skeletonkey/mcp/adapter.py` (tier + pagination + `_meta` receipts);
  `skeletonkey/fsx/search.py` (fallback); `skeletonkey/tools/builtin.py` (route/expand/
  explain/stats + search handler); `skeletonkey/live/*` (HMR subsystem).
- Tests: `test_discovery.py` (P5a ACs + AC2 both modes), `test_semantic.py`,
  `test_remotes.py` + `remote_helpers.py`, `test_mcp_stdio.py` (wire: P5a + remote),
  `test_live.py` (live.*), `test_tools_builtin.py` (search fallback),
  `tests/eval/suite.jsonl` (25 tasks, `target` ground truth).
- `skills/fs-safe-refactor/references/discovery.md`; `config/skeletonkey.example.toml`
  (`[advertise]`, `tools.semantic`, `[mcp.remotes.<name>]` sample);
  `docs/LIVE-HMR.md`, `docs/LIVE-IMPL-PLAN.md` (live queue).
