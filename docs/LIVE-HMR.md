# Live HMR + LiveREPL (normative contract of the `live.*` group)

LiveREPL here is a **live, in-process REPL for a running program's namespace**:
inspect variables, execute expressions, mutate state — no restart. Layered on
top, this subsystem adds the HMR loop Python does not ship: a file watcher, a
transactional hot-patcher for functions/classes/modules, a state-preserving
runtime, and a browser preview panel (2D SVG + 3D view) with an in-page REPL
and an agent-debugger page.

This document is the contract. `tests/test_live.py` executes it; where prose and
tests disagree, the tests are right.

## 1. The loop

```
save file ──▶ watcher (poll | watchfiles) ──▶ scratch compile+exec ──▶ ok?
                                               │                        │
                                            no ▼                        ▼ yes
                                      error overlay on panel     in-place patch
                                      old code keeps running     3-way state merge
                                                                 registry rebind
                                                                 render() → frame++
```

A program is a python file. It is exec'd into a managed namespace with two
things injected: `canvas` (a Scene of 2D + orbit-3D nodes) and `__argv__`.
If the program defines `render()`, that hook owns the frame: it is called
after load, after every reload, after every REPL mutation, after every patch.
`render()` may also *return* a raw SVG string to bypass the canvas entirely.

```python
color = "red"          # module-level state: survives reloads (see §3)
ticks = 0

def render():          # the frame is a function call
    global ticks
    ticks += 1
    canvas.rect(10, 10, 80, 40, fill=color, id="hero")
    canvas.text(30, 70, f"ticks={ticks}", id="hud")
```

## 2. Tools

| Tool | Risk | What it does |
| --- | --- | --- |
| `live.start` | write | Load a file as a live program (watched by default); executes it once. |
| `live.stop` | write | Stop one program, or all when `program` is omitted. |
| `live.status` | read | Programs, watcher backend + targets, panel, config, optional history. |
| `live.reload` | write | Re-read the file now and merge it (the watcher's manual twin). |
| `live.patch` | write | Hot-swap one `def`/`class` from `code` in place; `name` may be `Cls.meth`. |
| `live.repl` | write | Eval/exec `code` in the live namespace; re-renders by default. `_` = last value. |
| `live.state` | read | Every visible name: type, clipped repr, ownership badge. |
| `live.snapshot` | write | `op` = save / restore / list deep-copied state checkpoints. |
| `live.render` | read | Force a frame; `svg: true` inlines the SVG. Errors land in the overlay field. |
| `live.scene` | write | Retained scene ops: upsert/remove/clear/list/orbit by node `id`. |
| `live.serve` | write | Start/stop the HTTP preview panel (`host`, `port` from config by default). |

All of them return the standard envelope (docs/TOOL-CONTRACT.md). In-place
mutations of *state* are not filesystem writes, so undo is expressed via
snapshots, not the fs journal.

## 3. State semantics: the 3-way merge

This is the law of the system, stated once:

For every module-level data name at reload time there are three values:

- **base** — what the source set it to at the last successful load;
- **live** — what the running namespace holds now (possibly moved by the REPL);
- **fresh** — what the newly saved source wants.

Decision:

| Condition | Winner | Reported as |
| --- | --- | --- |
| name is new in the source | fresh | `added` |
| name in `__live_keep__` / keep list | live | `preserved` |
| live == base (untouched since load) | fresh | `data_updated` if it changed |
| live != base (moved by REPL/restore) | live | `preserved` |
| listed in `live.reload {force_source}` | fresh | `data_updated` (+ note) |
| name vanished from source, untouched | — removed — | `removed` |
| name vanished from source, moved live | live (orphan kept) | `removed_kept` |

Corollaries: a REPL assignment is sticky across saves until you either
restore a snapshot (which hands ownership back to the source) or reload with
force_source. Functions and classes are never 3-way-merged: the file owns
code it defines, always (with the alias exception of §4).

## 4. Code patching (the Fast Refresh analog)

`importlib.reload` rebinds names and orphans every held reference. The patcher
instead **swaps code objects in place**:

- A patched function keeps its identity and its `__globals__`; anything holding
  a reference (registries, decorators, closures, other modules) runs the new
  code on its next call.
- A patched class keeps its identity and its instances: methods (including
  `staticmethod`/`classmethod`) swap on the class object, existing instances get
  new behaviour with their attribute state untouched. `__init__` is patched but
  not re-run for live instances.
- New top-level functions are rebuilt against the live namespace
  (`types.FunctionType(code, live_ns)`), never left reading the scratch dict.
- If the closure-capture shape changed (`co_freevars` mismatch), in-place swap
  is refused with a rebind fallback, reported under `rebound`.
- A removed definition is deleted only if the binding looks like a definition
  (binding name == `__name__`). Aliases (`ref = draw`) are state, deleted never.

Whole-file reloads are **transactional**: the new source is parsed, compiled
and executed in a scratch namespace first; only a clean exec reaches the
namespace merge. Failures produce `PARSE` (syntax) / `TIMEOUT` (guard) /
`INTERNAL` (module body raised) reports with line/offset and keep the old code
serving frames; the panel shows the error as a red overlay, Vite-style.

### Dependencies

`import foo` / `from foo import bar` where `foo` resolves to a file inside a
declared root is a **watched dependency**: saving `foo.py` patches its module
object in `sys.modules` *in place* (same algorithm), then re-points the
program's own `from`-imported names — but only names provably owned by that
module (callables whose `__module__` matches, or data equal to the dep's old
base), so a name the program itself defined is never clobbered. Editing the
program file refreshes its dependency edges. Stdlib/site-packages modules are
never hot-patched.

### Explicit contracts (opt-in)

```python
__live_keep__ = ["cache"]            # names that always belong to the runtime
__live_registries__ = ["HANDLERS"]   # dict-of-callables re-pointed after reload

def __hmr_export_state__():          # runs on the OLD code, pre-patch;
    return {"calls": calls}          # a failure here aborts the reload cleanly

def __hmr_import_state__(state):     # runs on the NEW code, post-merge; its
    ...                              # failure means code WAS patched, state wasn't

def __live_on_reload__(report):      # observer; receives the patch report dict
```

Registry rebinding is how `importlib`-style handler tables stay live from the
blueprint pattern (`HANDLERS = {"job": run_job}`); in most cases it is a no-op
because the in-place swap already updated the shared object — it exists for the
entries that were rebound rather than patched.

## 5. The LiveREPL

`live.repl {code, mode}` — `mode: auto` tries an eval-compile first (expressions
return `value`, captured into `_`), else statements run as `exec` with
stdout/stderr captured (clipped at `live.repl_max_output_bytes`). Names the REPL
defines are keep-listed: a save never deletes `def` you typed at the console;
names the file also defines get patched back to the file's version on save —
the file owns what it defines, the REPL owns what it made.

Every repl/patch/reload runs under a wall-clock trace guard (`live.exec_guard_s`
seconds): a `while True:` in the REPL interrupts at the next line event with a
`TIMEOUT` envelope instead of hanging the tool loop. C-level blocking calls are
best-effort (documented leash, not a sandbox).

## 6. Preview panel (`live.serve`)

| Route | Kind | Content |
| --- | --- | --- |
| `/` | page | 2D frame (SVG), error overlay, REPL console |
| `/view3d` | page | soft-rendered 3D scene (perspective, painter sort, drag-orbit) |
| `/agents` | page | agent debugger: state table, registries, patch log, REPL, engine activity |
| `/frame.svg` | data | current frame |
| `/scene.json` | data | the scene graph (a react-three-fiber-style frontend's feed) |
| `/state` | data | the state table incl. registry listings |
| `/version` | data | version counters + program list (the page polls this ~300ms) |
| `/events` | SSE | push hint per frame (polling remains the fallback) |
| `/api/program` | data | one program's status + registries |
| `/api/history` | data | REPL history + patch log |
| `/api/activity` | data | engine ledger tail (recent tool calls) |
| `POST /repl` | mutation | the in-page console; only when `live.panel_repl` is true |
| `POST /api/control` | mutation | `{action: reload \| stop \| start \| patch \| render \| save_snapshot \| restore_snapshot}`; same code path as the tools - the panel is a client, not a back door |

### Perprocess reality, and the `--via-panel` hatch

Live state lives in the process that started it. `sk live start` then
`sk live repl` as two invocations are two processes (the first one's program
exits with it). For one-shot control of a long-lived session -
`sk live demo`, `sk live serve --wait`, or `sk mcp` - pass `--via-panel`
(optionally `--host/--port`): the CLI then speaks the panel's JSON routes
(POST /api/control, POST /repl, GET /state, GET /frame.svg) and the running
program answers. Inside agents and tests, of course, `engine.call("live.repl",
...)` drives the same process directly.

3D nodes (`cube3d`, `mesh3d` with vertices+faces, `poly3d`, `point3d`) render
orthographic+painter-shaded in the SVG path and perspective in `/view3d`; the
canvas view there (drag/wheel) is local to the viewer and never mutates the
program's camera — the program's `canvas.camera` stays canonical and can be
moved from the REPL with `canvas.orbit(...)`.

## 7. Security posture

- `live.start`, `live.repl`, `live.patch` execute python in-process. They are
  risk `write` — the same class as `shell.run` — so `policy.deny`, read-only
  mode, approvals, and `tools.disable` apply identically. `live.enabled = false`
  makes every member refuse with `TOOL_NOT_ADVERTISED` (visible, coded, actionable).
- Paths resolve through the fs sandbox: programs and watched files must live in
  declared roots; the same `SANDBOX_VIOLATION` wall as `fs.*`.
- The panel is a *power*: `POST /repl` executes code. Default bind is loopback
  (`live.host` 127.0.0.1, `live.port` 8010); set `live.panel_repl = false` to
  make the served pages read-only. Do not expose it beyond a dev sandbox.
- The exec guard is a wall-clock leash on python-level loops only — it is not
  a security boundary and never claimed to be one.

## 8. Non-goals / honest limits

- Not a module re-importer for third-party packages; stdlib/site-packages are
  never patched in place.
- Code that stashes *deep* references to nested closures stays old until its
  owner function is re-entered (Python semantics; same as every HMR runtime).
- Old-style class-attribute *instance* state is preserved; class-level data
  attributes follow the source (they are code-shaped, not instance state).
- Two browser clients dragging `/view3d` do not share a camera; the program's
  `canvas.camera` is the only shared camera, and it lives server-side.
