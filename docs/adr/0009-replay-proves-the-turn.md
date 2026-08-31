# ADR 0009: replay proves the turn — explicit normalization, `stateful` means loose, mutations retire cached reads

Date: 2026-08-31
Status: accepted
Affects: `core/replay.py`, `core/engine.py` (cache generation), `fsx/ops.py` (glob
tie-break), `fsx/search.py` (walk order), `mcp/adapter.py` (streaming), `toolkit.py`
(`plan`), `cli.py` (`replay`/`eval`), `tests/test_replay.py`, `tests/eval/suite.jsonl`

## Context

P0–P3 made every *call* verifiable and reversible, but the loop had no way to prove a
finished *turn*: no record of a run it could re-execute and compare, no way to score a
scripted task against the toolset, and no channel for a host to watch a long call. And
the first harness run exposed a real engine bug — the idempotency cache served a stale
read after a mutation (search → patch → same search inside the 5 s TTL returned the
pre-patch answer). Worse, the bug hid inside "reproduction": the recorded run and its
replay hit the same stale cache, so the two agreed, and a naive diff called the run
reproducible.

## Options considered

1. **Fuzzy replay** — tolerate diffs on volatile fields and "close enough" data.
   Rejected: tolerance is normalization in hiding, and every new diff kind becomes a
   tuning argument about how fuzzy is fuzzy.
2. **Strict byte-for-byte diff, full stop.** Rejected as stated: timestamps, run
   identities, and scratch-copy paths legitimately differ, and a tool that declared
   itself `stateful` *promised* live data — holding its data to byte equality would be
   holding it to something it never promised.
3. **Explicit, closed normalization; everything else strict; the strictness boundary is
   the tool's own `stateful` declaration.** Chosen.

For the stale cache: (a) mark the `fs` read family `stateful="session"` — rejected, it
would demote most of the toolset to loose comparison and gut the strictness the harness
exists to assert; (b) carry a **mutation generation** in the cache key, bumped on each
successful mutation — chosen: no locking, stale entries simply go unreachable, and the
`stateful` meaning is untouched.

## Decision

- **A recording is a JSONL of full envelopes plus a start-state snapshot.**
  `RunRecorder` writes `<recording>.baseline` (the workspace as found *before* the
  first step — a mutation run changes the tree, so a replay that starts from the run's
  end state re-runs steps against their own results). `replay()` copies the baseline
  into a scratch workspace (the original is never touched) but rewrites paths against
  the *live* workspace, because that is where the envelopes were written.
- **Normalization is explicit and closed.** Volatile keys are dropped wherever they
  occur (identity: `run_id`/`trace_id`; wall time including file `mtime`/`atime`; wire
  size: `est_tokens`/`bytes_out`/`duration_ms`); the workspace and state roots are
  rewritten to `<WS>`/`<STATE>`; journal `und_<hash>` tokens — per-call identities —
  are rewritten. Anything else is diffed byte-for-byte. A diff is a real difference.
- **`stateful` is the strictness boundary.** A tool that declared itself stateful
  (session or host state) is held to `ok` + error code only; everything else is held
  to strict `data`. A tool that cannot survive a strict diff must declare `stateful` —
  the declaration is the contract, not a test convenience.
- **A mutation retires every cached read.** The idempotency cache key carries the
  engine's mutation generation, bumped in the same `finally` that counts the
  mutation. The search → patch → search-again verify loop reads the new state, never
  the pre-patch answer. Pinned by
  `test_engine_policy.test_a_mutation_retires_cached_reads`.
- **Stable output is part of the contract.** `fs.glob`'s mtime sort tie-breaks on path
  (files created in the same instant share an mtime, and a stable answer is the only
  one a replay can reproduce), and the pure-Python search walk sorts its directories.
  This was the harness's first flake: equal mtimes fell back to readdir order, which
  differed between the recorded dir and the scratch copy.
- **Eval tasks are static but honest.** A step's args may reference an earlier step's
  data as `"$<step>.data.<path>"` — the only way a static script can use a `job_id` or
  token it can only know after the fact; a missing reference fails the task explicitly.
- **Streaming is opt-in and honest.** `--log-level debug` makes every `tools/call`
  stream a `notifications/message` log line (below debug the channel is quiet). A
  `progressToken` in the request `_meta` makes tree-scanning tools (`fs.search`,
  `fs.glob`) answer with `notifications/progress`: an immediate `progress=0`
  acknowledgement, then elapsed-second pings while the scan is alive. The value is
  indeterminate on purpose — a tree walk's total is unknown until it finishes.

## Consequences

The loop now has its integration surface — `toolkit.plan(task)` (ranked tools, matched
skills, exact budgets, replayable calls), per-call `context_receipt`s in the ledger,
`sk replay` for a finished turn, `sk eval --suite` for a task set — and stops calling
tools through ad-hoc glue (the P4 exit gate). Acceptance, **re-measured on this box on
2026-08-31** (re-measure rather than trust memory; the box is a CI-style Linux sandbox
and the numbers move with it):

- 12-step recorded refactor: `sk replay` reproduces the same `data` for every step
  except the normalized fields, and the ledger shows exactly one row per call (12/12).
- Shipped eval suite: 25/25 tasks, median 3 calls/task (bar: ≤ 6), mean ≈ 890
  tokens/task, 1 refusal-then-recovery — the refusal is the suite's intentional
  `PATCH_CONFLICT` (a bad anchor that the task re-reads around).
- Full suite: 578 passed, 3 skipped, 1 xfailed; the core still imports with zero
  third-party packages.

Costs: one workspace-sized `.baseline` per recording (delete it to keep the run's
workspace pristine); a recompute after each mutation (bounded by the same 5 s TTL the
cache already had); log notifications only when the host asked. Open gaps unchanged:
`shell.run` script-content rules (argv-prefix + secret-path matcher, deny stays
non-overridable) and the CI workflow file (spec'd in PLAN §6, not committed).
