# Tool Visualizer — Phase Plan (V0–V6)

**Goal:** one command, `sk viz`, opens a browser page that shows *every* tool this host
knows about — what it is, whether it is advertised here and why, what it costs, what it
touches, how it relates to the others, and what happened the last time it ran.

**Status:** **V0 + V1 + V2 shipped** — `skeletonkey/viz/` (`model.py`, `render.py`,
`server.py`), `sk viz` in three modes (JSON model, standalone HTML catalog, served page
with drift detection), `schemas/viz-model.schema.json`, 60 tests across
`tests/test_viz_model.py`, `tests/test_viz_render.py` and `tests/test_viz_server.py`.
V3–V6 below are still plan.

## 0. Ground truth (measured on this box, `python -m skeletonkey.cli describe`)

| fact | value |
| --- | --- |
| tools registered | 81 |
| tools advertised | 79 (`digest 313dd1d3d3695bc1`, 7 293 tokens) |
| groups | integrations 17, fs 16, live 11, shell 11, publishing 9, registry 7, skills 5, planner 2, capabilities 1, policy 1, profile 1 |
| risk classes | none 30, write 30, read 17, destructive 4 |
| tiers | task 45, full 24, core 12 |
| distinct capabilities | 74 |
| `see_also` edges | 91 |
| worked examples | 46 |
| manifest fields per tool | 38 (`ToolSpec`) |

Those numbers are the visualizer's acceptance fixtures: the page must account for all
81, not just the 79 the host advertises — the 2 withheld ones are the interesting case.

## 1. Invariants the visualizer must not break

1. **Read-only projection.** The visualizer never mutates registry, journal or policy.
   It renders `engine.advertise()` and `registry.all()`; a "try it" affordance (V5) goes
   through the normal `engine.call` path with the normal policy gates, never around them.
2. **Same snapshot, same digest.** The page shows the `digest` it rendered. If the
   digest changes underneath (skill install, HMR reload, profile re-probe), the page
   says so rather than silently drifting. One source of truth — §2.1 of `PLAN.md`.
3. **Zero mandatory dependencies** (ADR-0001). Stdlib `http.server` + hand-written
   HTML/CSS/JS, exactly like `skeletonkey/live/panel.py`. No npm, no CDN at runtime;
   any layout maths ships as a vendored, reviewed `.js` under `skeletonkey/viz/static/`.
4. **Secrets never render.** Reuse `core/export.py`'s `_redact_schema` / `secret_args`
   stripping before anything reaches the DOM. A visualizer is an export surface.
5. **Offline and air-gapped by default.** Bind `127.0.0.1`, random port, `--host` to
   opt out, printed URL, no telemetry.

---

## V0 — Data layer: one JSON document that describes the whole toolset ✅ SHIPPED

*The visualizer is a view. Build the model first, headless and testable.*

**Deliverables**
- `skeletonkey/viz/model.py` — `build_viz_model(engine) -> VizModel` (dataclass →
  `to_json()`), assembled from `registry.all()` + `advertise()` + `profile` receipts.
- Per-tool node: `id, group, title, summary, capability, provider, tier, risk,
  destructive, reversible, idempotent, parallel_safe, open_world, stateful,
  typical_latency_ms, typical_output_bytes, timeout_s, approval, platforms, tags,
  source, advertised, hidden_reason, gate_receipt, token_cost, example_count`.
- Edges, four typed kinds: `see_also` (91 today), `same_capability` (74 buckets, the
  provider/priority contest), `provides→requires` (planner-derived), `anti_pattern`.
- Aggregates: group rollups, risk histogram, tier histogram, token budget breakdown
  (which tools ate the 7 293 tokens; which were dropped by `budget_drops`).
- `schemas/viz-model.schema.json` + validation in tests.
- `sk viz --json` prints the model and exits — CI-friendly, no server.

**Acceptance (as built):** `tests/test_viz_model.py` — every registered tool is a node
exactly once; `advertised` matches the snapshot tool-for-tool; every withheld tool names a
machine-readable cause (`gate | tier | internal | budget`) *and* prose, never `unknown`; no
`secret_args` key or credential-shaped string reaches the document (asserted against a
planted `ghp_…` fixture); byte-identical JSON across builds and a `model_digest` that
ignores the clock but notices a new tool; edges never dangle; building a model does not
mutate the registry; the model and `toolkit.describe()` agree exactly on counts, tokens and
digest. Counts are asserted as relationships, not hard numbers, so adding a tool does not
break the suite.

**What V0 actually produced** (measured here: 81 nodes, 79 advertised, 2 withheld — both
`internal`, 112 edges: 91 `see_also` + 11 `anti_pattern` + 10 `same_capability`):

```console
$ sk viz --json | jq .aggregates          # or: sk viz -o tools-model.json
$ sk viz --tier core --json               # 12 advertised, 69 withheld_by=tier
$ sk viz --read-only --no-schemas --json  # every mutating tool withheld, lean payload
```

---

## V1 — Static render: the catalog page ✅ SHIPPED

*Ship something useful before anything is interactive.*

**Deliverables**
- `skeletonkey/viz/render.py` — `render_html(model) -> str`, a single self-contained
  file (inlined CSS/JS, no network fetches).
- Layout: left rail = groups (11) with counts; main = card grid, one card per tool
  showing title, id, risk badge, tier badge, latency, capability, advertised/withheld
  chip; click = detail drawer with description, input/output schema (collapsible tree),
  examples, anti-patterns, `see_also` links, gate receipt.
- Filters: free text (id/title/description/tags), group, risk, tier, advertised-only,
  destructive-only, has-examples. All client-side over the embedded model.
- `sk viz --out tools.html` writes the standalone file — mailable, commitable, diffable.

**Acceptance (as built):** `tests/test_viz_render.py` — 18 tests. Every tool is exactly
one card (advertised or not, withheld ones dashed and labelled); card `data-*` filter
attributes match the model field for field; markup is well-formed and fully closed per
`html.parser`, not a regex; the page makes **no** network reference (no `src`, no `href`,
no `@import`, no `url(` in the chrome) so `file://` works offline; every id the behaviour
script looks up actually exists in the DOM; the embedded payload round-trips through
`json.loads`; a tool whose title/description/tags contain `</script><img src=x
onerror=alert(1)>` cannot break out of the block or become live markup; no credential-shaped
string reaches the page; rendering is deterministic; and `embed_model=False` still renders
the full catalog, which is the JS-disabled degradation path V3 will fall back to.

```console
$ sk viz -o tools.html --title "Ops toolset"   # 240 KB, one file, opens offline
$ sk viz -o model.json                          # extension picks the format
$ sk viz --json | jq .aggregates                # stdout stays machine-readable
```

---

## V2 — Serve it: `sk viz --serve` ✅ SHIPPED

**Deliverables**
- `skeletonkey/viz/server.py` — `ThreadingHTTPServer`, mirroring `live/panel.py`
  conventions (thread, `--port 0`, graceful shutdown, request counter).
  - `GET /` page · `GET /api/model` JSON · `GET /api/digest` cheap poll ·
    `GET /api/tool/{id}` detail · `GET /healthz`.
- Digest polling every 2 s; when it changes, a banner offers reload (and auto-reloads
  under `--watch`). This is how HMR (`live.*`, 11 tools) and `sk skills install` show up
  live.
- CLI: `sk viz [--port] [--host] [--open] [--json] [--out FILE] [--read-only-view]
  [--watch]`, registered next to `describe`/`export` in `cli.py`.
- Reachable from the existing live panel as a `/tools` route so one server can host both.

**Acceptance (as built):** `tests/test_viz_server.py` — 21 tests. Every route answered on
a port-0 loopback server; `/api/model` byte-matches the library call's digest; `/api/digest`
stays under 400 bytes; registering a tool while serving changes the digest and appears in
the page **without a restart** (the model is rebuilt per request under a lock, never
cached); start is idempotent and `stop()` leaves no live `sk-viz` thread and a rebindable
port; unknown routes 404 *with the route list*; a raised exception becomes a 500 and the
server keeps serving; 8 concurrent requests all succeed; serving never perturbs the
registry's tool list or digest.

**Read-only, structurally.** There is no `do_POST`/`do_PUT`/`do_DELETE`/`do_PATCH` on the
handler at all — asserted by test, not by convention — so V5's `--allow-calls` gate is
pre-decided in the negative. That is the right default for something an operator leaves
running. Loopback-only bind unless `--host` says otherwise.

```console
$ sk viz --serve                 # random free port on 127.0.0.1, prints the URL
$ sk viz --serve --port 8080 --open --poll-ms 1000
```

The served page is the V1 renderer with `live=True` — one renderer, not two. The drift
banner polls `/api/digest`, compares it to the digest it was rendered with, and offers a
reload when a skill install or an HMR swap changes the toolset underneath it; if the server
goes away it says that instead, once, after three misses.

---

## V3 — The graph view (the actual "visualizer")

**Deliverables**
- Force-directed / grouped layout over the four edge kinds, vendored layout code, SVG
  render, ≤ 200 nodes so plain SVG is fine (81 today, headroom for skills + packs).
- Node encoding: colour = group, ring = risk, size = token cost, dashed outline =
  withheld here, icon = destructive / stateful / open-world.
- Edge toggles per kind; hover highlights neighbourhood; click focuses and opens the
  drawer from V1; "capability contest" mode collapses the 74 capability buckets and
  shows provider priority ordering with the loser greyed.
- Alternative views, same model, tab-switched:
  - **Treemap** — token budget: who spends the 7 293 tokens.
  - **Matrix** — group × risk, and capability × provider.
  - **Timeline** — `typical_latency_ms` distribution, log scale.
- Keyboard: `/` search, `j/k` walk, `Esc` close. Prefers-reduced-motion honoured.

**Acceptance:** deterministic layout under a seeded RNG (snapshot test on node
positions); no node overlaps beyond a tolerance; graph renders with JS-disabled
fallback = the V1 card grid.

---

## V4 — Why-not: the gating explainer

*The most valuable question about a tool is why you cannot call it.*

**Deliverables**
- Per-tool "why is this (not) here" panel: profile probe receipts for each
  `Requirement` / `require_any`, `read_only` withholding, `tools.disable` config hits,
  `advertised=False`, provider de-dup loss, budget drop — each with the receipt object
  that decided it, rendered as a pass/fail chain.
- Host switcher: render the model *as if* on another platform (`windows`, `linux`,
  `darwin`) or another tier/read-only mode, so you can see what a client would get.
  Pure function of the model + hypothetical profile — no re-probing.
- Diff mode: `sk viz --diff other-model.json` shows added/removed/changed tools between
  two snapshots (two hosts, two commits, before/after a skill install).

**Acceptance:** for the 2 currently-withheld tools the chain names the exact deciding
rule; property test — a tool is advertised in the model iff the explainer chain passes.

---

## V5 — Runtime overlay: what actually happens

**Deliverables**
- Read the ledger (`core/ledger.py`, redacted via `core/redact.py`): per-tool call
  count, p50/p95 latency vs. declared `typical_latency_ms`, error rate, bytes returned
  vs. `typical_output_bytes`, last `run_id`.
- Overlay heat on the graph; flag **contract drift** (measured p95 > 3× declared) as an
  explicit list — this turns the visualizer into a manifest-accuracy linter.
- Planner overlay: render a `planner.*` DAG onto the same graph, animate a replayed run
  (`core/replay.py`) step by step.
- Optional "call this tool" form generated from `input_schema`, behind
  `--allow-calls` (default off), routed through `engine.call` with policy intact and
  destructive tools requiring a typed confirmation.

**Acceptance:** overlay is absent and the page still works when no ledger exists;
redaction test — no credential-shaped string reaches the DOM; `--allow-calls` off means
no mutating route exists at all (404, not merely hidden).

---

## V6 — Ship it

**Deliverables**
- `docs/TOOL-VISUALIZER.md`: routes, model schema, screenshots, embedding guide.
- ADR: *"the visualizer is a projection of `advertise()`, never a second registry."*
- `sk export --format viz-html` so the standalone page joins the export family
  (openai-tools / anthropic-tools / mcp-manifest).
- MCP resource `skeletonkey://viz/model` so a host agent can read the same model.
- `sk doctor` gains a viz check (model builds, node count == registry count).
- README section + a committed `examples/tools.html` regenerated in CI, so the diff of
  the toolset is reviewable in pull requests.

---

## Sequencing, risk, and effort

| phase | depends on | effort | risk | value if you stop here |
| --- | --- | --- | --- | --- |
| V0 model | — | S | low | machine-readable full-toolset document |
| V1 static page | V0 | M | low | shareable catalog of all 81 tools |
| V2 server/CLI | V1 | S | low | `sk viz` in the loop, live digest |
| V3 graph | V0 | L | med (layout, no deps) | the actual visualizer |
| V4 why-not | V0 | M | med (receipt coverage) | debugging tool for gating |
| V5 runtime | V2, ledger | M | med (redaction, perf) | manifest-accuracy linter |
| V6 ship | all | S | low | it exists for other people |

**Recommended cut line:** V0→V2 is the minimum that pays for itself and is ~2 days of
work; V3 is the headline; V4 is the one long-time users will actually miss; V5 is where
it becomes a diagnostic instead of a diagram.

**Known risks**
- *Zero-dep layout.* Writing a force layout by hand is the single largest unknown in
  V3. Mitigation: start with a deterministic grouped/radial layout (no simulation) and
  only add force relaxation if it reads badly.
- *Receipt coverage in V4.* Some gates may not currently emit a receipt object; V4 may
  require small, additive changes in `core/registry.py`. Keep them additive and covered.
- *Snapshot drift.* Tests that hard-code 81/79/7 293 will fail on every tool addition —
  assert relationships (registered ≥ advertised, every withheld has a reason), and keep
  the exact counts in one fixture file.
