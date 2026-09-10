# Roadmap: V10–V16

Everything left over from the visualizer session, plus the adoption and
integration work that came out of it. Written in the same shape as
`TOOL-VISUALIZER-PLAN.md`: each phase is independently shippable, has explicit
acceptance criteria, and states what it deliberately does *not* do.

Status legend: 🔴 not started · 🟡 in progress · ✅ shipped

| Phase | What | Status |
|---|---|---|
| V9.5 | Performance: the O(n²) explain | ✅ shipped |
| V10 | Make the suite green (`[mcp]` extra) | 🔴 |
| V11 | Adoption: accept the tools people already have | 🔴 |
| V12 | Live invalidation: HMR → MCP → catalog | 🔴 |
| V13 | `viz.explain` as a tool an agent can call | 🔴 |
| V14 | CI recipe: the toolset snapshot as a gate | 🔴 |
| V15 | Extract `skeletonkey-viz` | 🔴 |
| V16 | The 3D viewport, only if earned | 🔴 |

---

## V9.5 — Performance ✅ SHIPPED

Profiled before optimising. `explain_model` called `registry.advertise()` once
per tool: 395 snapshots for one page, recomputing `tokens_estimate()` 30,810
times.

Two fixes:

1. `explain_model` computes one `advertise()` snapshot and shares it via a
   private `_snap` argument. It is a pure function of the projection
   arguments, so every tool was recomputing the identical object.
2. `tokens_estimate()` memoises against its own derived doc string.
   `toolkit.py` rewrites `description` on handler-less skill manifests after
   registration, so a blind cache would go stale and corrupt every budget
   decision — keying on the text makes staleness impossible.

| | before | after |
|---|---|---|
| `build_viz_model` | 3.2 ms | 1.9 ms |
| `explain_model` | 86.1 ms | **0.9 ms** |
| full page + chains | 155.0 ms | **25.2 ms** |

The `tokens_estimate` win also lands on `advertise()` itself, so every MCP
`tools/list` and every budget pass gets faster, not just the visualizer.

Guarded by tests that **count `advertise()` calls rather than measure time**,
so they stay deterministic on a noisy CI box, plus an equivalence test that
the shared snapshot produces byte-identical chains under every projection.

**Not done, deliberately:** caching the rendered page. The server rebuilds per
request on purpose (ADR-0016) and 25 ms does not justify a staleness bug.

---

## V10 — Make the suite green 🔴

**The one that blocks everything else.** 27 failed + 17 errors, all from the
uninstallable `[mcp]` extra. A permanently red suite trains everyone to ignore
it, and it already cost this session real coverage: the
`skeletonkey://viz/model` resource is asserted against *source text* because
it cannot be exercised.

Tasks:
1. Determine why `mcp>=2.1` will not install here — pinned range, Python
   version, or network. Fix or widen the pin.
2. If it genuinely cannot be installed in some environments, make the affected
   tests `skipif` on the import rather than fail. A skip is honest; a failure
   is noise.
3. Convert the source-text assertions in `test_viz_ship.py` to real over-the-
   wire tests.
4. Add a CI job that runs both with and without the extra.

**Acceptance:** `pytest -m "not slow"` exits 0. `test_viz_ship.py` exercises
the MCP resource through a real client. CI proves both install modes.

**Do not:** paper over it by deleting tests.

---

## V11 — Accept the tools people already have 🔴

The measured adoption barrier. An outsider tries the obvious thing and gets:

```
build_viz_model(my_tool_dicts)
→ AttributeError: 'list' object has no attribute 'all'
```

The real cost is low once you know `ToolManifest` needs only `id` of its 38
fields — a foreign toolset renders in ~15 lines. **So this is a discovery
problem, not an architecture problem**, which makes it the cheapest high-value
work available.

Tasks:
1. `viz/adapt.py`: `as_registry(obj)` accepting a list of dicts, OpenAI
   function schemas, an MCP `tools/list` response, or anything with `.all()`.
   Infer `group` from a dotted id, default risk conservatively.
2. `build_viz_model` calls it, so the naive call works.
3. `sk viz --from tools.json` — render any tool list without installing the
   toolkit into your project. This is the realistic on-ramp: people see the
   page first and adopt the framework second.
4. `docs/VISUALIZE-YOUR-TOOLS.md`: a 15-line quickstart for a project that has
   never heard of SkeletonKey.

**Acceptance:** the exact failing snippet above works. A copied OpenAI
function array renders. Round-trip test: adapted model ≡ hand-built manifests.

**Do not:** guess at risk or destructiveness from tool names. Unknown means
unknown, and the page must say so rather than invent a reassuring answer.

---

## V12 — Live invalidation 🔴

A real correctness hole found by reading, not by a failing test:
`notify_tools_changed` exists and `skills/watch.py` calls it, but **nothing in
`live/` does**. Hot-reload a program that registers tools and connected MCP
clients never learn.

Tasks:
1. Wire the live watcher to `notify_tools_changed` when a reload changes the
   registry generation.
2. The mounted catalog subscribes to the panel's existing frame condition
   variable, so HMR reload or skill install refreshes the page without a
   manual reload. The plumbing is already there for SSE.
3. Debounce: one notification per settled change, not one per file event.

**Acceptance:** a reload that adds a tool produces exactly one
`tools/list_changed`; one that changes nothing produces none (the existing
digest check already guards this). The open catalog updates within one poll.

---

## V13 — `viz.explain` as a callable tool 🔴

The visualizer can already answer "why can't I see `fs.delete`?" in ten
gated steps. Today only a human can ask. Exposing it as a *tool* lets an agent
introspect its own capabilities — genuinely novel, and ~30 lines on top of V4.

Tasks:
1. `viz.explain` (read, non-mutating): tool id in, decision chain out.
2. `viz.model`: the aggregate projection, for an agent reasoning about its own
   budget.
3. Both withheld from `core` tier — this is introspection, not core work.

**Acceptance:** over MCP, asking about a withheld tool returns the chain with
the failing step named. Property test: the IFF invariant (advertised ⟺ no
`fail` step) holds through the tool surface too.

**Watch for:** recursion. `viz.explain` explaining `viz.explain` must
terminate, and there should be a test that says so.

---

## V14 — The snapshot as a CI gate 🔴

`docs/TOOLSET.txt` is the most transferable idea in the repo and currently
only this repo benefits.

Tasks:
1. `sk viz --summary --check` → exit 1 with a diff when stale. One command, no
   pytest dependency, usable by any project.
2. A ready-to-copy GitHub Actions snippet in the docs.
3. **State the limitation in the doc:** the snapshot catches tools added, risk
   classes changed, exposure flipped. It does *not* catch a tool whose
   behaviour changed while its manifest stayed identical. Someone will
   otherwise read a green diff as proof of safety.

**Acceptance:** `--check` exits non-zero on a stale file and prints a readable
diff; exits 0 when current.

---

## V15 — Extract `skeletonkey-viz` 🔴

`viz/` imports exactly four things from core (`manifest`, `util`, `redact`,
two `export` helpers). It is nearly extractable already, and it is the part of
this repo with the strongest standalone appeal: a dependency-free "point it at
your tools, get a page" library will be adopted by projects that would never
take on a whole agent framework.

Tasks:
1. Vendor or narrow the four imports behind a tiny internal shim.
2. Split into `packages/skeletonkey-viz` with its own `pyproject.toml`, zero
   dependencies, its own test run.
3. The main package depends on it and re-exports, so nothing downstream
   changes.

**Acceptance:** `pip install skeletonkey-viz` alone renders a page from a list
of dicts. `test_core_purity.py` still passes. No import from the parent
package remains.

**Decide, do not drift:** if `viz/` keeps reaching into core, this gets harder
every week. Either commit to the boundary now or close the idea — a
half-extracted package is worse than either end state.

---

## V16 — The 3D viewport, only if earned 🔴

`/scene.json` is already documented as the react-three-fiber hook and is
untouched. Worth doing **only** if the orthographic SVG projection is actually
limiting real work.

It would be the first mandatory-dependency pressure on ADR-0001, so it needs a
concrete need first, and a new ADR if it lands. Listed here so it is tracked
rather than forgotten — not because it should be built next.

---

## Sequencing

**V10 first, and alone.** Adding features onto a red suite is how red becomes
permanent.

Then **V11 + V14** together — both small, both convert this from an internal
tool into something other projects use, and neither depends on the other.

Then **V12** (a real correctness hole) and **V13** (the novel capability).

**V15** is the strategic bet: highest ceiling, and the one that expires if it
is not decided. **V16** waits for evidence.
