# ADR 0011: Live HMR — in-place code swap, 3-way state merge, zero-dep watcher

Date: 2026-08-31
Status: accepted
Affects: `skeletonkey/live/` (patcher, runtime, watcher, scene, panel, tools),
`core/config.py` (`LiveConfig`), `toolkit.py` (build wiring), `cli.py`
(`sk live`), `examples/live_hmr/`, `tests/test_live.py`,
`tests/test_policy_property.py` (burst coverage), `docs/LIVE-HMR.md`,
`docs/LIVE-IMPL-PLAN.md`

## Context

Dev-loop tooling for JS assumes three platform primitives Python lacks: a
module hot-reloader that keeps references honest (`importlib.reload` rebinds
and orphans), a state model with a preservation contract, and instant visual
feedback. The `live.*` group provides "edit the file or the REPL, the running
program changes, nothing is lost" for python programs an agent is driving —
the LiveREPL-as-HMR story articulated in the project chat history.

The hard requirements:

1. **No restart, no reference drift.** A handler table, a decorator registry,
   a held callback, or an existing class instance must run the NEW code after
   a save.
2. **State survives.** A program's accumulated runtime state — including
   mutations made *through the REPL* — must not be silently reset by a save,
   yet must not silently veto edits either (the classic reload failure where
   "my change didn't take" is undiagnosable).
3. **Broken saves cannot brick the program.** A file saved mid-edit is the
   normal case of watching a live file.
4. **Zero mandatory dependencies** (ADR-0001), including the preview panel.
5. **The engine's policy surface applies unchanged.** Code execution is risk
   class `write`, same as `shell.run` — deny rules, read-only mode, and
   approvals must work with no special law.

## Options considered

1. **`importlib.reload` + explicit export/import hooks only** (the blueprint's
   §2.3 sketch). Rejected as the *default*: re-exec rebinds, so every registry
   entry, decorator output, and live instance goes stale; hooks become
   mandatory boilerplate for correctness instead of an opt-in for migration.
   (The hooks are still honoured — for state the 3-way merge cannot express.)
2. **jurigged/reloadium as a dependency.** Rejected: third-party (ADR-0001) and
   they parse-and-patch at *lineno* granularity tuned for pdb-style steppers,
   which is the wrong unit for a contract an agent reads.
3. **Full restart with state checkpointing** (pickle the world, re-exec,
   rehydrate). Rejected: pickle a running namespace is pickling file handles,
   threads, locks — the failure set is everything interesting; and identity
   restarts break every external reference exactly what HMR exists to avoid.
4. **In-place `__code__` swap + 3-way data merge + transactional scratch exec**
   (chosen). Functions/classes keep identity — existing instances included —
   so references stay live with zero registry bookkeeping; the merge owns the
   data story with three named inputs (base/live/fresh) and an explicit
   report; scratch-first exec makes a bad save a no-op with a line number.

## Decision

- **Code**: patch in place (`__code__`, defaults, kwdefaults, annotations,
  doc). New defs are rebuilt against the live namespace globals
  (`types.FunctionType(code, live_ns)`). A `co_freevars` shape change refuses
  the swap and falls back to a reported rebind. Deletions only remove bindings
  that look like definitions (binding name == `__name__`); aliases are state.
- **State**: per-name 3-way merge with the decision table in
  docs/LIVE-HMR.md §3 (`added` / `data_updated` / `preserved` / `removed` /
  `removed_kept`), `__live_keep__` for runtime-owned names, `force_source`
  for the deliberate reclaim. Snapshot/restore complements it; restore hands
  names back to the source arm.
- **Contracts**: `__hmr_export_state__` runs pre-patch on old code (failure
  aborts, nothing applied); `__hmr_import_state__` post-patch on new code
  (failure reported honestly as "code patched, state handoff failed").
  `__live_registries__` re-points dict-of-callable entries at freshly defined
  same-named objects. `__live_on_reload__` observes reports.
- **Watching**: stdlib polling baseline (mtime+size, optional content-hash
  salt), `watchfiles` as the optional fast path exactly like
  `skills/watch.py`'s degradation story; debounce collapses editor
  save-storms; watcher only *notifies* — the manager decides what changed
  (ADR-0002's render-don't-rewrite spirit: it never edits the program's file).
- **Preview**: retained scene graph rendered to SVG server-side (2D) plus a
  hand-rolled perspective soft-renderer page for 3D nodes fed by
  `/scene.json`. No JS build chain, no CDN, no dependency — ever.
- **Policy**: the exec tools are `risk="write"`, `open_world`, non-idempotent;
  paths go through the fs sandbox; `[live] enabled=false` turns the group into
  coded refusals; the panel's `POST /repl` obeys `live.panel_repl`, default
  bind loopback.

## Consequences

- Observable: `tests/test_live.py::TestPatcher`,
  `TestProgram`, `TestHMRContracts`, `TestDebugPanelRoutes`;
  `tests/test_policy_property.py` burst rows for the mutating members prove
  the walls; `tests/test_docs.py` now covers the `live` namespace in docs.
- The demo (`sk live demo`) is a self-materializing playground program whose
  mirror in `examples/live_hmr/orbital.py` is pinned by a sync test.
- Carried forward, honestly: deep references into nested closures stay old
  until the outer function runs again; the trace guard is a leash not a
  sandbox; cross-package reload closure is L4 work (docs/LIVE-IMPL-PLAN.md).
