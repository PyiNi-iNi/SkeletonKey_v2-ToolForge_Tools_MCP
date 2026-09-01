# HANDOFF — SkeletonKey / ToolForge v2 (P5 shipped → P6)

Session `arena/01a05a44-skeletonkey-v2-toolforge-tools` · 2026-09-01.
Written by the agent that shipped **P5** (P5a discovery at scale + P5b semantic stage,
`fs.search` fallback honesty, `mcp.client` aggregation), for the session that starts
**P6** (distribution + hardening) and then P7 (Windows frontier spike). Read `PLAN.md`
for the roadmap and `docs/` for the contracts; this file is the *transfer* — state, next
steps, ideas, and landmines. The P3→P5a handoff is superseded by this one; standing
constraints are carried in §7.

**Agent / model provenance.** The harness is **Arena.ai Agent Mode** (repo-cloned sandbox,
bash + file tools, auto-saved turns). It is not attributable to a single base model:
Arena's Agent Mode draws on many (Claude, ChatGPT, Gemini, Grok, Qwen, Kimi, …), and no
specific one was recorded or should be assumed. Everything below was measured against the
tree at handoff, not remembered: re-measure before you restate it.

---

## 1. State on handoff

| | |
| --- | --- |
| Branch | `arena/01a05a44-skeletonkey-v2-toolforge-tools` — **push only here** |
| HEAD | `d8e1dbb` (pushed). P5 commits: `873de6f` (P5a spec), `4e9a1c3` (P5a core), `7dc1e19` (P5a tools+wiring), `2d43528` (docs), `f2a5641` (skills), `84f2ae8` (handoff), `c8621bb` (P5b spec+ADRs), `b08ca56` (semantic), `ce76ddf` (fs.search fallback), `d8e1dbb` (mcp.client) |
| main | still at `6ad120e` — **all of P5 is on the arena branch, not merged**; merge + review is yours (see §3.4) |
| Test suite | **651 passed, 3 skipped, 1 xfailed** in ~60 s (`pytest -q` exit 0) |
| Ruff | clean |
| Venv | repo `.venv`: mcp 2.1.1, pytest, ruff, pyyaml, **package installed editable** (`pip install -e .`) — needed so remote *child* servers (`python -m skeletonkey.mcp`) resolve from any cwd. No watchfiles (its absence is the tested state) |
| Registered tools | **50** (repo-root build, no remotes) |
| Advertised (default `full`) | **48** tools, **4752** tokens; per-tier core 11/945, task 38/3482 |
| Route hit-rate | 25/25 (1.000) at k=5; **semantic stage reorders 13/25 eval tasks** and keeps 25/25 (AC2 now real, both asserted) |
| Remote tests | engine level `tests/test_remotes.py` (8) + wire level 2 in `tests/test_mcp_stdio.py` — all green |
| Docs | TOOL-CONTRACT §7e (P5a) + §7f (remote, ADR-0013), §3 `REMOTE`; ADR-0012 (semantic), ADR-0013 (remote); README updated |

## 2. What P5 actually is (one paragraph each, because it will be misrepresented)

- **P5a — discovery at scale.** `tier` on every manifest (`core`/`task`/`full`, default
  `full`; manifestation only, never authorization); tier-aware `registry.advertise` with
  per-tier `[advertise]` budgets + honest `budget_drops`; `registry.active_tier` switched
  by `registry.expand`; two-stage `registry.route {task, k, semantic}` (exact → lexical
  with per-hit `reasons` → optional backend); `selection_receipts`/`provider_receipt` in
  snapshots, `registry.list` rows, MCP `_meta`, and `capabilities.explain`; cursor
  pagination on `registry.list` and MCP `tools/list` (opaque positional, page 100, bad
  cursor → page 0); digest-driven `tools/list_changed` on the calling session.
- **P5b — semantic stage (ADR-0012).** The core ships `lexical-tfidf` (pure stdlib,
  TF-IDF cosine over words + char bigrams, micro-corpus idf, deterministic, versioned),
  registered under the `skeletonkey.semantic` entry-point group (installed dists) and
  resolved directly in dev checkouts; discovery dedupes by name and returns per-backend
  `load_errors`. `tools.semantic = false` (default) keeps the lexical path untouched;
  `semantic = true` blends normalized lexical + semantic 50/50 with an id tie-break and
  reports `mode`/`backend`/`semantic_score`/`blend`. AC2 asserts both modes: same
  candidate ids, reordering observed, hit-rate intact.
- **P5b — `fs.search` fallback honesty.** Auto-selected ripgrep that vanished at call
  time falls back to the built-in python walker: `data.provider` and
  `metrics.provider == "python"`, a `warnings` entry naming the fallback, `data.notes`
  for payload consumers. `prefer="ripgrep"` still raises `MISSING_BINARY`.
- **P5b — `mcp.client` aggregation (ADR-0013).** `[mcp.remotes.<name>]` (command+args
  stdio OR url; enabled; timeout_s) enrolls at build time as
  `remote.<server>.<tool>`: risk inherited (`readOnlyHint` → read; unannotated ⇒
  `write`, never lowered), `reversible: false`, `stateful: "host"`, `idempotent: false`,
  `source/provider: "remote:<server>"`, unique capability, `tier: "full"`. Each server
  runs one thread + its own asyncio loop (sync engine calls it; mcp imported lazily).
  Skeletonkey-shaped remote envelopes pass through code-verbatim (BAD_ARGS stays
  BAD_ARGS); foreign errors → `REMOTE` (new code); transport/probe → `DEPENDENCY_MISSING`;
  connect/list failures → `load_errors` + build report, never a silent absence.
  `registry.stats` rows carry `source`, `stats(source=...)` filters,
  `stats_by_source()` groups.

## 3. What is NOT done (and why)

1. **Merged to main.** All five phases of P5 sit on the arena branch. PRs: the branch
   was created from `main` at `6ad120e`; review (and merge `--merge` or squash) is the
   next session's first job. Nothing on `main` has tiers/routing/remotes yet.
2. **`ci.yml` still untracked** (`.github/workflows/ci.yml` exists; the GitHub App lacks
   `workflows` permission so any push touching `.github/` is *rejected by design — don't
   retry*). Jobs included: `core-constraint`, `test` 3.11/3.12, `lint`. Unblock: grant
   the App `workflows`, user pushes it, or leave it (repo has no CI until then).
3. **P6 is the next phase.** Distribution/hardening per PLAN §6 (Windows CI *before* P7
   is the stated priority), plus the small open items below.
4. **Optional quick wins not taken.** publish task in `tests/eval/suite.jsonl`; store
   `expiry` + rotate doc; Windows NT chmod-0600 honesty test; `registry.explain_all`
   (whole-surface gates in one call).

## 4. Next steps (in order)

1. **Merge P5 to main** (it is pushed and green; make sure `.github/` is excluded from
   any commit before pushing — the branch currently has it untracked only).
2. **Land `ci.yml`** once the App has `workflows` (or the user pushes it). Until then
   "CI green" is a local claim.
3. **Start P6 from PLAN.md §6.** Its first sub-step is Windows CI *before* P7's remote
   Windows spike: a GitHub-hosted `windows-latest` job (if workflow permission arrives)
   or a documented local Windows run; PowerShell assertions already self-skip off
   Windows (`pwsh`/`powershell` probes), so a real Windows machine is the only gap.
4. If P6 stalls, the highest-value leftover is the **publish eval task** and the
   **Windows NT store-permission test** (§3.4) — both small, both close honesty gaps.

## 5. Ideas (honest, prioritized — none are decided)

- `capabilities.explain` is per-capability; a whole-surface `registry.explain_all`
  (gates + receipts for everything in one call) would make the 200-tool world
  debuggable from a prompt. Same data, one projection.
- `route` could emit a compact "tool shortlist" block for the next prompt — it already
  carries reasons; a UX decision for the autopilot loop, not a tool change.
- Remote servers: `registry.route`/budgets currently treat remote tools like any
  `full`-tier tool (they count toward caps); decide whether remote tools should ever
  opt into `core`/`task` tiers when a server is trusted.
- Do **not** build `pub.run_plan` (a loop concern, not a tool). Recorded again because
  it keeps coming back.

## 6. Suggestions for the next session

- **House rule, unchanged:** every new tool ships a TOOL-CONTRACT section (or extension),
  a skill-guidance entry an agent will read, and a **wire-level** test. "A feature that
  only works when called from Python is not done."
- **Spec-first + 3-ish chunks** (core+data / tools+wiring / docs+skills) with **explicit
  `git add <paths>`** — `git commit .` sweeps in untracked files; `.github/` stays out.
- **Push early.** Sandbox recycles: it re-clones at the base commit and reapplies file
  state as an uncommitted patchset, so *unpushed* commits can vanish (recovery this
  session: fetch the remote branch, `git reset --soft` to it, re-add and commit — files
  hash-match the remote, so the delta is exactly the new work). `git push` early.
- **Measure, don't remember:** token counts, digests, test counts, SHAs re-measured at
  handoff; re-measure before citing. pytest 9.1.1 suppresses the summary line when
  piped — use `-rA`/grep or trust exit code.
- **Venv gotcha for remote tests:** the package must be installed (`pip install -e .`)
  or remote *child* servers launched from a tmp cwd can't import `skeletonkey`. The RpcClient
  helper sets `PYTHONPATH` for the outer server only.

## 7. Standing constraints (carried from the original handoff §9, reaffirmed)

- **Licensing/identity frozen:** Apache-2.0, authorship stays "Dime", README title line +
  tagline unchanged. No relicense/retitle/author-tidying without the owner.
- **Python 3.11+, zero mandatory dependencies.** Core imports with nothing installed
  (ADR-0001); extras (`mcp`, `watch`, `dev`, `all`) carry the rest; the `mcp.client`
  connector imports `mcp` lazily so a no-remotes build never pays the import.
- **Windows + Linux + macOS first-class; PowerShell is not optional.** Every PowerShell
  claim is backed by a rendered-payload assertion or a `win`-tagged self-skipping test.
- **Primary consumer is the bespoke autopilot loop; MCP surface ships and stays honest.**
  No silent reordering: rankings, gates, and receipts are data; a remote server's error
  code is never re-wrapped.
- Don't `pip install watchfiles`; no `python -m skeletonkey` (it is `skeletonkey.mcp` /
  `sk`).

## 8. Landmines (measured this session; the old ones still bite)

- **mcp 2.1.1 lowlevel:** `tools/list` params model is `PaginatedRequestParams`
  *directly*, not a `ListToolsRequest` wrapper — registering with the wrapper makes
  `params` a model whose `.params` is `None` and the cursor silently never arrives.
  Result `meta` serializes as `_meta` on the wire. Check `mcp_types` camel/snake-case
  fields (`read_only_hint`, `input_schema`, `is_error`) before use.
- **RemoteServer keep-alive:** the thread IS the event loop — `threading.Event.wait` in
  the keep-alive loop blocks every `run_coroutine_threadsafe` call; use
  `asyncio.sleep(0.25)` and `await` yourself.
- **Drop-in contract is `TOOL`/`TOOLS`/`register()`** (plus MANIFEST(S)); `TOOL_SPECS`
  is the built-in internal name and will not be picked up ("no TOOL/TOOLS/register()
  found").
- **`engine.call` returns a failure `ToolResult` for UNKNOWN_TOOL** — don't
  `pytest.raises`; assert `r.error.code` (discovery tests already corrected this).
- **Sandbox recycle** (see §6): venv excluded from snapshots (recreate + editable
  install), `.git` history re-cloned at base.
- `SkeletonKeyError.err.code` is the **string** `"BAD_ARGS"` (compare to `"BAD_ARGS"`,
  not the enum); `registry.all()` is a method returning manifests; `AdSnapshot`
  has `.tokens`/`.digest`, not `.tokens_estimate`; config `_set_path` handles
  `[mcp.remotes.<name>]` because `mcp.remotes` is a `dict` field (per-server keys are
  raw dicts — RemoteSpec validates them).
- Skill inject cap: `fs-safe-refactor` is at ~3995 tokens — detail lives in
  `references/` (not counted). Remote/semantic pointers must stay one line.
- Old ones unchanged: `E` namespace class; `_ledger` swallows exceptions; legacy
  `deny: ["**"]`; path denies need `tool(**/glob)`; `fs.glob` dotfile behavior;
  `ReadResult.sha256` 16 chars; `req.meta` plain dict + positional progress args;
  replay task_id match; `cmd | head` exit code; `python -m pytest` from repo root.

## 9. Where things live (pointers, not contents)

- `PLAN.md` — §5 P5a/P5b (both shipped), §6 P6 portal, risk register.
- `docs/TOOL-CONTRACT.md` — §3 errors, §7e (P5a), §7f (remote, ADR-0013), §8 checklist.
- `docs/adr/0012-semantic-backend.md`, `0013-remote-tools-passthrough.md`.
- `skeletonkey/core/{registry,semantic,config,errors}.py` — tiers/route/explain/receipts,
  SemanticBackend + LexicalSemantic + discover(), McpConfig.remotes, REMOTE code.
- `skeletonkey/mcp/client.py` — RemoteSpec/RemoteServer/RemoteConnector (P5b).
- `skeletonkey/mcp/adapter.py` — tier-aware advertise, `_page_slice`, list_changed,
  `_meta` receipts.
- `skeletonkey/toolkit.py` — build: builtins → skills → drop-ins → entry points →
  **remotes** (report["remote"]).
- `skeletonkey/fsx/search.py`, `tools/builtin.py` — fallback honesty; registry.route/
  expand/explain/stats.
- `tests/test_discovery.py` (P5a ACs + AC2 both modes), `test_semantic.py` (backend +
  discovery contract), `test_remotes.py` + `remote_helpers.py` (engine-level remote),
  `test_mcp_stdio.py` (+P5a wire, +2 P5b remote wire), `test_tools_builtin.py`
  (search fallback).
- `tests/eval/suite.jsonl` (25 tasks, `target` ground truth) — semantic AC2 runs on it.
- `skills/fs-safe-refactor/references/discovery.md` — agent-facing discovery + remote
  guidance; `config/skeletonkey.example.toml` — `[advertise]`, `tools.semantic`,
  `[mcp.remotes.<name>]` sample.
