# HANDOFF — SkeletonKey / ToolForge v2 (P5 queue + the live.* subsystem)

Session `arena/01a05ad1-skeletonkey-v2-toolforge-tools` · 2026-08-31 (America/Chicago).
Written by the agent that shipped the `live.*` Python-HMR subsystem (LiveREPL +
hot reload + preview panel), for whatever session picks up next — P5 hardening,
the L4 live queue, or Windows CI. Read `PLAN.md` for the roadmap, `docs/` for the
contracts; this file is the *transfer* — state, next steps, and landmines.

This supersedes the P3–P4b handoff. Its standing constraints carry forward and
are collected in §6.

**Agent / model provenance.** The harness is **Arena.ai Agent Mode** (repo-cloned
sandbox, bash + file tools, auto-saved turns). Not attributable to a single base
model: Arena's Agent Mode draws on many (Claude, ChatGPT, Gemini, Grok, Qwen,
Kimi, …), and no specific one was recorded or should be assumed. Everything below
was measured against the tree at handoff, not remembered: re-measure before you
restate it.

---

## 1. State on handoff

| | |
| --- | --- |
| PR | opened/merged this session (pull request page shows the number); previous merged PRs: #2 (`arena/01a05944-…`, P3–P4b) |
| Branch | `arena/01a05ad1-…` — Arena tracks sessions by branch; keep work there |
| Surface | **56 tools registered / 55 advertised / 5 899 advertisement tokens** / digest `d3139e78632b35f3` |
| New group | **`live.*` — 11 tools** (`start stop status reload patch repl state snapshot render scene serve`) |
| Skills | 5 packs discovered, 2 synthesized tools, 0 load errors |
| Tests | **660 passed, 3 skipped, 1 xfailed** (~50 s), 21 test modules; `ruff check skeletonkey tests` clean |
| Code / docs | **18 328 lines** in `skeletonkey/`; ~3.5 k lines of docs (PLAN, 7 contract/reference docs, README — note tests exclude PLAN by design), 11 ADRs |
| Phases | P0–P4b shipped earlier; **live HMR subsystem shipped this session** (not a PLAN phase — landed alongside the roadmap); **P5 is still the next roadmap phase** |
| Preview | `sk live demo` runs the full loop; panel on the configured port (`/` frame, `/view3d`, `/agents`) |

## 2. What the live.* subsystem is (the parts, honestly)

**`live/patcher.py` — the HMR primitive Python lacks.** Functions get their
`__code__` swapped *in place* (identity + `__globals__` survive → held
references, decorators, and *existing class instances* run new code); classes
patch method-by-method on the same class object; new defs are rebuilt with
`FunctionType(code, live_ns)`. A `co_freevars` shape change degrades to a
reported **rebind**. Removed definitions are only deleted when the binding name
equals the object's `__name__` (aliases like `ref = draw` are *state*, never
pruned). Whole-file reload is **transactional**: parse+scratch-exec first; only
a clean exec merges.

**`live/runtime.py` — state-preserving host + LiveREPL.** Per-name **3-way
merge**: `base` (source value at last load) vs `live` (possibly REPL-mutated)
vs `fresh` (new source). Untouched names track the file; REPL-moved names are
preserved and *reported*; `live.reload {force_source}` reclaims by name;
restore of a `live.snapshot` hands names back to the source arm. Contracts:
`__live_keep__`, `__hmr_export_state__` (pre-patch, old code; its failure
aborts), `__hmr_import_state__` (post-merge, new code; its failure = code
patched, state not), `__live_registries__` (dict-of-callables re-pointed),
`__live_on_reload__`. REPL: `mode=auto` tries eval first, `_` is the classic
last-value, REPL-defined names get keep-listed (a save never deletes them;
the file re-owns any name it defines). A settrace wall-clock guard
(`live.exec_guard_s`) leashes runaway loops — python-level only, documented
as a leash not a sandbox. Watched same-tree imports hot-patch their
`sys.modules` object in place; from-imported names re-point only when
provably dep-owned (per-name collision rule in `reload_dependency`, don't
relax it without a test).

**`live/scene.py` / `live/panel.py` — the preview.** Retained scene graph →
deterministic SVG (2D nodes; `mesh3d` painter-sorted + lambert-shaded;
`cube3d` solid-or-wire). HTTP panel: `/` (frame + Vite-style error overlay +
in-page REPL), `/view3d` (hand-rolled perspective soft-renderer, drag-orbit,
camera is viewer-local), `/agents` (debugger: live state table with
merge-ownership badges, registry browser with click-to-invoke, patch log,
ledger activity rail). Refresh = 300 ms version poll with an SSE upgrade —
polling is the deliberately reliable default through proxies.
`POST /api/control` actions: `reload stop start patch render save_snapshot
restore_snapshot`. **Live state is per-process**; one-shot CLI control of a
long-running session is `sk live <action> --via-panel` (HTTP client mode).
`POST /repl` executes code — `live.panel_repl = false` makes pages read-only,
default bind is loopback. Say this plainly anywhere the panel is exposed.

**Zero-dep discipline held (ADR-0001):** the entire subsystem is stdlib-only;
`watchfiles` stays an optional fast path, mirroring `skills/watch.py` (report
the degraded state, never ImportError at import time).

Counts moving: README intro line, PLAN header line, and this table all carry
the 56/55/5 899 numbers — update them together. `tests/test_docs.py` now
checks the **`live` namespace** in docs' backtick spans (tool args, config
keys, error-code tables) — docs that name nonexistent live knobs fail CI once
CI exists; the docs suite is the enforcer.

## 3. What is NOT done (and why)

1. **P5 ("Scale and discovery") is untouched.** Specced in PLAN.md; still the
   roadmap's next phase.
2. **L4 queue from docs/LIVE-IMPL-PLAN.md is open:** (a) cross-package dep
   closure (today: program → same-tree files; cycle = no-op+note), (b) panel
   scene-edits written back to source as AST patches through the journaled fs
   layer (blueprint Option B), (c) per-viewer 3D camera channels,
   (d) watchfiles parity tests (guarded `skipif`; absence stays the tested
   state — **do not `pip install watchfiles` into the dev venv**),
   (e) a perf-budget slow-marker test.
3. **`ci.yml` remains un-landed** (GitHub App lacks the `workflows` permission;
   previous session's file isn't even in this checkout). Unblock = grant the
   App the permission or push the file by hand. Until then nothing gates PR
   merges; the local repro is `ruff check . && pytest -q -m "not slow"`.
4. **Windows-only surfaces of `live.*`** are untested anywhere (no win runner);
   the polling watcher and panel are stdlib-clean, but nobody has run them on
   `\\?\` paths or CRLF files.

## 4. Next steps (in order)

1. Land `ci.yml` (permissions) — same blocker as last session.
2. P5 per PLAN.md; note `live.status`/`registry.*` composition already gives
   the router a healthy surface to talk about.
3. L4(a) cross-package closure — acceptance test ids live in
   docs/LIVE-IMPL-PLAN.md §L4.
4. L4(b) scene→source patch-back — it must route through `fs.patch` semantics
   (journaled, `expect_sha`-preconditioned) or not at all.

## 5. How things run here (operational)

```bash
python3 -m venv .venv && .venv/bin/pip install -e . --no-deps pytest pytest-asyncio ruff
# add 'mcp' only if you need tests/test_mcp_stdio.py; do NOT add watchfiles
.venv/bin/pytest tests/ -q                      # 660 passed, 3 skipped, 1 xfailed
.venv/bin/python -m skeletonkey.cli live demo --host 0.0.0.0 --port 8000
.venv/bin/python -m skeletonkey.cli live repl 'hue = "#f2cc60"' --via-panel --port 8000
```

`sk` global flags (`--root`, `--json`, `--read-only`…) go **before** the
subcommand; argparse exits with "unrecognized arguments" otherwise (this bit
the session once; tests don't cover the mistake).

## 6. Landmines carried forward + new

- **Zero mandatory deps in core** (ADR-0001). If you import a third-party
  package outside an extra, the constraint test fails; the live subsystem
  passed it with no new imports at all.
- **`test_docs.py` is the docs police:** every `` `tool.id {args}` `` in
  `docs/*.md`, README, skills must name real arguments; every `` `section.key` ``
  a real config field; every error-code table row a real code. Namespaces
  include `live.*` now. PLAN.md is deliberately exempt (roadmap names the
  future).
- **`tests/test_policy_property.py` has a BURST table** covering exactly the
  mutating tools; adding a mutating tool without a burst row fails the suite.
  The proof is disk-level (snapshot diff), not error-level.
- **`watchfiles` is deliberately absent from `.venv`** — the degraded branch is
  the tested branch.
- **pyc staleness in-session:** editing `skeletonkey/live/*.py` and importing
  within the same second can serve stale bytecode (mtime granularity). If a
  just-made edit doesn't show up in a quick smoke, clear `__pycache__` and
  retry before suspecting the logic.
- The previous session's full constraint list (deny-is-first, approval
  receipts, journal-not-git, argv-over-interpolation, render-don't-rewrite) is
  in git history at the P3–P4b handoff commit if you need the archaeology.
