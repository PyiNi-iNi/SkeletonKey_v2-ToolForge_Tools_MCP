# Roadmap: V10–V16

Everything left over from the visualizer session, plus the adoption and
integration work that came out of it. Written in the same shape as
`TOOL-VISUALIZER-PLAN.md`: each phase is independently shippable, has explicit
acceptance criteria, and states what it deliberately does *not* do.

Status legend: 🔴 not started · 🟡 in progress · ✅ shipped

| Phase | What | Status |
|---|---|---|
| V9.5 | Performance: the O(n²) explain | ✅ shipped |
| V10 | Make the suite green (`[mcp]` extra) | ✅ shipped |
| V11 | Adoption: accept the tools people already have | ✅ |
| V12 | Live invalidation: HMR → MCP → catalog | ✅ (premise corrected) |
| V13 | `viz.explain` as a tool an agent can call | 🔴 |
| V14 | CI recipe: the toolset snapshot as a gate | ✅ |
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

## V10 — Make the suite green ✅ SHIPPED

**The finding: there was never a bug.** The "known" 27 failed + 17 errors were
an unprovisioned sandbox, not broken code. `mcp 2.2.0` installs fine against
the existing `mcp>=2.1,<3` pin; it had simply never been installed here. Three
environment facts, all invisible without CI:

| Symptom | Real cause |
|---|---|
| ~40 MCP failures | `[mcp]` extra never installed |
| 3 `test_p7_*` failures | package not installed editable, so no `sk` on PATH |
| `Unknown config option: asyncio_mode` | `pytest-asyncio` absent |

With `pip install -e ".[mcp,watch,dev]"`: **1160 passed, 0 failed.**

That is a one-line fix that would have rotted again by next week, so the
actual work was making it impossible to sustain:

1. **CI, which did not exist at all** (`.github/workflows/ci.yml`) — four
   jobs: the full suite on 3.11 and 3.12; a `core-purity` job that installs
   the core alone and fails if *any* third-party package appears, turning
   ADR-0001 from a claim into a check; a no-extras job proving the suite is
   green with MCP tests skipped; and a staleness gate on `docs/TOOLSET.txt`.
2. **Skips instead of failures.** `tests/_extras.py` exports `HAS_MCP` /
   `NO_MCP_REASON`; the four affected modules skip cleanly. Without the extra:
   **1106 passed, 48 skipped, 0 failed.**
3. **`tests/test_optional_extras.py`** guards the discipline itself — the
   probe matches a real import, every MCP-dependent module carries a
   module-level skip, no test imports `mcp` unguarded at module scope (which
   would explode collection instead of skipping), and the core genuinely
   builds a full registry with `mcp` and `watchfiles` blocked.
4. **`CONTRIBUTING.md`** with the symptom→cause→fix table, so the next person
   diagnoses this in seconds rather than assuming red is normal.
5. `pytest-asyncio` moved to a `[test-mcp]` extra, since only the MCP tests
   are async.

**Lesson worth keeping:** a suite that is "always a bit red" stops being read.
The 27 failures were treated as a known baseline for this entire session —
including by me — and they were never real. Anything genuinely expected to
fail should `skip` with a reason.

## V11 — Accept the tools people already have ✅

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

### Shipped

`skeletonkey/viz/adapt.py` — `to_manifests()` / `as_registry()`, accepting
OpenAI and Anthropic function schemas (including the `{"type": "function"}`
wrapper), MCP `tools/list` and its `{"tools": [...]}` envelope, `{id: {...}}`
mappings, bare `ToolManifest`s, and a real `Registry` (passed through).
`build_viz_model` calls it unconditionally, so the failing snippet works.
`sk viz --from FILE`, and `docs/VISUALIZE-YOUR-TOOLS.md`.

Two decisions worth recording:

- **It converts into a real `Registry`, not a duck-typed stub.** Adapted tools
  go through identical gate evaluation, dedupe and budget logic, so `explain`
  and `diff` work on them. The alternative — a lightweight shim — would have
  been a second, weaker code path that drifts from the real one and quietly
  renders different answers.
- **The no-handler invariant is preserved** by injecting a handler that
  *raises*. An adapted tool is documentation; a no-op handler would let a
  caller believe something ran.

The "do not guess" rule needed enforcing, not just stating. The first version
of the test asserted only `risk_declared is False`, which a name-sniffing
adapter passes happily — it can set `risk="destructive"` *and* flag it
undeclared. The test now asserts an undeclared tool's risk does not vary with
its name (`db.delete_all` must land where `docs.read_page` lands), which is
the property that actually matters. Verified by mutation: the weak assertion
survived the guessing adapter, the strong one kills it.

---

## V12 — Live invalidation ✅ (premise corrected)

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

### What actually shipped — and why it is not what this section describes

I wrote this phase from reading the code. Three of its four claims did not
survive being executed. Measured on a real toolkit:

| Claim in this plan | Measured |
|---|---|
| `live/` reloads change the registry, clients never learn | **False.** A live reload does not touch the toolkit registry at all — generation stays put across `live.start` and `live.reload`. `live/` patches a *program's* namespace; it never registers tools. |
| The mounted catalog needs wiring to the frame condvar | **Already works.** `VizMount` rebuilds from the registry per request and the page polls `/api/digest`; `integrations.load` moved it 81→91 tools with no changes. |
| Debounce needed | **Already there**, in `FileWatcher._deliver`. |
| Nothing notifies on registry change | **Mostly false.** Every MCP tool call already calls `notify_tools_changed` afterwards, so `integrations.load` (+10 tools) does notify. |

So the "real correctness hole found by reading" was not real. Reading found a
plausible story; running it found the story was wrong. Worth remembering: this
plan asserted a bug confidently enough that I nearly built the fix before
checking whether the bug existed.

**But the probe found a different hole, which is real.** `McpBridge` was
written when "one server, one client" was true, and `--transport
streamable-http` falsified that without the bookkeeping being updated:

- `_digest` was a single shared value. The first client notified advanced it,
  so every *other* connected client was then told nothing — the digest already
  matched — and kept serving a stale `tools/list` indefinitely.
- `session` was a single slot overwritten by whichever client called last, so
  a notification could go to the wrong client.
- One dead client raised, and the exception ended the (point-to-point) send.

Fixed: `bridge.sessions` tracks every connected client, `notify_tools_changed`
fans out to all of them, a failed send drops that client without aborting the
fan-out, and the digest advances once per settled change so repeated calls
still cost exactly one notification each. `tests/test_mcp_multisession.py`
(9 tests) pins it; verified by mutation — reverting the fan-out to
point-to-point fails two of them.

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

## V14 — The snapshot as a CI gate ✅

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

### Shipped

`sk viz --summary --check -o FILE`: unified diff on stdout, the regenerate
command on stderr, exit 1. It never writes the file — a gate that silently
fixes what it is gating is not a gate, and there is a test asserting the file
is byte-identical after a failed check. The GH Actions snippet and the
limitation ("this does not catch behaviour changes behind an unchanged
manifest") are in `docs/VISUALIZE-YOUR-TOOLS.md`.

One consistency fix fell out of this: `--summary` printed `read` for tools
that had never declared a risk, while the HTML page said "risk not declared".
Same model, opposite conclusions depending on which surface you looked at. The
summary now prints `undeclared`, and a test pins the two together.

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

Then **V12** (billed as a real correctness hole - the hole turned out to be
elsewhere; see the phase notes) and **V13** (the novel capability).

**V15** is the strategic bet: highest ceiling, and the one that expires if it
is not decided. **V16** waits for evidence.
