# HANDOFF — SkeletonKey / ToolForge v2 (P5a shipped → P5b)

Session `arena/01a05a44-skeletonkey-v2-toolforge-tools` · 2026-09-01.
Written by the agent that shipped **P5a** (tiers, routing, receipts), for the session that
starts **P5b** (semantic stage, MCP client aggregation). Read `PLAN.md` for the roadmap and
`docs/` for the contracts; this file is the *transfer* — state, next steps, ideas, and
landmines. The P3–P4b handoff is superseded by this one; its standing constraints are
carried forward in §7.

**Agent / model provenance.** The harness is **Arena.ai Agent Mode** (repo-cloned sandbox,
bash + file tools, auto-saved turns). It is not attributable to a single base model: Arena's
Agent Mode draws on many (Claude, ChatGPT, Gemini, Grok, Qwen, Kimi, …), and no specific one
was recorded or should be assumed. Everything below was measured against the tree at handoff,
not remembered: re-measure before you restate it.

---

## 1. State on handoff

| | |
| --- | --- |
| Branch | `arena/01a05a44-skeletonkey-v2-toolforge-tools` — **push only here** |
| HEAD | `f2a5641` (pushed). P5a commits: `873de6f` (spec), `4e9a1c3` (core), `7dc1e19` (tools+wiring), `2d43528` (docs), `f2a5641` (skills) |
| main | still at `6ad120e` — P5a is on the arena branch, **not merged**; review + merge is yours (see §3.4) |
| Test suite | **630 passed, 3 skipped, 1 xfailed** in ~51 s (`pytest -q` exit 0). Before P5a it was 610 passed |
| Ruff | `ruff check .` clean |
| Venv | `.venv` at repo root: mcp 2.1.1, pytest, ruff, pyyaml; **no watchfiles** (its absence is the tested state) |
| Registered tools | **50** (repo-root build) |
| Advertised (default `full`) | **48** tools, **4752** tokens, digest `41da93e9b81b0d1d` |
| Per-tier | core **11**/945 tokens (`94f7da59a9f9937a`), task **38**/3482 (`34cd2f31d4af4484`), full **48**/4752 |
| Gated (registered, not advertised) | `shell.selftest` (skill declares `advertised = false`), `skills.install` (`skills.allow_install` off) |
| Route hit-rate | 25/25 (1.000) at k=5 over `tests/eval/suite.jsonl`; semantic on/off identical (no backend installed — the only shipped state) |
| Wire tests | 3 P5a tests in `tests/test_mcp_stdio.py` green (expand → `list_changed`, cursor round-trip, route/explain reasons) |
| Docs | `docs/TOOL-CONTRACT.md` §7e (author-facing P5a contract), `skills/fs-safe-refactor/references/discovery.md`, README updated |

Note on the two surface numbers: repo-root `tk.build()` = 50 registered / 48 advertised
(what README says). The MCP wire server started with `--cwd` in a bare temp dir finds no
skills dir, so it sees **48 registered / 47 advertised** — both are honest, just different
workspaces. Don't "fix" one to match the other.

## 2. What P5a actually is

- **Tiers are a manifestation filter, never authorization.** `tier` on every manifest
  (`core`/`task`/`full`, default `full`); `registry.advertise(tier=…)` + `[advertise]`
  budgets per tier (0 = no cap); `registry.active_tier` session state switched by
  `registry.expand {tier}`; the digest changes with the set, so `tools/list_changed` fires.
  A tier-hidden tool still works when called by id.
- **Two-stage router.** `registry.route {task, k, semantic}`: exact-id fast path always
  wins → deterministic lexical ranking → optional `SemanticBackend` protocol
  (`core/semantic.py`, entry-point group `skeletonkey.semantic`). Every hit carries
  `reasons` (field × token, capped 6) + `tier`/`provider`. No backend + `semantic=true` ⇒
  identical to lexical, `mode: "lexical"`, honest `note`. The engine, adapter `INSTRUCTIONS`,
  CLI (`sk tools route/expand`), `toolkit.plan()` and `registry.search` all route through it.
- **Provider receipts.** `selection_receipts` (capability → winner, provider, score, why,
  competitors incl. `no call evidence`), `budget_drops` (greedy by ascending score, honest),
  surfaced in `registry.list` rows, MCP `tools/list` `_meta`, `registry.describe`, and the
  new `capabilities.explain {capability}` tool (unknown capability ⇒ `BAD_ARGS` + `near`,
  never silence).
- **Cursor pagination.** `registry.list` (`cursor`/`page_size`/`next_cursor`) and MCP
  `tools/list` (`cursor` → `nextCursor`); opaque positional, page 100, malformed cursor
  falls back to page 0, no-cursor callers get the whole small surface (backward compatible).
- **ACs satisfied by P5a tests** (`tests/test_discovery.py`, 17 tests): 200-tool core tier
  ≤20 tools/≤1.2k tokens + eval hit-rate ≥0.9; semantic-off property over the eval suite;
  expand round-trip + no-notify-when-unchanged; provider-receipt honesty.

## 3. What is NOT done (and why)

1. **Semantic stage has no backend.** `core/semantic.py` is a protocol + entry-point
   discovery only, by design: the model/dependency choice was explicitly deferred ("that is
   an ADR, not a surprise"). P5b must pick it (see §4.3) — zero-mandatory-deps (ADR-0001)
   means it lands as an optional extra either way.
2. **`mcp.client` connector (multi-server aggregation) is not started.** The original
   AC4/5: `remote.<server>.<tool>` pass-through with inherited `risk`, `reversible: false`,
   `stateful: "host"`, remote error codes passed through un-wrapped, `registry.stats`
   keeping remote/local rows separate.
3. **`fs.search` provider-fallback honesty is not asserted.** `rg`-absent →
   `metrics.provider == "python"` + `warnings` naming the fallback. Behavior is P1/P2; the
   executable assertion is the leftover.
4. **P5a is not merged to main** and **`ci.yml` is still untracked** (same GitHub App
   blocker: no `workflows` permission — any push touching `.github/workflows/` is rejected
   by design, don't retry). `ci.yml` content is at `.github/workflows/ci.yml` (untracked);
   unblock options unchanged: grant the App `workflows`, have the user push it, or leave it.
5. `registry.list` on the **wire** doesn't yet expose `next_cursor` semantics for
   `registry.list` itself beyond the tool-level test; MCP `tools/list` pagination is the
   wire-tested path. (Check `test_discovery.py::test_list_pagination` before assuming.)

## 4. Next steps (in order)

1. **Merge P5a to main** (review first; `gh pr merge` from this branch — it's pushed and
   green locally).
2. **`fs.search` fallback assertion** (smallest P5b piece, P1/P2 behavior exists): fake `rg`
   absent (PATH shadowing in the test), assert `metrics.provider == "python"` and a
   `warnings` entry naming the fallback. Wire-level or engine-level both fine; check
   `fsx/search.py` for the actual provider plumbing first.
3. **`mcp.client` connector** (the big one, exploratory per PLAN): decide config surface
   (`[mcp.remotes]`? — check `config.py` naming for `CONFIG_RE` compat in
   `tests/test_docs.py`), implement the client, per-tool pass-through with
   risk/`reversible`/`stateful` mapping, remote envelope error passthrough, stats rows
   separated by source. New tools must ship: TOOL-CONTRACT section, skill guidance, wire
   test (house rule).
4. **Semantic backend ADR + extra.** First write the ADR (model/dependency choice:
   pure-python deterministic TF-IDF/char-ngram is zero-dep but weak; `fastembed`
   (onnxruntime) or `sentence-transformers` (torch) are real embeddings but heavy — weigh
   Windows first-class + offline + eval evidence). Then `semantic.*` extra package,
   entry-point registration, `tools.semantic = true`, eval proof that reordering changes no
   *outcomes* (AC2 becomes a real two-stage comparison).
5. Optional quick wins still open: publish task in `tests/eval/suite.jsonl`; store
   `expiry`; Windows NT chmod 0600 test.

## 5. Ideas (honest, prioritized — none are decided)

- `registry.route` result could feed the next prompt directly (compact "tool shortlist"
  block) — the shape already carries `reasons`; decide in P6 UX if at all.
- `capabilities.explain` is currently tool/capability-scoped; a `registry.explain_all`
  (gates + receipts for the whole surface in one call) would make the 200-tool world
  debuggable from a prompt. Same receipt data, just a projection.
- Multi-server: decide whether remote tools participate in per-tier budgets (they should
  count against the same caps, not bypass them).
- Do **not** build `pub.run_plan` (a loop concern, not a tool) — recorded again because it
  keeps coming back.

## 6. Suggestions for the next session

- **House rule, unchanged:** every new tool ships a TOOL-CONTRACT section (or extension), a
  skill-guidance entry an agent will read, and a **wire-level** test. "A feature that only
  works when called from Python is not done."
- **Spec-first:** write the PLAN.md section before code; commit in 3-ish chunks
  (core+data / tools+wiring / docs+skills) with **explicit `git add <paths>`** — `git
  commit .` sweeps in untracked files and has bitten twice. `.github/` stays untracked.
- **Push early and often:** the sandbox recycles and wipes local state; the remote is the
  only durable record.
- **Measure, don't remember:** token counts, digests, test counts, and SHAs in this doc were
  re-measured at handoff; re-measure before citing. pytest 9.1.1 suppresses the "N passed"
  summary when piped — use `-rA`/grep PASSED or trust exit code.
- P5b's semantic backend choice involves a real dependency decision — weigh it against
  ADR-0001 (zero mandatory deps) and Windows first-class before committing; that's what the
  ADR is for.

## 7. Standing constraints (carried from the original handoff §9, reaffirmed)

- **Licensing/identity frozen:** Apache-2.0, authorship stays "Dime", README title line +
  "Dime's Custom Toolkit" tagline. No relicense/retitle/author-tidying without the owner.
- **Python 3.11+, zero mandatory dependencies.** Core must import with nothing installed
  (ADR-0001); extras (`mcp`, `watch`, `dev`, `all`) carry the rest. `core-constraint` CI job
  exists in the (unlanded) ci.yml.
- **Windows + Linux + macOS first-class;** PowerShell is not optional. Every PowerShell
  claim backed by a rendered-payload assertion or a `win`-tagged test that self-skips off
  Windows.
- **Primary consumer is the bespoke autopilot loop;** the MCP surface ships and stays
  honest. No silent reordering: rankings and gates always readable as receipts.
- Don't `pip install watchfiles`; no `python -m skeletonkey` (it is `skeletonkey.mcp` /
  `sk`).

## 8. Landmines (measured this session; the old ones still bite)

- **mcp 2.1.1 lowlevel:** `tools/list` handler params model is **`PaginatedRequestParams`
  directly** — the SDK does *not* wrap it in `ListToolsRequest`. Registering with
  `ListToolsRequest` makes `params` a `ListToolsRequest` whose `.params` is `None`, so the
  cursor silently never arrives. Same for response: put meta on the result model, and over
  the wire it serializes as `_meta` (not `meta`). Check `types.ListToolsResult` attrs from
  `mcp_types` before using them.
- `SkeletonKeyError.err.code` is the **string** `"BAD_ARGS"`, not the `E.BAD_ARGS` enum
  object — compare with `== "BAD_ARGS"`.
- `registry.all()` returns manifests (not dicts); `registry.all()` is a method;
  `AdSnapshot` has `.tokens` and `.digest`, **not** `.tokens_estimate`.
- Skill inject cap: `skills/fs-safe-refactor/SKILL.md` sits at 3995 tokens; body +
  `when_to_use` are both counted. Move detail to `references/` (they aren't counted).
- `E` namespace class; engine `_ledger` swallows exceptions (assert row presence); legacy
  `deny: ["**"]` is a tool glob; path denies need `tool(**/glob)`; `fs.glob` dotfile
  behavior; `ReadResult.sha256` is 16 chars; `req.meta` is a plain dict + positional
  progress args; replay task_id must match; `cmd | head` exit code reports head's.
- The two failed wire tests this session were exactly the two mcp landmines above — if a
  P5b wire test fails on cursor/meta, re-check them first.

## 9. Where things live (pointers, not contents)

- `PLAN.md` — §5 P5a/P5b spec (P5a "shipped", P5b "next"), §6 pipeline spec, risk register.
- `docs/TOOL-CONTRACT.md` — §7e P5a contract (normative), §8 adding-a-tool checklist.
- `skeletonkey/core/registry.py` — tiers, route, explain, receipts, advertise, pagination.
- `skeletonkey/core/semantic.py` — SemanticBackend protocol + entry-point discovery.
- `skeletonkey/mcp/adapter.py` — tier-aware advertise, `_page_slice`, list_changed.
- `skeletonkey/tools/builtin.py` — registry.route/expand/explain handlers + specs,
  tier marks on all specs.
- `skeletonkey/toolkit.py` — `plan()` routes; status reports tier/receipts/budget_drops.
- `skeletonkey/cli.py` — `sk tools route/expand`, `--tier/--k/--semantic`, `--gated`.
- `tests/test_discovery.py` (17), `tests/test_mcp_stdio.py` (+3 P5a wire),
  `tests/eval/suite.jsonl` (25 tasks, ground truth `target`).
- `config/skeletonkey.example.toml` — `[advertise]`, `tools.semantic`.
- `schemas/tool-manifest.schema.json` — `tier` enum.
- `skills/fs-safe-refactor/references/discovery.md` — agent-facing discovery guidance.
- `HANDOFF.md` (this file), `PLAN.md`, `.github/workflows/ci.yml` (untracked, push-blocked).
