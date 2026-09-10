# ADR 0016: The visualizer is a projection of `advertise()`, never a second registry

Date: 2026-09-09
Status: accepted
Affects: `skeletonkey/viz/` (model, render, server, layout, views, explain,
runtime), `schemas/viz-model.schema.json`, `skeletonkey/cli.py` (`sk viz`,
`sk export viz-html`), `skeletonkey/core/export.py`, `skeletonkey/mcp/adapter.py`
(`skeletonkey://viz/model`), `skeletonkey/diagnostics.py` (the `viz` check),
`docs/TOOL-VISUALIZER.md`, `docs/TOOL-VISUALIZER-PLAN.md`,
`tests/test_viz_*.py`

## Context

The toolkit advertises 79 of 81 registered tools on this host, across 11 groups
and 74 capabilities, for 7 293 tokens. Three questions were expensive to answer
and got asked constantly:

1. *What can this thing actually do?* — answerable only by reading
   `builtin.py` or paging through `registry.list`.
2. *Why can I not call `X` here?* — `capabilities.explain` answers it one
   capability at a time, if you already suspect which one to ask about, and
   `registry.explain_all` dumps every gate as rows.
3. *Is a manifest still telling the truth?* — nothing answered this at all.
   `typical_latency_ms` is a planner input, and nothing ever checked it against
   what the tool did.

A visualizer answers all three. The danger in building one is that it is
*so much easier* to give it its own model of the toolset — a second list of
tools, its own idea of what is available, its own gating logic — and that model
will drift from the registry within a release. A diagram that disagrees with
the engine is worse than no diagram: it is a confident lie.

## Decision

**The visualizer is a pure, read-only projection of `registry.all()` and
`registry.advertise()`. It holds no facts of its own.**

Five consequences, each enforced by a test rather than a convention:

1. **One model, many surfaces.** `build_viz_model()` is the only thing that
   reads the registry. The JSON document, the standalone page, the served page,
   the graph, the MCP resource and the export all render *that*, so they cannot
   disagree. `test_matches_describe` cross-checks the model against
   `toolkit.describe()` on counts, tokens and digest.

2. **Every registered tool is in the model, advertised or not.** The withheld
   ones are the interesting ones. Each carries the advertise-time gate receipt
   and a one-word cause (`gate | tier | internal | budget`).

3. **The explainer mirrors the registry, and the mirror is pinned.** V4's
   10-step chain reproduces `gate()`/`advertise()` in order, which is a
   liability — two implementations of one rule drift silently. The invariant
   that makes it safe is asserted for every tool under every projection:

   > a tool is advertised **if and only if** its chain has no failing step.

   If the registry's gating changes and `explain.py` does not, that test fails.

4. **Read-only, structurally.** The server has no `do_POST`/`PUT`/`DELETE`/
   `PATCH` — not "disabled by a flag", *absent* — so there is no mutating route
   to find. Building a model, serving it, or attaching the runtime overlay
   leaves the registry's digest and the ledger's bytes unchanged.

5. **Zero dependencies** (ADR-0001). Layout maths, SVG, HTML, CSS, JS and the
   HTTP server are stdlib and hand-written. No npm, no CDN, no vendored force
   library — which also means the page opens from `file://` with the network
   off, and the layout is deterministic enough to snapshot-test as arithmetic.

## Options considered

1. **Ship a JSON dump and let people use an external graph tool.** Cheapest,
   and rejected: the interesting facts (why a tool is withheld, which provider
   won a contest, whether a manifest is honest) are exactly the ones a generic
   viewer cannot render, and it would put an npm toolchain between an operator
   and an answer.

2. **A JS single-page app fed by the API.** Rejected on ADR-0001 and on
   degradation: a build step for a diagnostic is a diagnostic that breaks when
   the build breaks. Instead all six views are server-rendered markup and
   script only toggles which is visible, so a browser with JS off still gets
   the complete catalog.

3. **Cache the model and invalidate on registry events.** Rejected: the model
   is a pure function of the registry and costs milliseconds, while a served
   page that can go stale is worse than no server. It is rebuilt per request,
   and the page polls a digest so it *notices* when the toolset moves under it.

4. **Let the visualizer call tools ("try it" buttons).** Deferred, deliberately.
   The V5 plan floated `--allow-calls`; it was not built. A tool-runner deserves
   an explicit decision of its own rather than arriving inside a diagnostic
   view, and read-only-by-absence is the safer default for something an operator
   leaves running.

## Consequences

**Good.** One digest to trust. Adding a tool needs no visualizer change. The
drift linter (`sk viz --drift`, non-zero exit) makes manifest accuracy a CI
gate, which no amount of documentation would have achieved. `sk doctor` now
fails if the model stops covering every registered tool.

**Costs.** The explainer duplicates gating logic and must be updated in lockstep
with `Registry.gate()` — accepted because the IFF test makes the drift loud
rather than silent. Layout is hand-written, so exotic topologies may read worse
than a mature force-directed library would render them. Rebuilding per request
is wasted work on a large registry; at 81 tools it is milliseconds, and the
constant is documented here so a future maintainer can add a digest-keyed cache
deliberately instead of by reflex.

**Deliberately not decided here.** Whether the page should ever execute a tool
(option 4). If that changes, it needs its own ADR — the read-only guarantee in
this one is load-bearing for leaving `sk viz --serve` running.
