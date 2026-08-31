# SkeletonKey / ToolForge v2 — Phase Plan

**Dynamic toolset + skills + MCP server for autopilot and autonomous agents.**
Adaptive by host, reversible by default, bounded in bytes and tokens.

Status: **P0 shipped, P1 shipped**, and documented. CI is specified (§6) but not
committed as a workflow file yet.

```bash
pip install -e ".[dev,mcp]"
ruff check .                      # All checks passed!
pytest -q -m "not slow"           # 377 passed, 2 skipped, 1 xfailed  (~18 s)
sk describe                       # what this host advertises, with probe receipts
python -m skeletonkey.mcp         # stdio server: 30 tools, prompts, resources
```

Measured on this box: 32 tools registered / 30 advertised / **1 909 tokens** of
advertisement (`digest d5de0fcc7cb61eb5`), 10 probed capabilities, 2 skills discovered,
0 load errors. Code: 10 102 lines in `skeletonkey/`, 16 files in `tests/` (377 passing,
2 skipped, 1 xfailed), 1,919 lines of docs (this plan, four contract docs,
README; seven ADRs). No workflow file is committed on this branch — the pushing token
cannot write to `.github/workflows/` — and the pipeline is specified in §6 for whoever
lands it (one command reproduces it locally: `ruff check . && pytest -q -m "not slow"`).

Contracts live in `docs/` (`TOOL-CONTRACT`, `SHELL-DIALECTS`, `SKILLS-SPEC`,
`SECURITY-MODEL`), decisions in `docs/adr/`, knobs in `config/skeletonkey.example.toml`,
shapes in `schemas/`.


---

## 1. What this is, and for whom

The primary consumer is **our own autopilot loop**: a long-running agent that calls
tools hundreds of times per task, cannot ask a human what `exit 2` meant, and must not
lose a user's files while doing it. That constraint drives every design decision below,
and it is why the surface is richer than a generic MCP server: tools are stateful
(sessions, jobs, journals), results carry their own continuation (`next_actions`), and
every mutation is retractable.

The **MCP endpoint** is the same engine behind a standard transport
(`python -m skeletonkey.mcp`, stdio or streamable-http), so third-party hosts get the
tool set, prompts and resources too. It is a projection, not a second implementation:
the envelope is returned verbatim in `structuredContent` *and* as JSON text for hosts
that ignore structured content.

Targets, first-class and not optional:

| | Windows | Linux / macOS |
| --- | --- | --- |
| shell | `pwsh` 7 → `powershell` 5.1 fallback | `bash` → `sh` |
| scripting | `python` dialect everywhere | `python` dialect everywhere |
| paths | drive letters, `\\?\` long-path prefix, `\` separators, device names | POSIX paths, symlinks, modes |
| text | CRLF, cp1252 consoles, CLIXML stderr | LF, UTF-8 |
| privilege | UAC / `is_admin`, ACLs instead of chmod | file modes, ownership |

---

## 2. Design principles (the invariants every phase must keep)

1. **Advertise = what the host can actually run here.** The registry's single
   `advertise()` step is the only source of truth: profile gates, `read_only`
   withholding, `tools.disable`, `advertised=False`, provider de-duplication and the
   token budget all resolve there, and everything (MCP `tools/list`, `registry.list`,
   `sk tools`) reads the same snapshot with the same `digest`.
2. **Every tool returns the same envelope.** `ok / tool / run_id / data / error /
   artifacts / hints / next_actions / metrics / warnings`. Success and failure are the
   same shape, so a host needs one parser and one retry policy.
3. **A failure must be actionable or it is a bug.** Codes are typed (`MISSING_ARG`
   carries the missing name, `BAD_ARGS` a `minimal_example`, `TOOL_NOT_ADVERTISED` the
   unmet requirement *plus the probe receipt that produced it*, `APPROVAL_REQUIRED` a
   replayable token, `TIMEOUT` a `background: true` retry). An agent should be able to
   recover without re-deriving the world state.
4. **Nothing is silently missing.** Empty search results explain themselves; a truncated
   result says so and ships a spill artifact holding the complete payload; a withheld
   tool says why. A wrong-but-plausible `count: 0` is the worst output this system can
   produce (see ADR-0005).
5. **Mutations are reversible by default.** Every `fs.*` write journals a before-image
   first and returns an `undo_token`; `fs.undo_task {task_id}` retracts a whole turn.
6. **The context window is a budget, not a suggestion.** Output bytes, per-task calls,
   mutations, tokens out and wall time are enforced by the engine, and each is reported
   in `metrics` so the planner can see what a call cost.
7. **stdout is sacred.** On the MCP transport, protocol frames only; every diagnostic
   goes to stderr or the log file.
8. **Dialects are rendered, not string-formatted.** One renderer produces bash /
   PowerShell / python payloads with the same sentinel protocol, environment capture and
   error contract (ADR-0002, ADR-0003).
9. **The audit trail survives the run.** A hash-chained NDJSON ledger records every
   call exactly once, with secrets redacted from previews while `result_digest` covers
   the pre-redaction bytes.

---

## 3. Architecture

```
skeletonkey/
  core/        util errors envelope validate manifest profile coerce config redact ledger registry engine
  shells/      dialect.py (render/parse/CLIXML)  base.py (ShellRunner, sessions, jobs)
  fsx/         sandbox.py (roots, deny, links)  ops.py (read/write/patch/move/…)
               search.py (rg + built-in walker)  journal.py (before-images, undo)
  skills/      loader.py (SKILL.md + references + tool.toml)
  tools/       builtin.py (31 TOOL_SPECS + register())
  mcp/         adapter.py (low-level MCPServer)  __main__.py (stdio/http)
  toolkit.py   build() -> Toolkit(engine, fs, journal, shells, skills, sandbox)
  cli.py       `sk` — the same engine, no MCP, for humans and smoke tests
skills/        fs-safe-refactor/  shell-crossplatform/   (SKILL.md + references/ + tool.toml)
schemas/       tool-manifest.schema.json  tool-result.schema.json  skill-frontmatter.schema.json
config/        skeletonkey.example.toml
tests/         16 files, 377 tests, incl. a raw-JSON-RPC MCP stdio client
docs/          TOOL-CONTRACT, SHELL-DIALECTS, SKILLS-SPEC, SECURITY-MODEL, adr/0001-0007
```

Call lifecycle (`Engine.call`): resolve → gate (disable/read_only/profile) → validate
(own JSON-Schema subset, defaults applied) → authorize (deny rules, dry-run, approval)
→ budget charge → idempotency cache → dispatch (signature-injected `ctx/fs/journal/shells/
engine/dry_run`) → normalize → estimate → **audit + reliability record in `finally`** →
envelope. Exceptions never escape: `classify_exception` maps them onto the taxonomy.

---

## 4. Contracts

| Contract | Where | Schema |
| --- | --- | --- |
| Tool manifest (what a tool declares) | `core/manifest.py` | `schemas/tool-manifest.schema.json` |
| Result envelope | `core/envelope.py` | `schemas/tool-result.schema.json` |
| Skill frontmatter + directory layout | `skills/loader.py` | `schemas/skill-frontmatter.schema.json` |
| Error taxonomy | `core/errors.py` (`E.*`, `ErrorClass`) | — |
| Shell dialect rendering + sentinel | `shells/dialect.py` | `docs/SHELL-DIALECTS.md` |
| Sandbox path policy | `fsx/sandbox.py` | `docs/SECURITY-MODEL.md` |
| Journal / undo | `fsx/journal.py` | `skills/fs-safe-refactor/references/undo-and-journal.md` |
| Config precedence | `core/config.py` | `config/skeletonkey.example.toml` |
| MCP projection | `mcp/adapter.py` | `docs/TOOL-CONTRACT.md` |

`docs/TOOL-CONTRACT.md` is the normative text for tool authors; the schemas are
machine-checkable subsets of it (they document fields the registry validates today).

---

## 5. Phase plan

Effort is in focused engineer-days for one person who knows the codebase; ranges
include the test suite and the docs that make each phase usable by an agent.

### P0 — Foundations and contract (shipped)

**Goal.** One envelope, one error taxonomy, one registry, one config, so that every
later phase adds behaviour instead of adding shapes.

**Delivered.** `ToolResult`/`Artifact`/`Metrics` with byte-budget enforcement and
spill-to-file (`_apply_budget` bisects the inlined prefix rather than truncating mid
payload); `E` error taxonomy with `error_class`/`retryable`/`hint`/`details`; a
dependency-free JSON-Schema subset validator (`core/validate.py`: types, required,
`additionalProperties`, combinators, `pattern`, `min/max`, `format`, `uniqueItems`);
`ToolManifest` with `from_dict` tolerant enough to read hand-written `tool.toml`;
layered `Config` (defaults < user < `./config` < `./skeletonkey.toml` <
`$SKELETONKEY_CONFIG` < `SKELETONKEY_*` env < overrides) with coercion notes surfaced
as `overrides_applied` and `warnings` instead of a failed load; capability profile with
probe receipts; hash-chained NDJSON ledger with redaction; registry with
advertise/gate/digest/search.

**Acceptance (all green).** `tests/test_core_contracts.py`, `test_envelope.py`,
`test_ledger_redaction.py`, `test_registry_config.py` — e.g. `to_dict(max_bytes)` never
exceeds the budget and the spill file is the complete payload; a tampered ledger line
reports `broken_at.reason == "digest mismatch"`; a torn tail line is dropped on open and
the chain stays valid; a redacted result preview is still parseable JSON.

**Exit gate met.** Nothing downstream re-derives an envelope, an error, or the
tool list.

**Risks carried forward.** A hand-rolled validator is a maintenance bet (ADR-0004);
it is covered by ~40 direct tests and refuses to guess.

---

### P1 — Adaptive core (shipped)

**Goal.** An agent can do real work on an unknown host: read/write files safely, run
scripts in three dialects, and call all of it over MCP — with dynamic dispatch instead
of a fixed tool list.

**Delivered.**

- **Engine**: dispatch with signature injection, short-TTL idempotency cache (only for
  `idempotent and not mutating and stateful == "none"`), budget governor hooks, approval
  flow with `once/task/session` grants, profile-refresh + `tools/list_changed`
  plumbing, per-tool reliability stats recorded once per call from the `finally`
  (successes included — providers are ranked on this).
- **Shells**: `ShellRunner` + `dialect.render` for bash/sh, pwsh/powershell, python; `argv`
  pass-through (no shell parser in the path) and `shell.quote` for values that must be
  embedded in the program text;
  sentinel protocol (random per-call token, EXIT-trap so a `set -e` abort is still
  read), cwd/env capture for `session` persistence, background jobs with
  `job_wait`/`job_kill`, CLIXML stderr decoding on Windows, `strict_*` preambles,
  `env_mode: clean`, tree-kill on timeout.
- **FS**: sandbox with roots + deny + symlink policy + Windows device-name and long-path
  handling; `read` (paging + `next_call`), `write` (`expect_sha`, atomic, newline
  policy), `patch` (ordered edits, `occurrence`/`replace_all`, `at_line`, unified diff),
  `search` (ripgrep or built-in walker, same result shape, honest zero-match advice),
  `list`/`glob`/`stat`/`sniff`/`move`/`delete`/`mkdir`/`chmod`, journal + `undo` /
  `undo_task` / `journal_list`.
- **Skills (P1 slice)**: `SKILL.md` + `references/` + `scripts/` + `tool.toml` loader,
  lexical task matching, token-budgeted injection block, declared tools registered
  hidden with a `NOT_IMPLEMENTED` handler so the registry already knows them.
- **MCP**: low-level server (not the decorator API) with `tools/list` (dotted names,
  annotations, `_meta.sk`), `tools/call`, `prompts/{list,get}` including one prompt per
  skill, `resources/{list,read}` (profile, tools, journal, ledger tail, workspace
  files), `tools/listChanged` advertised, `initialize` untouched.
- **CLI**: `sk profile|tools|shell|jobs|fs|skills|describe|call|mcp` for humans and CI.

**Acceptance (all green).** 377 tests including a raw JSON-RPC stdio client that proves:
stdout carries only protocol frames; `fs.read` of `../../../etc/passwd` is a *tool*
error (`isError: true`, `SANDBOX_VIOLATION`) and the session survives; `registry.stats`
counts successes; a spill artifact's `fetch_rest` is a legal `fs.read`; a denied dialect
is refused with the allow-list in `details`; `undo` still works after a process restart;
`profile.json`-style filenames are not mistaken for tool ids in skill bodies.

**Exit gate met.** A fresh host with no pwsh, no rg and no git still gets a coherent,
self-describing tool set; every advertised tool was called at least once in tests
(`xfail`/`skip` are explicit, and the win-only paths are covered by rendered-payload
tests that need no Windows).

**What P1 deliberately does not do.** No semantic (embedding) routing; no tool
synthesis at runtime; no per-tool rate limits; no OS-level sandbox (see Non-goals).

---

### P2 — Skills subsystem and dynamic tool synthesis

**Goal.** The agent can *extend* the toolset mid-task: a skill that ships a script
becomes a callable tool, and the registry learns it without a restart.

**Deliverables.**

- Compile `tool.toml` declarations into real handlers: `script`-backed tools bind to
  `shells.run` with the skill's `entry` script, an argument mapping (`--flag {name}`,
  `$ARG_json`, stdin-JSON for PowerShell), and a `returns: json|text|lines` contract.
- `skills.install {dir|git_ref}` / `skills.uninstall`, with a `--dry-run` that reports
  the tool ids, requirements and the exact argv that would run.
- Skill-scoped tool namespaces (`skill.<name>.<tool>`) plus `registry.alias` so a skill
  can shadow a built-in only when `tools.override_builtin = true`.
- `skills.match` upgrades from lexical to the P5 router interface (same call shape, so
  nothing else changes).
- Two new reference skills written against the finished contract: `vcs-git-safely`,
  `python-env-bootstrap` (venv + lockfile detection, pwsh and bash variants).
- Hot-reload via `watchfiles` behind `tools.hot_reload`, emitting `tools/list_changed`.

**Design notes.** A skill-authored tool is a *manifest plus a subprocess*, not
executed Python: the sandbox, budget and journal apply unchanged, and a broken script
fails as `NONZERO_EXIT` with the tail attached. Synthesized tools default to
`risk: "write"` unless the manifest says otherwise, and never to `destructive`.

**Acceptance criteria.**

1. `skills.install` on a fixture skill → `registry.list` includes `skill.demo.wordcount`
   within the same process, `advertise().digest` changes, and a `tools/list_changed`
   notification is observed by the stdio test client.
2. Calling it runs the script through `ShellRunner` with `env_mode: clean` and returns
   parsed JSON in `data`; a non-zero exit yields `NONZERO_EXIT` with `stderr_tail`.
3. A skill whose script does not exist yields a load error, not a callable-but-broken
   tool; `skills.list {errors}` shows the path and reason.
4. Uninstall removes the tools, re-advertises, and refuses while a job from that skill
   is running (with the job ids in `details`).
5. `pip install`-free: no new mandatory dependency; `watchfiles` stays an extra.

**Exit gate.** An agent can author a skill (write `SKILL.md` + `scripts/x.ps1` +
`tool.toml` through our own `fs.*`/`shell.run`), load it, and use the resulting tool to
complete a task — demonstrated in `tests/test_skill_synthesis.py` end-to-end, on both a
POSIX and a PowerShell dialect path (the latter via a fake profile + rendered-payload
assertions until a Windows runner exists in CI).

**Risks.** Script-to-schema mapping is where the complexity hides → keep the mapping
surface tiny (three bindings) and refuse anything else with a clear error. Trust: a
skill can run arbitrary commands → P3's policy engine must land before `skills.install`
is enabled by default (`skills.allow_install = false` until then).
**Effort: 8–11 days.**

---

### P3 — Policy, safety, reversibility

**Goal.** Make "the agent will not wreck this machine" a checked property rather than a
hope, and make the damage-reversible cases reversible.

**Deliverables.**

- Policy engine as data: `allow` / `deny` / `escalate` / `rate_limit` rules with
  per-pattern matchers (path globs, argv prefixes, host:port for future network tools),
  evaluated in `Engine._authorize` before any handler; deny stays non-overridable.
- Per-tool rate limits (`fs.delete` ≤ 20/min) and a `mutations` circuit breaker that
  returns `BUDGET_EXCEEDED` with a `summarize_and_stop` next action.
- Path provenance: `fs.*` results echo `via` (which root matched, symlink hops) and
  `sandbox.follow_symlinks` gets a real test on a host that supports links.
- Deletion tiers: `fs.trash = "journal" | "os-trash" | "delete"`; `os-trash` uses the
  platform recycle bin (PowerShell `Shell.Application` / `gio trash`) with the journal as
  a second copy.
- Undo hardening: `fs.undo {expect_sha}` refuses to roll over an edit it did not make
  (today it warns — the warning is the P1 contract); `fs.redo` for the last undone entry.
- Approval UX for the autopilot: `policy.grant` (already present) + a
  `receipt` for every grant so the ledger shows who approved what, and
  `ApprovalRequired.prompt_payload()` gains `diff_preview` for write-risk tools.

**Acceptance criteria.**

1. A deny rule blocks `shell.run` with a matching argv prefix *even when* the caller
   passes an approval token; the error names the rule (already true for `fs.*`, extended
   to argv/env matching).
2. Rate limit test: 21 `fs.delete` calls in a burst → the 21st is
   `BUDGET_EXCEEDED`, `details.exceeded` names the rule, and the tool is *not* executed.
3. `os-trash` puts a file in the recycle bin and the journal entry survives a restart;
   on a host without a trash API the call reports `UNSUPPORTED_PLATFORM` and deletes
   nothing.
4. `fs.undo {expect_sha}` with a stale sha → `CONFLICT`, file untouched; without
   `expect_sha` → unchanged P1 behaviour plus the warning (regression-pinned).
5. Property test: for every mutating tool, `read_only` or a deny rule means zero writes —
   asserted by diffing a snapshot of the workspace before/after a scripted burst.

**Exit gate.** `docs/SECURITY-MODEL.md` matches the code line for line (it is written
from it, not in advance), and the threat-model table has a test id per row.
**Risks.** Policy that reads as advice gets ignored by agents → every refusal must
carry the rule text and the fix. Effort saved by refusing to build an OS sandbox (see
Non-goals); state that plainly in the docs. **Effort: 7–9 days.**

---

### P4 — Autopilot integration

**Goal.** The loop that runs this thing has everything it needs: budgets it can see,
jobs it can await, receipts it can replay, and a way to grade itself.

**Deliverables.**

- `toolkit.plan(task)` → a ranked shortlist of tools, matched skills, the exact
  budgets to charge, and the `sk call` invocations that reproduce the turn (replay).
- Budget governor: per-task `CallContext` created from config + the loop's remaining
  tokens; `metrics.est_tokens` charged against it; `budget.exhausted` reported as a
  first-class field so the agent's "should I summarize now?" branch is a lookup, not a
  guess.
- Async jobs as a first-class turn shape: `shell.run {background: true}` returns
  `{job_id, next_call: shell.job_wait}`; add `shell.job_watch {job_id, until: pattern}`
  (poll-until-match, capped), and `jobs.list` in `sk jobs`.
- Receipts: every call's ledger row gets `context_receipt` — what was withheld
  (`exposed_results`, `withheld`, `stop_reason`) mirroring the discovery-receipt idea
  from the MCP gateway registry, so an agent can see *why* it never saw a tool.
- Replay/eval harness: `sk replay <run_id|task>` re-executes a recorded sequence in a
  scratch copy (read-only fs, `env_mode: clean`) and diffs envelopes; `sk eval
  --suite tests/eval/*.jsonl` scores task success, calls-per-task, tokens-per-task,
  refusal-then-recovery rate.
- Streaming: `notifications/message` (log) per tool call at `--log-level debug` for
  hosts that render it, plus `progress` tokens for long searches.

**Acceptance criteria.**

1. `sk replay` on a recorded 12-step refactor reproduces the same `data` for every step
   except timestamps/`run_id` (normalization is explicit, not fuzzy).
2. A budget-exhausted run terminates with a `summarize_and_stop` next action and the
   ledger shows exactly one row per call (already enforced; asserted across the replay).
3. `shell.job_watch` on a build that prints `OK` returns within the cap and reports
   `matched_line`; on a job that never prints it, returns `timed_out: true` and leaves
   the job running (it does not kill what it was only watching).
4. `eval` suite: ≥ 20 scripted tasks (rename a symbol, add a dependency, split a file,
   fix a CRLF-corrupted file, find a secret leak, background a long build, undo a bad
   batch edit) with per-task assertions on `ok`, `data` and the absence of
   `warnings`; median calls/task ≤ 6 on this box.

**Exit gate.** The autopilot team accepts `plan()` + receipts as the integration
surface and stops calling tools through ad-hoc glue. **Risks.** Replay fidelity is
easy to over-promise: anything that reads wall time or host state must declare
`stateful` and be excluded from strict diff. **Effort: 9–12 days.**

---

### P5 — Scale and discovery

**Goal.** 32 tools become 200 without the host drowning: routing, ranking, and a tool
list that changes underneath it safely.

**Deliverables.**

- Two-stage router: deterministic lexical shortlist (already the core of
  `registry.search`) → optional embedding stage (`semantic.*` extra) with
  `registry.route {task, k}` returning scores *and* reasons; exact-name matches always
  win, and the deterministic path stays available offline (one flag: `tools.semantic =
  false`).
- Provider ranking: capabilities with multiple providers (search, patch, archive,
  vcs) get selected by `registry.stats` reliability × latency × host capability, with
  `provider_receipt` in every result (which one answered and why).
- Advertisement tiers: `core` (always), `task` (selected by `plan()`), `full`
  (on `registry.expand`), each with its own token budget; `tools/list_changed` fires on
  tier changes, `list` gains `cursor` pagination (already modelled in `next`).
- Multi-server aggregation: `mcp.client` connector so other servers' tools appear in our
  registry as `remote.<server>.<tool>` with pass-through envelopes, `risk` inherited,
  `reversible: false`, and `stateful: "host"`.
- `capabilities.explain {capability}` — why a tool is gated here, with receipts.

**Acceptance criteria.**

1. With 200 registered tools, `tools/list` on tier `core` stays ≤ 20 tools / ≤ 1.2 k
   tokens, and every task in the eval suite still resolves its tool from tier `task`
   alone (asserted by `registry.route` top-k hit-rate ≥ 0.9 on the suite).
2. Turning the semantic stage off changes no call outcomes — only ordering
   (property test over the eval suite).
3. A `tools/list_changed` round-trip: drop-in a tool → the stdio client sees the
   notification and the new `digest` within one refresh; no notification when the
   advertisement is unchanged (already true; extended to tier changes).
4. Aggregation: a fixture second server contributes 3 tools; a denied remote call
   returns the *remote* envelope's error code, not a wrapper; `registry.stats` keeps
   remote and local rows separate.
5. Selection honesty: with `rg` faked absent, `fs.search` reports
   `metrics.provider == "python"` and `warnings` naming the fallback (never a silent
   provider switch).

**Exit gate.** No host is ever handed a tool that fails because of a gate we could have
predicted. **Risks.** Rankings can silently deprioritise the correct tool → every
ranking decision is exposed in `provider_receipt` and asserted in tests.
**Effort: 10–14 days.**

---

### P6 — Distribution and hardening

**Goal.** Others can install, run, and debug this without reading the source.

**Deliverables.**

- Packaging: tagged GitHub releases with wheels + sdist (built in CI), a
  `pipx`-installable console script set (`sk`, `skeletonkey-mcp`), and a
  zero-dependency core guarantee enforced by a test that imports `skeletonkey.core` with
  the venv's `site-packages` hidden (ADR-0001's promise, checked).
- Diagnostics: `sk doctor` (config layers, roots, probe receipts, gate diffs, ledger
  integrity, spill dir, skill load errors — one JSON blob an operator can paste),
  `sk doctor --fix` limited to safe moves (create state dir, refresh profile).
- Docs site in-repo: this plan, the four contract docs, a "write a skill" tutorial, and
  a "connect to host X" page (Claude Desktop / generic stdio clients / our autopilot).
- Security pass: dependency audit in CI (`pip-audit` on the extras only, since core has
  none), fuzz-ish tests for the sentinel parser and path normalization (property tests
  over random path shapes), red-team list of bypass attempts as executable tests
  (`..`, absolute-external, symlink escape, device names, `\\?\`, env injection,
  CLIXML spoofing, spill-path traversal).
- Windows CI runner turns `@pytest.mark.win` from skip to real: CLIXML decoding,
  CRLF round-trips, pwsh strict mode, long paths, recycle-bin deletion.

**Acceptance criteria.** Clean-checkout `pip install -e .[dev]` then `pytest` passes on
ubuntu-latest and windows-latest, Python 3.11 and 3.13; `sk doctor` output is stable
JSON with a documented schema; every bypass test either passes or is a filed bug with
severity; release artifacts installable from the release page with no network beyond
PyPI.

**Exit gate.** Version + changelog + migration notes for any envelope change (semver on
`__version__`, `manifest.version` per tool). **Risks.** Windows-only bugs resurface
because the runner is flaky → keep `win` tests deterministic: no downloads, no
`pwsh` install steps (the image has it), skip-with-reason instead of retry-loops.
**Effort: 6–8 days.**

---

### P7 — Frontier

**Goal.** The same agent brain, more hands.

- **Remote targets**: `target.ssh` / `target.docker` adapters — `fs.*` and `shell.*`
  against a remote root (SFTP or `ssh cat` streaming; `docker cp`/exec), with per-target
  capability probes so "this container has no rg" degrades the same way a host does.
  Journal stays *local* (before-images fetched into the state dir) so undo works without
  remote write access.
- **Self-authored tools**: the agent writes `tool.py` + manifest, the toolkit registers
  it in a *quarantined* mode (subprocess-only, `env_mode: clean`, timeouts halved, no
  `fs.write` outside its own scratch dir) and only promotes it after N successful calls
  (`tools.selfauthored = "quarantine" | "off" | "trusted"`).
- **Bridging out**: expose our engine as a tool-provider for other agent frameworks
  (OpenAI/Anthropic function-calling JSON export from the same manifests; a
  `sk export openai-tools` command) so the manifest is the single definition.
- **Agents-as-tools**: a `subagent.run` tool whose child gets a *restricted* tool set
  (tier `task`, `read_only` default) and returns a compact envelope + its ledger tail.
- **Parallel execution**: `plan()` returns a DAG of independent calls; the engine
  executes `parallel_safe` batches with per-call budgets (already tracked), and the
  journal serialises mutations to the same path (last-writer-wins is refused, not
  applied silently).

**Acceptance criteria.** One non-trivial task (multi-file refactor + test run)
completed against a `docker` target with the same tool calls as local, undo working
after the container is stopped; a self-authored tool that mutates outside its scratch
dir is blocked by the quarantine gate with `SANDBOX_VIOLATION`; `sk export openai-tools`
output validates against the provider schema and round-trips `registry.describe`.

**Risks.** Remote reversibility is a research problem, not an implementation one — if
before-image latency exceeds the task's budget, ship read-only remote targets first.
**Effort: exploratory, 15+ days, spike before committing.**

---

## 6. Testing strategy

| Layer | Where | Notes |
| --- | --- | --- |
| Unit (contracts) | `test_core_contracts.py`, `test_envelope.py`, `test_ledger_redaction.py` | shapes, budgets, redaction, chain integrity |
| Registry/config | `test_registry_config.py` | advertisement gates, digests, config layers + coercion notes |
| Engine/policy | `test_engine_policy.py` | every failure code, approval grants, deny walls, dry-run |
| Filesystem | `test_fs_ops.py`, `test_sandbox.py` | EOL/encoding/BOM table incl. BOM-less UTF-16, patch strategies, path escapes |
| Journal | `test_journal.py` | before-images survive restart, mode+mtime restore, never rmtree, pruning reclaims disk |
| Shells | `test_shell_runner.py`, `test_dialects.py` | live bash + rendered payloads for pwsh (no Windows needed) |
| Skills | `test_skills.py` | frontmatter edge cases, one bad skill cannot hide the rest, bodies never promise missing tools |
| Tools | `test_tools_builtin.py` | the whole surface through the engine, as a host drives it |
| Wire | `test_mcp_stdio.py` | raw JSON-RPC subprocess: handshake, list, call, error paths, prompts, resources, clean exit |
| Docs | `test_docs.py` | every documented call shape, tool/knob name and error code resolves against the live registry |

### Pipeline spec (not committed as a workflow yet)

Four jobs; the exact commands, so whoever writes `.github/workflows/ci.yml` is transcribing
rather than re-deciding:

| Job | Matrix / runner | Steps |
| --- | --- | --- |
| `core-constraint` | ubuntu-latest, py3.11 | `pip install -e ".[dev]"`, then a stdlib-only import check that **fails if `mcp`, `mcp_types`, `pydantic`, `watchfiles` or `jsonschema` is importable**, then `pytest tests/test_core_contracts.py tests/test_envelope.py tests/test_ledger_redaction.py tests/test_dialects.py`. This is ADR-0001 enforced, not ADR-0001 asserted. |
`slow` is registered in `pyproject.toml` and currently marks **nothing**: the two
process-spawning files (`test_shell_runner.py`, `test_tools_builtin.py` — 95 tests) cost
~10 s of the ~19 s suite, which is cheap enough to keep inside the default gate, and a marker
that quietly excluded them from CI would be a worse gate. The
`-m "not slow"` in the commands exists so a future stress/E2E suite can opt out without
editing the pipeline spec.

| `test` | ubuntu-latest + windows-latest × py3.11 + py3.13 | `pip install -e ".[dev,mcp]"` → `ruff check .` → `pytest -q -m "not slow" --tb=short` → `sk describe` (prints what that host advertises, so a gating regression shows up as a diff in a log line) |
| `smoke` | ubuntu + windows, py3.11, **`.[mcp]` only** (no dev extra) | `sk --version`, `sk profile`, `sk tools list`, `sk skills list`; then a real turn through the CLI — `fs search`, `fs patch` with an edits file, `grep` the patched file, `fs write` from stdin, `sk shell "echo sentineled" --dialect bash`; then pipe `initialize` + `notifications/initialized` + `tools/list` (protocol `2025-06-18`) into `python -m skeletonkey.mcp --cwd <tmp>` and assert `fs.patch`/`shell.run` came back. All four commands were run locally on Linux before this branch was pushed. |
| `audit` | ubuntu-latest, `continue-on-error` | `pip-audit` after `pip install -e ".[all]"`; the core declares nothing to audit, so this is advisories in the extras only, and it must not block a docs PR until the floor is pinned in a lockfile |

`windows-latest` starts as `continue-on-error: true` and becomes a gate in P6. That is not
dodging Windows: most POSIX-only tests self-skip on *environment* (`/bin/bash` missing)
rather than on a platform marker, and every PowerShell claim has a rendering-level test that
runs anywhere — so the value of the windows job is finding where the two disagree.

Rules that keep the suite honest: tests assert on behaviour, never on internals; any
Windows-only path is *either* covered by pure-rendering assertions or marked `win` and
run in CI; a test that cannot fail without the code changing is deleted; `xfail` is
allowed only with a reason string (`xfail_strict = true`).

**Eval metrics (P4+, tracked per run).** task success rate · calls per task ·
`est_tokens` per task · refusals-then-recovery rate · undo usage rate · share of calls
served from cache · provider fallback count · zero-result search count (a proxy for
"the agent gave up too early") · count of results that carried a warning (a proxy for
silent-ish failure).

---

## 7. Risk register

| # | Risk | Impact | Likelihood | Mitigation | Owner |
| --- | --- | --- | --- | --- | --- |
| R1 | Tool set grows past what fits a context window | agent picks wrong/none | high | tiered advertisement + token budget + `registry.search` (`advertise_max_tools`) | P1 done, P5 |
| R2 | Host lies about capabilities / probe is wrong | failed calls | med | probe receipts in every gate error; `profile.probe {force}`; refresh diff → `list_changed` | P1, P5 |
| R3 | An irreversible bulk delete | user data loss | low-med | journal-before-write, `confirm_destructive`, deny list, undo_task; P3 trash tiers | P1 done, P3 |
| R4 | PowerShell/Windows differences discovered late | broken on half the fleet | med | pwsh in the dialect core, CLIXML handling, `win`-marked tests + Windows CI (P6) | P1, P6 |
| R5 | Secrets leak into ledger/spill/context | credential exposure | med | redaction on previews + args, `result_digest` over pre-redaction bytes, deny on `.env`, session env names only; pattern table is tested | P1 done, P3 audit |
| R6 | Skills execute arbitrary code | RCE-by-design | med | P2 script-backed tools only (subprocess, sandboxed, budgeted); `skills.allow_install=false` until P3 policy lands | P2, P3 |
| R7 | Envelope changes break the autopilot | silent misparses | med | envelope is versioned + schema'd; `manifest.version` bumps; replay diffs in CI | P4, P6 |
| R8 | Engine becomes a god-object | velocity loss | med | stages are separate methods, handlers injected by signature; contract docs forbid shortcuts | ongoing |
| R9 | Idempotency cache serves stale state | wrong decisions | low | cache only `idempotent ∧ ¬mutating ∧ stateful=="none"`; `metrics.cached` always visible | P1 done |
| R10 | Upstream MCP SDK churn | transport breakage | high (observed) | SDK pinned `mcp>=2.1,<3`, adapter is the only file that knows it; wire tests speak raw JSON-RPC so they outlive SDK renames | P1 done |

---

## 8. Non-goals

- **Not** an OS sandbox: no seccomp/nsjail/Job Objects. Path roots, deny lists and
  timeouts are a *policy*, not containment; say so in every threat model.
- **Not** a browser/computer-use layer, and not an orchestration framework: the loop,
  prompts, and model choice live in the autopilot.
- **No** multi-tenant server, no auth layer, no per-user quotas (single-user, local).
- **No** database/warehouse tools before P7; `fs` + `shell` cover the 90 % case.
- **No** TypeScript/Node port: Python is the implementation (ADR-0001).
- **Not** a general-purpose workflow engine — if P4's `plan()` starts looking like one,
  it has failed.

---

## 9. ADR index

| ADR | Decision | Status |
| --- | --- | --- |
| 0001 | Python core with zero mandatory dependencies; MCP is an extra | accepted |
| 0002 | Render shell payloads; never rewrite the user's script | accepted |
| 0003 | One sentinel protocol for all dialects, token-random per call | accepted |
| 0004 | Hand-rolled JSON-Schema subset instead of `jsonschema` | accepted, monitored |
| 0005 | Never fabricate a default: report what is unknown (`sniff`, empty search, gates) | accepted |
| 0006 | Journal-and-undo in the toolkit, not delegated to git | accepted |
| 0007 | Values are `argv`, never interpolated text; quoting is a separate explicit tool | accepted |

`docs/adr/` holds the full text of each, with the options rejected and the observable
consequence (usually a test id).

---

## 10. Order of work from here

**Step 0 — shipped:** `shell.run {argv: [...]}` and `shell.quote {args[], dialect, shape}`.
`argv` goes straight to `execve`/`CreateProcess`, so values in it never meet a shell parser
(`$HOME` stays `$HOME`, `*glob*` stays literal, `it's` needs no surgery); `shell.quote`
covers the residual case of a value that must live *inside* the program text. Validation
refuses non-strings (a `True` in a path is silent corruption), more than 128 entries, and
NUL bytes. `docs/SHELL-DIALECTS.md` §"Arguments and quoting" is the norm, and embedding a
token in a real script is a test, not a claim.

**Step 0b — shipped:** a docs-lint test, because a roadmap that describes code it has not
read is a claim. `tests/test_docs.py` resolves every documented `tool.id {prop: …}` call
against the live registry, checks every bare `` `fs.x` ``-style name against tools / probed
capabilities / config keys, checks every ``| `CODE` |`` table row against `E`, and requires an
inline phase citation for any tool the roadmap still owes. It found **ten** real drifts in
its first run: `shell.wait`, `shell.kill`, `shell.job_status` and `shell.job_kill {signal}`
(in SKILLS-SPEC) never existed; `shells.run` and `fs.chmod` were cited as tools; `fs.roots`
and `fs.max_read_bytes` were config keys that are actually `roots` and `budget.max_read_bytes`;
`fs.journal_summary` is `fs.journal_list`; and README's tool counts were stale. A second run
found more (see its own docstring). Unreferenced citations fail too, so the register below
cannot quietly rot.

**Step 0c — next free day:** expose `fs.chmod` as a tool. `fsx/ops.py` already preserves
modes through `fs.move`/`fs.write`, but agents have no first-class way to set permissions, so
they shell out — and on ACL-only Windows hosts the honest answer is an envelope with the
`icacls` recipe rather than prose.

**Then the phases, in order:** P2 (skill synthesis, whose handler-body rule already exists in
`docs/SKILLS-SPEC.md` §Constraints and depends on step 0) → P3 (policy, rate limits, trash,
`fs.redo` — before an install path is enabled by default) → P4 (autopilot integration:
`plan()`, receipts, replay, evals; this is where the loop stops hand-coding retries) → P5
(discovery at scale) → P6 (distribution; Windows CI *before* P7, or remote Windows work will
burn the budget) → P7 (frontier spike: one remote Windows host through one transport).

Two standing rules for every phase: each new tool ships with a manifest section in
`docs/TOOL-CONTRACT.md`, an entry in the skill guidance that agents will read, and a
wire-level test. A feature that only works when called from Python is not done.
