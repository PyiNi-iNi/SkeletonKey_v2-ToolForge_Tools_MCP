# Live HMR / LiveREPL implementation plan (blueprint-mapped, sequential)

This is the unified build plan for the blueprint's twelve steps, in execution
order, each mapped to real code in this repo. Status marks: **shipped** =
merged behavior with tests (`tests/test_live.py`), **ready** = seam exists,
**next** = planned work. The normative behavior contract lives in
[LIVE-HMR.md](LIVE-HMR.md); this file is the *plan*, not the spec.

## Phase L0 — foundation (the REPL as the mutation surface)

| # | Blueprint step | Status | Where |
| --- | --- | --- | --- |
| 1 | Integrate the LiveREPL into the main loop | **shipped** | `live.repl` executes against the program namespace; no `refresh_debugger()` tick needed — the panel polls, and every mutation re-renders through the runtime instead |
| 2 | Registry-based execution (agents/tools/handlers) | **shipped** | dict-of-callable registries; `__live_registries__` declaration; surfaced on the debugger page |

The blueprint's PySimpleGUI windows are replaced by the served panel (`/` for
the frame, `/agents` for the debugger): same introspection intent, zero local
GUI dependency, reachable from any MCP host or browser.

## Phase L1 — the HMR core

| # | Blueprint step | Status | Where |
| --- | --- | --- | --- |
| 3 | File watcher | **shipped** | stdlib poll backend + `watchfiles` fast path; debounced; sandbox-rooted; backend reported in `live.status` |
| 4 | Reload manager with state export/import | **shipped** | runtime reload pipeline + `__hmr_export_state__` / `__hmr_import_state__`; transactional (scratch exec first) |
| 5 | Dependency graph + affected-only reloads | **shipped (scoped)** | same-tree imports tracked per program; saving a dep patches its `sys.modules` object in place and re-renders importers; cross-package closure is **next** |
| 6 | Registry rebinding after reload | **shipped** | `__live_registries__` re-point pass; rendered unnecessary in the common case by the in-place `__code__` swap |

Key divergence from the blueprint's `importlib.reload` sketch, deliberate and
recorded in ADR-0011: reloads do **not** re-execute-and-rebind the live
module. Functions/classes patch in place and state is reconciled with a 3-way
merge, so held references, live instances, and REPL-made mutations all survive
— the properties Fast Refresh has and naive reload loses.

## Phase L2 — the preview panel (2D + 3D)

| # | Blueprint step | Status | Where |
| --- | --- | --- | --- |
| 7 | 3D preview panel | **shipped** | `/view3d` — blueprint Option C: persistent `requestAnimationFrame` loop over a scene registry (`/scene.json`), hot-swapped by version polling; hand-rolled perspective renderer, zero-dependency |
| 8 | HMR for UI components | **shipped** | the component is `render()`; code edits reload the program which repaints; scene-node edits go through `live.scene` by stable `id` (upsert, not flicker) |
| 9 | Embed panel in the IDE | **ready** | the panel is plain HTTP/HTML/SVG/JSON: any webview embeds `/`; Arena's live preview exposes it directly |
| 10 | IPC for editor ↔ preview sync | **shipped (as REST+SSE)** | editor→preview: `POST /repl`, `POST /api/control`; preview→editor: `/state`, `/api/history`, `/events`. Editor-side AST patch-back from scene edits (blueprint Option B) is **next** and lands as a `live.scene → fs.patch` bridge, journaled like any fs write |

## Phase L3 — agent runtime integration

| # | Blueprint step | Status | Where |
| --- | --- | --- | --- |
| 11 | Agent runtime introspection | **shipped** | `/agents` page: per-program live state table (with merge-ownership badges), registry browser with click-to-invoke, patch log, REPL console, engine activity rail |
| 12 | Continuous agentic dev loop | **ready** | a host already drives `live.start` → edit via `fs.patch` → watcher reloads → `live.render`/`/frame.svg` verifies — the same tools, one envelope |

## Phase L4 — hardening & scale (next queue, ordered)

1. **Cross-package dependency closure.** Today's graph is program→same-tree
   file. Extend `_import_roots` resolution to namespace hints from `roots` so a
   lib edit refreshes every importing program in topo order, with cycle = no-op
   + note. Acceptance: fixture of 3 chained modules edited leaf-first asserts
   reload order via patch-log reasons.
2. **Scene→source patch-back (blueprint Option B).** `live.scene` edits made in
   the panel(`POST`) can optionally be written back to the file as an AST patch
   through the journaled fs layer (`fs.patch` semantics, task-scoped undo).
   Acceptance: a panel-made circle lands in the file inside the undo journal;
   a reload doesn't revert it.
3. **Multi-client camera channels for `/view3d`.** Named viewer slots;
   `live.scene {op: orbit}` stays the canonical program camera.
   Acceptance: two pollers get independent views; the program's own camera is
   unchanged.
4. **watchfiles parity tests** (guarded `skipif` when the extra is absent — the
   baseline CI stays on the poll backend, matching HANDOFF's tested state).
5. **Perf budget.** `live.render` on the demo program under 5 ms; 200-node
   scene serialize under 20 ms; `live.status` under 2 ms. Guarded by a slow
   marker test.

## Exit gates

- Every phase lists its files; every claim above is either a passing test
  reference or marked **next**/**ready** — the docs-drift suite
  (tests/test_docs.py, live namespace included) fails this file otherwise.
- Phase L4 items may descend individually; none blocks L0–L3 usage.
