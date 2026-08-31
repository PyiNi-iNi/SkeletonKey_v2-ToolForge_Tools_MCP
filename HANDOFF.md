# HANDOFF — SkeletonKey / ToolForge v2 (P3–P4b → P5)

Session `arena/01a05944-skeletonkey-v2-toolforge-tools` · 2026-08-31 (America/Chicago).
Written by the agent that shipped P3, P4 and P4b, for the session that starts **P5**.
Read `PLAN.md` for the roadmap and `docs/` for the contracts; this file is the *transfer* —
state, next steps, ideas, and landmines. The previous handoff (P2→P3) is superseded by this
one; its standing constraints are carried forward in §7.

**Agent / model provenance.** The harness is **Arena.ai Agent Mode** (repo-cloned sandbox,
bash + file tools, auto-saved turns). It is not attributable to a single base model: Arena's
Agent Mode draws on many (Claude, ChatGPT, Gemini, Grok, Qwen, Kimi, …), and no specific one
was recorded or should be assumed. Everything below was measured against the tree at handoff,
not remembered: re-measure before you restate it.

---

## 1. State on handoff

| | |
| --- | --- |
| PR | **#2** `arena/01a05944-…` → `main`, 17 commits over `adee12d`, **MERGEABLE / CLEAN** at handoff |
| Branch head | **`58f516f`** == remote tip (verified with `git ls-remote`) |
| Surface | **45 tools registered / 44 advertised / 4 241 advertisement tokens** / digest `6d94a998eca6f081` |
| Skills | 5 packs discovered (incl. new `publishing`), 2 synthesized tools, 0 load errors |
| Tests | **610 passed, 3 skipped, 1 xfailed** (~51 s), 16 test modules; `ruff check` clean |
| Code / docs | **14 571 lines** in `skeletonkey/`; **3 083 lines** of docs (plan, 5 contract docs, 10 ADRs, README, handoff) |
| Phases | P0 ✓ P1 ✓ P2 ✓ P3 ✓ P4 ✓ (replay/eval/plan, streaming, ADR-0009, script-content rules) **P4b ✓** (publishing) → **P5 is next** |
| Untracked (deliberate) | `.github/workflows/ci.yml` — **not committed; see §3, it is blocked on App permissions** |

## 2. What P3–P4b actually is (one paragraph each, because it will be misrepresented)

**P3 — policy as data.** `core/policy.py` compiles `deny`/`allow`/`escalate`/`rate_limit`
rules (legacy strings + structured tables) into one rule list; deny is read first and is
non-overridable by any token. `fs.undo {expect_sha}` is a hard `CONFLICT` guard, `fs.redo`
is a journaled re-apply that refuses drift, `fs.trash` gives three deletion tiers
(`journal` | `os-trash` | `delete`), `policy.grant` returns receipts, and a property test
proves *a wall means zero writes for every mutating tool*.

**P4 — the loop proves the turn.** `toolkit.plan()` ranks a shortlist; `RunRecorder`/
`replay()` re-execute a recorded run in a scratch baseline copy and diff envelopes with
**explicit, closed normalization** (volatile keys dropped, paths rewritten, journal tokens
rewritten; `stateful` tools held to ok+error-code only); `sk eval` scores scripted task
suites. The idempotency cache gained a **mutation generation** so a mutation retires stale
reads (the bug that hid inside "reproduction"). Streaming: per-call log at debug level,
`notifications/progress` for `fs.search`/`fs.glob` when a `progressToken` rides in.
`shell.run` script **content** is scanned by deny/escalate path rules (allow never scans
free text). ADR-0009.

**P4b — publishing with secrets you never see again.** The `pub.*` group (9 tools):
a **write-only credential store** — `pub.store_put/list/delete` over a JSON file that lives
**outside the workspace roots** (default `<user config dir>/skeletonkey/publish/store.json`,
`0600` best-effort, `[publish] store_path` override), so the fs *sandbox* is the wall and no
policy rule protects it. **No tool returns a raw value** (masked metadata only); the only
value flow out of the process is `pub.inject`, which replaces `{{PUB.<id>}}` markers through
the journaled fs layer (`expect_sha` per file, undo via `fs.undo_task` scoped to the call's
own task id, two-pass with **no partial publishes**, `dry_run`, `bindings` remaps, denied
files skipped with a note). The `value` arg is redacted from the ledger via a new manifest
field `secret_args` (engine-level) **and** a `redact_obj` backstop (bare `value`-named
keys). `pub.platforms/payments/packaging` surface `skeletonkey/publish_data.py` (real
console/docs URLs, steps, credential kinds); `pub.testers` emits machine-executable,
secret-free release test plans. ADR-0010. Honest gap, stated in SECURITY-MODEL: `shell.run`
can still read the store file if the user's OS permissions allow it; the store is plaintext
(no keyring dependency — the zero-deps rule).

## 3. What is NOT done (and why)

1. **`ci.yml` is untracked and will stay that way from this branch.** The GitHub App used
   by this harness lacks the `workflows` permission, so any push whose diff touches
   `.github/workflows/` is rejected. The file is in the working tree at
   `.github/workflows/ci.yml` (jobs: `core-constraint`, `test` on 3.11/3.12, `lint`).
   **Unblock options:** (a) grant the App the `workflows` permission in repo settings, then
   commit+push it; (b) the user pushes the file themselves; (c) leave it — the repo has no
   CI at all until it lands, which is why nothing gates PR #2's merge.
2. **P5 is not started.** It is specced in PLAN.md ("Scale and discovery") — tool routing,
   ranking, a tool list that changes underneath a host safely.
3. `watchfiles` is **deliberately not installed** in `.venv` — its absence is the tested
   state (`tools.hot_reload` reports why it can't run). Do not `pip install watchfiles`.

## 4. Next steps (in order)

1. **Merge PR #2** (`gh pr merge 2 --merge --no-delete-branch` — the house style keeps the
   branch). Then re-verify with `git ls-remote origin refs/heads/main`.
2. **Land `ci.yml`** once the App has `workflows` (or the user pushes it). Until then the
   "CI green" claim is a local-suite claim, and that should be said plainly.
3. **Start P5 from PLAN.md's P5 section.** First concrete sub-step: read
   `registry.advertise()`'s selection path (token budget, capability dedupe) and the
   `withheld` receipt — P5 is about making that story scale from 45 to ~200 tools without
   the host drowning.
4. Optional quick win while P5 designs: add a **publish task to `tests/eval/suite.jsonl`**
   (store → scan → inject → verify) so the eval scores the new surface; `pub.*` tools are
   `stateful: "host"` and replay holds them to ok+error-code — worth one explicit replay
   fixture proving that.

## 5. Ideas (honest, prioritized — none are decided)

1. **ADR-0011: a controlled read path, if it ever earns one.** The store is write-only by
   design. The likely first demand is "give me the 2FA code *now*" (`kind: two_factor`
   stores a TOTP seed). If built, it should be a *named, approval-gated, ledgered* tool
   (e.g. `pub.otp {id}` returning a code, not a seed) — and the ADR must say why the wall
   gets this one door. Do not add ad-hoc reads.
2. **Store hardening, still zero-deps:** `expiry` field + `pub.store_list {expiring: N}`,
   and a `rotate` workflow doc (put-same-id is rotation; delete is for the rest). Optional
   (not mandatory) encryption at rest via an *optional* keyring dependency would break
   ADR-0001's spirit — the zero-deps rule says mandatory, not impossible; weigh it in an
   ADR if it comes up.
3. **`pub.inject` for >2 MB / binary-templated files** is out of scope today (text cap,
   honest skip note). If a real need appears, the fix is byte-level marker replacement with
   an explicit encoding contract, not lifting the cap.
4. **Publish orchestration:** a `pub.run_plan` that executes a `pub.testers` plan step by
   step is a *loop* concern, not a tool — it belongs to P5's planner or the autopilot
   harness, and it should stop on the plan's `stop_rule`. Don't build a mini-interpreter
   inside a tool.
5. **Replay the publish:** a recorded publish run (store_put is a no-op read from the
   store's viewpoint, inject mutates) makes a great ADR-0009 stress fixture: it exercises
   `stateful: "host"` normalization, journal-token rewriting, and the no-partial-publish
   error path in one run.
6. **Windows honesty pass** (P6 territory): on NT, `chmod 0600` sets the read-only
   attribute and *nothing else* — the SECURITY-MODEL store section already says this in
   general; a `win`-tagged test asserting the store file's actual NT state would close it.

## 6. Suggestions for the next session

- **House rule, unchanged:** every new tool ships a TOOL-CONTRACT section (or extension of
  an existing one), an entry in the skill guidance an agent will read, and a **wire-level**
  test. "A feature that only works when called from Python is not done."
- **Spec-first:** write the PLAN.md section before code; commit in 3-ish chunks
  (core+data / tools+wiring / docs+skills) with **explicit `git add <paths>`** — `git
  commit .` sweeps in untracked files and has bitten twice.
- **Push early and often:** this sandbox has recycled at least once and wiped local state;
  the remote is the only durable record.
- **Measure, don't remember:** token counts, digests, test counts, and commit SHAs in this
  doc were re-measured at handoff time; re-measure again before you cite them.
- The `.venv` is persistent in the sandbox (mcp 2.1.1, pytest, ruff, pyyaml; **no
  watchfiles**). `/tmp` is not persistent.

## 7. Standing constraints (carried from the original handoff §9, reaffirmed)

- Apache-2.0 + "Dime" authorship preserved; README keeps the original title line and
  "Dime's Custom Toolkit" tagline. No relicense/retitle/author-tidying.
- Python 3.11+, **zero mandatory dependencies**; the core must import with nothing
  installed (`core-constraint` job — once ci.yml lands).
- Windows and Linux/macOS both first-class; pwsh is not optional; every PowerShell claim is
  backed by a rendered-payload assertion or a marked `win` test that self-skips off Windows.
- The primary consumer is the bespoke autopilot loop (tools may be richer/stateful than
  generic MCP); the MCP surface ships and stays honest (second consumer).
- Provenance: the harness is Arena.ai Agent Mode, not a single base model.

## 8. Landmines (measured this session; the old ones still bite)

- **`E` is a namespace class** in `core/errors.py` (line ~54) — not `ErrorCode`. And
  `SkeletonKeyError.code` is a **str**, so `exc.code in {E.DENY_RULE}` never matches
  (compare `E.DENY_RULE.code` or the string). Cost one failing test cycle.
- **The engine's `_ledger` swallows all exceptions** (`except Exception: pass`) — a typo
  like `self.REDACTED` (no such attr) silently drops *every* ledger row for affected tools
  and no test fails until one asserts row presence. Ledger tests must assert a row exists.
- **Legacy `deny: ["**"]` is a tool glob** (`matches_tool`), no path constraint → it denies
  *every* call of *every* tool, not just path-bearing ones. Path-specific denies need the
  `tool(**/glob)` form.
- **`fs.glob` includes root-level dotfiles** (`.env` matches `**/*`) but not dot
  *directories*. Scanners that read all glob results must treat `DENY_RULE` on individual
  reads as skip-with-note, not fatal.
- **`ReadResult.sha256`** is full-length as an attribute but truncated to 16 chars in
  `to_dict()` — pass the attribute into `expect_sha`, not the dict value.
- **GitHub App cannot push workflow files** (no `workflows` permission); also, the local
  fetch refspec tracks **only `main`** — verify branch state with
  `git ls-remote origin <branch>`, not `origin/<branch>`.
- **mcp 2.1.1 wire shapes** (P4): `req.meta` in `tools/call` is a plain dict;
  `ClientCapabilities` has no `logging` field; `send_progress_notification` args are
  positional; `send_log_message(level, data, logger=None, related_request_id=None)`.
- Carried: replay flake fixed via `(-mtime, path)` + sorted dirnames; `.venv` needs mcp and
  NOT watchfiles; no `python -m skeletonkey` (it's `skeletonkey.mcp` / the `sk` CLI);
  replay task_id must equal the recorded one; eval refs are `$<n>.data.<path>`; never mark
  fs reads stateful; `cmd | head; echo $?` reports `head`'s exit.

## 9. Where things live (pointers, not contents)

| Thing | Where |
| --- | --- |
| Publish core (store + marker engine) | `skeletonkey/core/publish.py` |
| Knowledge bases + test plans | `skeletonkey/publish_data.py` |
| `pub.*` specs + handlers | `skeletonkey/tools/builtin.py` (group `publishing`) |
| Store wiring / default path | `skeletonkey/toolkit.py::build` (`[publish] store_path`) |
| `sk pub` CLI | `skeletonkey/cli.py` |
| `secret_args` declaration + ledger redaction | `core/manifest.py` (field), `core/engine.py::_ledger` |
| Bare-`value` redaction backstop | `core/redact.py::_KEY_ONLY` |
| Contracts | `docs/TOOL-CONTRACT.md` §7d (publishing), §4b (policy) |
| Security claims | `docs/SECURITY-MODEL.md` ("The publish store (P4b)" + test map) |
| Decisions | `docs/adr/0010-publish-store-write-only.md`, `0009-replay-proves-the-turn.md`, `0008-…` |
| Roadmap + P4b/P5 specs | `PLAN.md` |
| Agent guidance | `skills/publishing/SKILL.md` + `references/first-publish.md` |
| Tests | `tests/test_publish.py` (28), wire: `tests/test_mcp_stdio.py::test_publish_store_and_inject_over_the_wire`, walls: `tests/test_policy_property.py` (BURST table) |
