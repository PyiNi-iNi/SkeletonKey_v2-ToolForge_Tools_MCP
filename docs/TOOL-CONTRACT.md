# Tool contract (normative)

Every tool — built-in, drop-in, entry-point, skill-authored, remote (P5) — obeys this
document. Where this and the code disagree, the code is wrong (or this is stale, and the
tests in `tests/test_core_contracts.py` decide which).

## 1. Signature

A handler is a plain function. The engine injects by *parameter name*, so a handler takes
only what it needs:

```python
def fs_patch(path: str, edits: list[dict], *, expect_sha: str | None = None,
             dry_run: bool = False, ctx: CallContext, fs: Fs, journal: FsJournal) -> dict:
```

| Injected name | What you get |
| --- | --- |
| `ctx` | `CallContext`: `run_id`, `task_id`, `session_id`, `cwd`, `granted`, budget counters, `trace_id` |
| `engine` | the `Engine` (for `engine.call` composition — see §7) |
| `fs` / `journal` / `shells` | the sandboxed filesystem, the change journal, the shell runner |
| `dry_run` | **the effective preview flag**: set by `engine.call(..., dry_run=True)` *or* by the caller passing `dry_run: true` in args |

Anything else comes from validated `args`. Never read `os.getcwd()`, `Path.cwd()` or
`open()` on a caller-supplied path: resolve through `fs`/`sandbox` so roots, deny and
symlink policy apply.

`task_id`: mutating tools must pass `ctx.task_id` into the journal so `fs.undo_task`
can retract a whole turn.

## 2. Return value

Return a JSON-able `dict` (or list/str) — the engine wraps it in `ToolResult` with
`ok: true`. Raise `SkeletonKeyError(E.X, message, details=…, hint=…, next_actions=…)` for
anything you can name; any other exception is mapped by `classify_exception` and
reported as `INTERNAL`, which is a bug report you wrote for yourself.

### The envelope (host-visible)

```jsonc
{ "ok": true, "tool": "fs.patch", "run_id": "01…", "data": {…},
  "error": null, "artifacts": [], "hints": [], "next_actions": [],
  "metrics": {"duration_ms": 4, "est_tokens": 120, "provider": "ripgrep",
              "cached": false, "undo_available": true},
  "context": {}, "warnings": [] }
```

- `ok: false` ⇒ `error` set; `data` may still be present *as evidence* (e.g. a shell's
  partial stdout). Never rely on the absence of `data` to mean "nothing happened".
- `metrics.est_tokens` is charged against `budget.task_max_tokens_out` before the host
  sees the result.
- `metrics.budget` is the task's budget position *after* this call: `spent`, `limits`,
  `remaining` (`null` = unlimited) and `exhausted`. The loop's "should I summarize
  now?" branch is a lookup on `exhausted`, not a guess: the call that crosses a cap
  reports `exhausted: true` in the same envelope, and the next call is refused with
  `BUDGET_EXCEEDED` plus a `summarize_and_stop` next action. `est_tokens` is larger
  than `budget.spent.tokens_out` by the budget block's own cost (it is estimated
  again after the block is attached).
- `CallContext.from_config(..., remaining_tokens=N)` lets a host carry its own token
  budget (an LLM's remaining context): the effective task cap is the *tighter* of
  config and `N`, so an over-optimistic config cannot spend the loop's money.
- `warnings` is for truthful-but-non-fatal facts (truncation, provider fallback,
  "content had changed since this entry wrote it"). A warning never fails a call, and a
  failure never hides inside a warning.
- Serialization is bounded by `budget.max_output_bytes`: over it, `data` becomes
  `{"inlined": …, "spilled": true, "total_bytes": N}` plus an `artifacts[]` entry whose
  `path` holds the **complete** payload and whose `fetch_rest` is a legal `fs.read`
  call. The payload is never cut mid-JSON.

### Path provenance (`via`)

Every `fs.*` result that resolves a path carries `data.via`: the provenance of the
resolution, so a host can see *where* a path landed without re-deriving it.

```jsonc
"via": { "root": "/ws",                                  // which declared root matched
         "symlink": { "hops": ["/ws/a", "/ws/b"],        // each link target, in order
                      "final": "/ws/b" },                // the fully resolved path
         "long_path": "\\\\?\\C:\\…",                    // Windows long-path rewrite, when one
         "notes": ["resolved through symlink to b"] }    // human-readable, when relevant
```

`via.root` is always present; the other keys appear only when they describe something.
The block is diagnostic, not a security decision — containment was already enforced
against the resolved target, and `follow_symlinks = "never"` is refused with
`SANDBOX_VIOLATION` before any result exists.

### Background jobs: a first-class turn shape

`shell.run {background: true}` returns `data.job_id` and `data.next_call` (the exact
`shell.job_wait` invocation), so a loop can branch on them without parsing hints. Two
ways to come back, answering different questions:

- `shell.job_wait {job_id, timeout_s, tail_bytes}` — "is it **done**?" Blocks until exit
  (or the cap) and returns tails + exit code.
- `shell.job_watch {job_id, until: <regex>, timeout_s, poll_s}` — "is it **ready**?"
  Blocks until a line of the job's stdout matches `until`; on a match returns
  `matched_line`, on the cap returns `timed_out: true`. A timeout **leaves the job
  running** — watching is not killing, and a watch never kills what it was only watching.
  A job that exits before printing the line returns `running: false` with its exit code,
  not a timeout. `until` must compile as a regular expression (`BAD_ARGS` if not).

### Streaming on the MCP wire

Two optional channels let a host that renders a live turn see what is happening *during*
a call, without polling. Both are notifications on the same stdio channel; a host that
does not render them simply never asked for them.

- **Per-call log (`notifications/message`).** With the server started at
  `--log-level debug`, every `tools/call` streams one log notification:
  `level: "debug"`, `logger: "skeletonkey"`, `data: {tool, ok, ms, tokens, error_code}`.
  The flag is the host's opt-in — below debug the channel stays quiet, and a logging
  failure never turns a successful call into a failed one.
- **Progress (`notifications/progress`).** A client that sends a `progressToken` in the
  request's `_meta` and calls a tool that can scan a large tree (`fs.search`, `fs.glob`)
  gets an immediate `progress: 0` acknowledgement, then pings of elapsed seconds while
  the scan is alive. The value is indeterminate on purpose: a tree walk's total is
  unknown until it finishes, and a made-up total would be a lie with a number on it.
  No token, no pings — progress is never unsolicited.

## 3. Errors

`error = {code, error_class, message, retryable, hint, details}`.

`error_class` ∈ `usage | policy | execution | environment | internal`. `retryable: true`
means "safe to retry unchanged" — anything with side effects is `false` even when the
cause is transient.

Codes an author must know how to produce:

| Code | When | details the host expects |
| --- | --- | --- |
| `MISSING_ARG` | required arg absent (incl. nested: `edits[1].old_text`) | `missing`, `at`, `schema` |
| `BAD_ARGS` | validation failed | `errors[]`, `schema`, `minimal_example` |
| `UNKNOWN_TOOL` | id not registered | `requested`, `suggested[]` |
| `TOOL_NOT_ADVERTISED` | gate withheld it | `gate.unmet[]`, `receipt[]` (probe command + rc) |
| `MISSING_BINARY` / `MISSING_SHELL` | probe says the host lacks it | `missing`, `fallback`, `available_dialects` |
| `SANDBOX_VIOLATION` | path/argv outside roots, denied, symlink escape | `path`, `why`, `roots` |
| `DENY_RULE` | a policy rule matched | `rule`, `advice` ("cannot be overridden") |
| `READ_ONLY_MODE` | mutating tool under `policy.read_only` | `tool`, `advice` |
| `APPROVAL_REQUIRED` | risk needs consent | `prompt` (replayable `approve_token`), `grant_options` |
| `BUDGET_EXCEEDED` | calls/mutations/tokens/wall-time cap | `exceeded[]`, `spent`, `limits` |
| `TIMEOUT` | handler/shell exceeded its timeout | `killed`, `next_action` (`background: true`) |
| `EEXIST` / `ENOENT` / `CONFLICT` | file semantics; `CONFLICT` = stale `expect_sha` | `path`, `actual_sha` |
| `PATCH_CONFLICT` / `AMBIGUOUS_MATCH` | patch anchors | `failures[]` with `at_lines` |
| `NOT_IMPLEMENTED` | declared but unbuilt (skill stubs), journal off | `config` key to flip |
| `UNSUPPORTED_PLATFORM` | the OS genuinely lacks it (chmod on ACL-only hosts) | `os`, `alternative` |
| `NONZERO_EXIT` | a process failed | `exit_code`, `stdout_tail`, `stderr_tail` |
| `REMOTE` | an upstream MCP server reported an error (non-SkeletonKey) | `server`, `remote_message`, `remote_code` |
| `DEPENDENCY_MISSING` | the `mcp` extra / a remote server's transport failed | `server`, `command` |
| `IO`, `INTERNAL` | everything else | `trace_id` |

Every refusal names the fix. "Permission denied" without an advice string is a bug.

## 4. Risk classes and approval

`risk` ∈ `none | read | write | destructive | network | privileged`; the manifest also
carries booleans (`destructive`, `reversible`, `idempotent`, `parallel_safe`,
`open_world`, `stateful`) that the engine, MCP annotations and the cache read.

Gate stage (`_guard_gate`): `tools.disable` → `policy.read_only` withholding → profile
gate. Authorize stage (`_authorize`), in order: **deny rules** → **escalation** →
**rate limits** → `dry_run` preview → approval (allow rules → approval token →
`auto_approve` → `read_only` re-check → approver callback). A throwing approver is
treated as `INTERNAL`, never as consent. `grant_options` are `once | task | session |
deny`; a grant becomes `grant:<tool>` in `ctx.granted` and the same token replays the
call.

## 4b. Policy rules and approval UX

Rules as data: `[[policy.rule]]` tables (grammar in `core/policy.py`) plus the legacy
`policy.deny` strings, `policy.escalate` list and `policy.rate_limits` map all compile
to one rule list; a malformed rule is reported in the engine's policy errors and
skipped, never guessed at.

- **Deny is the first thing read** in the authorize stage - before any token, grant or
  flag could be consulted - and stays non-overridable. A deny rule can key on the tool
  (id or glob), a path glob (`paths`, tested against path-ish args and their
  basenames), an argv prefix (`argv_prefix`, tested against the `argv` array arg -
  content that merely *mentions* a word is not a match), or env-var *names* (`env`
  globs over the keys of the `env` arg). On a `script`/`command` argument, `paths`
  globs additionally match the path-like tokens *inside* the text (`cat .env` is
  caught by `paths = ["**/.env*"]`) — but for deny/escalate rules only; allow rules
  never scan free-form content. Every denial carries `details.rule` (the rule
  text including its `reason`), `details.matched` (which argument, which pattern) and
  an advice string: a refusal without the rule and a fix is a bug.
- **Allow** removes only the approval requirement for the matched call shape; it never
  clears a deny, never overrides `policy.read_only`, and the decision is echoed in
  `metrics.policy_allow` so the receipt shows which rule said yes.
- **Rate limits** are sliding windows per tool (`policy.rate_limits`; default
  `fs.delete` at 20 per `rate_window_s` = 60). A call that crosses the limit is
  `BUDGET_EXCEEDED` *before dispatch* - the tool does not run - with the rule named in
  `details.exceeded`, `details.retry_after_s`, and a `summarize_and_stop` next action.
  Previews do not burn slots. A second breaker caps *successful* mutations per rolling
  60s per engine (`policy.max_mutations_per_minute`), whatever the per-task caps say.
- **Prompts show intent.** `APPROVAL_REQUIRED.details.prompt` carries `diff_preview`
  for write-risk tools: `fs.write` gets a unified diff against the current file (or a
  new-file marker), `fs.patch` gets its edits applied dry-run, and any other mutating
  tool with a `content` arg gets the head of it. The human approves intent, not a hash.
  Best-effort: a preview that cannot be computed is omitted, never fatal.
- **Grants are receipts.** `policy.grant {tool, scope}` records a grant in the calling
  task's context, returns `data.receipt` (`granted_by`, `tool`, `scope`, `task_id`,
  `session_id`, `ts`) and writes its own ledger row - the audit shows who approved
  what. A grant for a tool that itself requires approval is itself approval-gated, and
  the approver sees the target in the prompt (`args_preview` carries `tool` +
  `scope`): that is what closes the unattended self-grant hole. A grant for a tool the
  caller could already run is record-keeping and needs no ceremony. Grants live in the
  `CallContext`, so a `task` grant does not outlive the task.

## 5. `dry_run` is a promise

If your schema exposes a `dry_run` property, you are claiming the preview has **no side
effects**. Consequences the engine relies on:

- a call with `dry_run: true` (in args or as a flag) is not blocked by approval, and
  under `policy.read_only` a previewable mutating tool stays *callable* — that is what
  makes read_only a plan mode instead of a gag;
- a tool **without** a `dry_run` property cannot preview: the engine returns
  `READ_ONLY_MODE` with `details.plan` (the redacted args) and `would_write: true`;
- a preview result must say so in `data` (`dry_run: true`) and return the *real* plan
  (diff, affected paths) where available, because the host cannot tell "would create"
  from "created" by any other means.

Lying about this is a policy violation: deny the tool.

## 5b. Untrusted text in arguments

A tool that accepts free text and then *runs* something has to decide where that text goes.
The rule in this toolkit: **values are arguments, not program text.**

- `shell.run {argv: [...]}` appends to the process command line, so the values never reach a
  shell parser: no `$` expansion, no backtick execution, no globbing, no quoting bugs.
  Entries must be strings (numbers are stringified; `bool` and everything else is
  `BAD_ARGS`), at most 128, and NUL-free — `fsx`/`shells` validate before spawning.
- A value that must be *inside* the script body goes through `quote_arg` in
  `skeletonkey/shells/dialect.py`
  (exposed as `shell.quote`), which is correct only where one token is expected: not inside a
  double-quoted PowerShell string, not inside a here-doc, not as half of a JSON document.
- Large or structured input is a file (`fs.write`) or one `stdin_text`/`argv` element holding
  `json.dumps(obj)` — the forms that survive newlines, quotes and a Windows console alike.

Any new tool that shells out should follow the same shape: an argv list plus an explicit
`script`/template, never a string the caller assembled. `deny` rules cannot see inside a
script (`docs/SECURITY-MODEL.md` §Gaps), which is the security reason for a style rule that
is otherwise only hygiene.

## 6. Reversibility

Any mutation of the filesystem must journal first and return `undo_token` **at the top
level of `data`**, plus the replayable suggestion:

```python
data["undo_token"] = token
data["undo"] = {"tool": "fs.undo", "args": {"token": token}}
```

`fs.patch` returns the token both at top level and inside its nested `write` result; new
tools do the same. `reversible: true` in the manifest makes `metrics.undo_available`
appear, which is how a host offers "undo" without parsing `data`.

A metadata-only change journals the previous *bits* instead of a content copy
(the journal's `record_meta`), and undo restores exactly those and nothing else. If the mode
could not be read while recording, the entry carries `meta.undo_reliable: false` and undo
refuses with `CONFLICT` rather than restoring the dataclass default: "unknown" and `0o644`
are different facts, and confusing them opens a file somebody locked.

### Undo with a precondition: `expect_sha`

`fs.undo` accepts `expect_sha` (a sha256, full or the 16-char prefix that `fs.read`
returns): the undo proceeds only while the file still holds that content, otherwise
`CONFLICT` — the same precondition `fs.write`'s `expect_sha` enforces at write time,
applied at rollback time. Without it the P1 behaviour is unchanged: the undo proceeds
and appends a divergence warning when the file had moved on.

### Redo

`fs.redo {path?}` re-applies the most recently *undone* change, optionally limited to one
path, and journals the redo itself — the result carries a fresh `undo_token`, so undo and
redo can ping-pong. A redo is never a silent overwrite: a file that changed after the
undo, a path that was re-created, a destination that exists again, or a pruned
after-image is a `CONFLICT` that says which state broke. Entries that predate after-image
capture (or lost it to pruning) refuse the redo instead of guessing; a fresh
`fs.journal_list` shows what is still reversible.

### Deletion tiers

`fs.delete` honours `fs.trash`: `journal` (default) journals then hard-deletes;
`os-trash` journals *and* moves the path to the platform recycle bin (`gio trash`
on Linux/macOS, PowerShell `Shell.Application` on Windows), so the OS bin is emptied
without the change becoming irreversible; `delete` is hard and unjournaled. A
host with no trash API under `os-trash` gets `UNSUPPORTED_PLATFORM` and deletes
nothing — the refusal happens before anything is recorded, so there is no journal
entry for a deletion that never happened. The result echoes `mode` (and `trash`
for the os-trash tier).

## 7. Composition and caching

- Tools may call other tools through `engine.call` (that is how `registry.search` and the
  skill loader reuse the registry). Budgets, policy and the ledger apply per inner call;
  the ledger therefore holds one row per *tool* call, not per turn.
- Every ledger row carries a `context_receipt`: `exposed_results` (the advertised set the
  host could call at that moment), `withheld` (every registered tool it could NOT, with
  the gate's own reasons — the per-call mirror of the per-tool discovery receipt, so a
  replay or an eval can read after the fact *why an agent never saw a tool*), and
  `stop_reason` (`ok` or the error code). It is inside the hash chain.
- The idempotency cache (5 s TTL) applies only when
  `idempotent ∧ ¬is_mutating ∧ stateful == "none"`. A pure read that reflects live state
  (`shell.sessions`, `fs.journal_list`, `registry.stats`) must set `stateful: "session"`
  — otherwise a polling agent is served its own last answer and calls it fresh.
- `idempotency_key` (call-level) collapses retries of the same logical mutation; the key
  is part of the cache key and of `registry.stats` attribution, never a security control.
- A successful **mutation retires every cached read** (the cache key carries a mutation
  generation that bumps on each mutating call): the search → patch → search-again verify
  loop must read the new state, never the pre-patch answer served from cache.

## 7b. Skill-authored tools

A `[[tool]]` in a skill's `tool.toml` is compiled into a manifest plus **one script run** —
`docs/SKILLS-SPEC.md` is normative for the declaration keys. What a *caller* needs from this
document is the envelope, because it is `shell.run`'s envelope plus provenance:

| key in `data` | what it is |
| --- | --- |
| `result` | the parsed payload: JSON object for `expects = "json"`, a line list for `lines`, raw stdout otherwise |
| `argv` | the arguments actually handed to the process — echo of the binding, and the thing to paste back when reproducing |
| `args_via` | `flags` \| `argv_json` \| `stdin_json` \| `none`: which binding ran |
| `exit_code`, `completed`, `timed_out`, `truncated`, `stdout`, `stderr_tail`, `duration_ms`, `dialect` | identical meaning to `shell.run` §5 |
| `skill`, `skill_tool`, `script` | provenance: which pack, which declaration, which file (or `<handler_body>`) |
| `owner` | `skill:<name>` — also recorded on any background job, so `skills.uninstall` can refuse to delete a running job's script |

Errors a script can produce are the shell ones and nothing else: `NONZERO_EXIT` (with
`stderr_tail`, `stdout_tail`, `exit_code`, `completed` attached), `TIMEOUT` (retryable, kill-tree
already applied), `MISSING_SHELL` for a dialect the profile cannot run, `BAD_ARGS` from the
manifest's own schema. There is no `INTERNAL` path for a bad script, because a bad script is a
*result*, not a crash.

Two reservations worth knowing before you write a skill:

* `dialect` in a skill tool's schema means "which interpreter", and the compiler consumes it.
  A declaration that also pins `dialect` is refused at load time rather than silently
  overridden.
* a tool that wants `dry_run` to be honoured must declare the property itself. That declaration
  is the author's promise that the script writes nothing; the engine stops guessing, which is
  also why `policy.read_only` refuses a skill tool that did not declare it.

## 7c. Replay and eval (the autopilot integration surface)

`toolkit.plan(task)` is the loop's entry point: a ranked shortlist of tools
(deterministic lexical ranking - P5 adds the optional semantic stage), the skills
matched to the task, the exact budgets to charge, and a replayable `sk call`
invocation per shortlist row. The loop consumes `plan()` + the ledger's
`context_receipt` and stops calling tools through ad-hoc glue.

- **Recording.** `RunRecorder` appends full envelopes to a JSONL run recording,
  one step per line, and snapshots the workspace's *start* state to
  `<recording>.baseline` before the first step - a mutation run changes the tree,
  so a replay must start where the run started, not where it ended.
- **Replay.** `sk replay <recording|task>` re-executes the steps in a scratch copy
  of the baseline (the original is never touched) and diffs the envelopes.
  Normalization is **explicit, not fuzzy**: volatile keys (`run_id`, `trace_id`,
  `ts`, `started`, `elapsed_s`, `wall_s`, `duration_ms`, `est_tokens`, `bytes_out`,
  `mtime`, `atime`) are dropped wherever they occur; the workspace/state roots are
  rewritten to `<WS>`/`<STATE>`; journal `und_<hash>` tokens - per-call identities -
  are rewritten. A tool that declared itself `stateful` (session or host) is held
  to `ok` + error code only: its data may reflect live state, which is exactly what
  the declaration promised. Anything else is diffed byte-for-byte.
- **Eval.** `sk eval --suite tests/eval/*.jsonl` scores scripted tasks (one JSON
  object per line: `id`, `setup`, `steps`, `expect`). A step's args may reference
  an earlier step's data as `"$<step>.data.<path>"` - the only way a static script
  can use a `job_id` or token it can only know after the fact. The report scores
  task success, calls/task, tokens/task, and the refusal-then-recovery rate.

The ledger keeps exactly one row per call (asserted across the replay), and every
row's `context_receipt` records what the host could and could not call at that
moment - so after the fact, an eval can read *why* an agent never saw a tool.

## 7d. Publishing: the write-only store and placeholder injection

The `pub.*` group (nine tools) ships credentials to a publish without letting the
agent *see* them again. The contract, enforced by code and tested at the wire:

- **The store is write-only to the agent.** `pub.store_put {id, kind, value}`
  persists to a JSON file **outside the workspace roots** (default
  `<user config dir>/skeletonkey/publish/store.json`, mode `0600` best-effort,
  override `[publish] store_path`). The fs sandbox is the wall: `fs.*` tools
  cannot read or write the store at all. No `pub.*` tool returns a raw value —
  `pub.store_list`/`store_put` return metadata plus a short non-inverting mask
  (`ab…YZ(19)`). The only path a value leaves the process is `pub.inject`.
- **Secrets in args are redacted by name, declared on the manifest.**
  `pub.store_put` declares `secret_args: ["value"]`; the engine replaces exactly
  those keys with `***REDACTED***` before the ledger row is written (a second
  backstop: `redact_obj` also masks bare `value`-named keys). A test asserts the
  raw value appears in **no** ledger row.
- **Placeholders are `{{PUB.<id>}}`** (id grammar `[a-z0-9][a-z0-9._-]{0,63}`).
  `pub.placeholders {path?}` reports every occurrence with **exact
  file/line/column**, the bound store id, and `bound`/`missing` status, plus
  `ready_to_publish`. Files that policy denies are *skipped with a note*, not a
  fatal error — a protected file is a statement that the agent may not touch it.
- **`pub.inject` is a two-pass write with no partial publishes.** Pass 1 reads
  every file and plans every replacement; if any marker's store id is missing,
  it raises `ENOENT` (listing the missing ids) **before a single byte is
  written**. Pass 2 writes only changed files through `fs.write` with
  `expect_sha`, so each write is conflict-detected and journaled. `dry_run`
  returns the plan and writes nothing. `bindings` maps a marker id to a
  different store id (maps to stored credentials).
- **Undo scope is the call, not the session.** Every engine call carries its own
  `task_id`, so the result's `undo` block points at `fs.undo_task {task_id}` —
  reverts exactly this injection's files, nothing the agent did before. Per-file
  `undo_token`s are still in `data.written[]` for token-granular rollbacks.
- **The knowledge tools are static data, not memory.** `pub.platforms` /
  `pub.payments` / `pub.packaging` surface `skeletonkey/publish_data.py`
  (single source of truth: console/docs URLs, steps, credential kinds,
  placeholder examples). `pub.testers` returns a machine-executable release
  test plan — steps as tool calls or commands with acceptance lines and
  on-fail behavior — that references `{{PUB.<id>}}` placeholders and never raw
  secrets.

## 7e. Tiers, routing and discovery receipts

The P5a discovery contract: a host that never opts in sees exactly the surface it saw
before, and everything that could explain a "why didn't I see it?" is a *receipt*, never a
guess.

- **Tiers are a manifestation filter, not a security boundary.** Every manifest carries
  `tier` (`core` | `task` | `full`, default `full`). `core` is advertised in every tier,
  `task` in task + full, `full` only in full. The active tier is session state starting at
  `full` and is switched by `registry.expand {tier}`; read it back from `registry.list`
  (`tier`/`active_tier` in the payload) or `sk tools list`. A tool removed by tier is
  still *registered*: `registry.describe` sees it and reports the tier that hid it, and
  calling it still works — the tier governs advertisement, never authorization.
- **Per-tier budgets.** `[advertise]` (`core_max_tools`/`core_max_tokens`, `task_*`,
  `full_*`; 0 = no cap) bounds each tier. Budget drops are greedy by ascending
  `ProviderStats` score and are recorded in `AdSnapshot.budget_drops` with the reason —
  the real `registry.stats` / `provider` of the tool that lost its slot, not a
  placeholder. An explicit per-call `token_budget` still wins over `tier_budgets`.
- **`registry.route` is the two-stage router.** `route {task, k, semantic}`: an exact
  name (tool id, `mcp_name`, dotted/underscored/slashed forms) always wins first, then the
  deterministic lexical ranking, every hit carrying `reasons` (which field matched which
  token, capped at 6) plus `tier`/`provider`. The semantic stage is a registered
  `SemanticBackend` (entry-point group `skeletonkey.semantic`); with no backend installed
  and `tools.semantic = false` (default) `route` is lexical-only and *says so* —
  `mode: "lexical"`, `backend: null`. `semantic=true` with no backend is identical to
  lexical (same ids, same order) plus an honest `note` — never a silent no-op.
- **Provider receipts ride everywhere.** Every capability race notes the winner in
  `AdSnapshot.selection_receipts` (capability → winner id, `provider`, score, `why`,
  competitors with scores — a competitor with zero calls is reported as `no call
  evidence`, not guessed). The receipts appear in `registry.list` rows, MCP `tools/list`
  `_meta` (`sk.selection_receipts`, `sk.budget_drops`, `sk.selected_providers`,
  `sk.tier`, `sk.digest`) and per tool `_meta.sk.provider_receipt`.
- **`capabilities.explain {capability}`** answers "why didn't I see this tool?": all tools
  claiming the capability (or the capability of a given tool id) with each one's gate
  reasons, score, tier, live stats, the winner and why — a missing or unknown capability
  is a `BAD_ARGS` with `near` suggestions, never empty silence.
- **Pagination is opaque by position.** `registry.list` and MCP `tools/list` accept
  `cursor` / `page_size` (server default 100) and return `next_cursor`/`nextCursor` only
  when more rows exist; a malformed cursor falls back to page 0 (documented, tested); a
  host that never sends a cursor gets the whole small surface in one page, exactly as
  before.
- **`tools/list_changed` is digest-driven.** The MCP `_meta.sk.digest` is a hash of the
  advertised set. After a tool call that can change the set (`registry.expand`,
  `skills.install`, `tools.enable`, a profile refresh) the adapter pushes
  `notifications/tools/list_changed` on the session that made the call — the next
  `tools/list` on that session already carries the new set, and unchanged advertisement
  never re-notifies. Hosts that only poll `tools/list` still converge.

## 7f. Remote MCP servers (`mcp.remotes`, ADR-0013)

Other MCP servers join the surface as tools without their own adapter:

- **Config is explicit.** `[mcp.remotes.<name>]` with `command` + `args` (stdio) or
  `url` (streamable-http), `enabled`, `timeout_s`. Names match `[a-z0-9][a-z0-9_-]{0,31}`;
  exactly one of command/url; unknown keys and malformed specs are config errors —
  never ignored. There is no auto-discovery and no env-var implicit server.
- **Identity is honest.** `remote.<server>.<tool>`; `group: "remote"`;
  `source`/`provider: "remote:<server>"`; `capability` = the tool's own id (no provider
  race with local tools — a remote `fs.search` and the local one both stay callable by
  name).
- **Risk is inherited, never lowered.** `readOnlyHint: true` ⇒ `risk: "read"`;
  `destructiveHint: true` ⇒ `risk: "write"`; **absent ⇒ `risk: "write"`** (approval gates
  an unannotated foreign call). `reversible: false` (its mutations are outside our
  journal), `stateful: "host"` (the remote owns state — never `none`), `idempotent:
  false`, `parallel_safe: false`, `tier: "full"`.
- **Errors pass through.** A skeletonkey-shaped remote result returns exactly the
  remote's envelope: its `error.code` (e.g. `BAD_ARGS`), message, hint and `details`
  arrive untranslated — the outer envelope only renames the tool for attribution. A
  remote error that is not skeletonkey-shaped maps to `REMOTE` with `server` +
  `remote_message` (+ `remote_code` when the server sent one); a transport/probe
  failure maps to `DEPENDENCY_MISSING` with `server` and `command`. Never `INTERNAL`.
- **Enrollment is visible.** A server that fails to connect, handshake or list tools is
  a `load_errors` entry (with `server`, `stage`, and the reason) and a row in the build
  report — as a host you see *why* `remote.<server>.*` is absent, and no remote tool is
  ever advertised without its server having answered `tools/list`. Disabled servers
  are reported, not skipped.
- **Trust boundary.** Remote calls pass through the local gate/policy/approval/budget/
  ledger *around* the call (attribution, budget, audit) but not *inside* it — the remote
  server enforces its own policy. A remote tool is only as trustworthy as its server:
  this is an aggregation layer for infrastructure, not a security boundary.
- **Stats stay separable.** Every `registry.stats` row carries `source`
  (`builtin` / `remote:<server>` / a drop-in's `source`); `registry.stats
  {source: "remote:<server>"}` filters, `stats_by_source` groups, and
  `registry.stats` (no args) returns the grouped view under `by_source`.

## 7g. Sandbox workspaces (`skills/sandbox` pack)

The `sandbox.*` tools are a *skill pack* (source `skill:sandbox`) that creates and manages
**isolated scratch workspaces**: a named project directory seeded from a template, optionally
its own Python venv, and commands run inside it with a cleaned environment. They run as
subprocesses like every skill tool (§7b), so their envelopes are the same; the pack's own JSON
result carries an inner `ok`/`error` for cases that are not shell failures (a `CONFLICT` on
creating over a non-empty dir, a `NOT_A_SANDBOX` when a `path` has no manifest, a `BAD_ARGS` on
an unsafe `name`).

| tool | risk | purpose |
| --- | --- | --- |
| `sandbox.create {name, template, make_runtime, packages, ...}` | write | scaffold `<root>/<name>` from a template, record it in `.sandbox/manifest.json` |
| `sandbox.runtime {path, python_version, packages}` | write | provision/refresh the sandbox's isolated `.venv` (own interpreter + site-packages) |
| `sandbox.run {path, argv, timeout_s, use_runtime}` | write | run a command with cwd inside the sandbox and its venv first on PATH; hard timeout + output cap |
| `sandbox.status {path}` / `{root}` | read | deep view of one sandbox, or inventory of every sandbox under `root` |

Result keys of note: `sandbox.create` returns `path`, `files_written`, `runtime`, `git`;
`sandbox.run` returns `exit_code`, `timed_out`, `truncated`, `stdout`, `stderr`, `duration_ms`,
`used_runtime`, plus `cwd` (always inside the sandbox). `sandbox.status` returns per-sandbox
`files`, `bytes`, `runtime.venv_present`/`python_version`, `runs`, and a `log_tail`.

Isolation honesty: these tools isolate a scratch *directory, interpreter and environment* —
they are not an OS/network sandbox. Each result and the `skills/sandbox` guidance therefore
route teardown through the journaled `fs.delete {path, recursive: true}` (undoable with
`fs.undo {undo_token}`), never `rm -rf`.

## 8. Adding a tool

| Where | How | Advertised? |
| --- | --- | --- |
| built-in | `tools/builtin.py`: `_spec(...)` + handler + `add(name, fn)` | yes, gated by `capability` |
| drop-in | `tools/*.py` with `TOOL_SPECS`/`register`, or `tool.toml` in `tools.dropin_dirs` | yes, `source: "dropin"` |
| entry-point | `[project.entry-points."skeletonkey.tools"]` | yes, `source: "entry-point"` |
| skill | `skills/<name>/tool.toml`, compiled by `skills/compiler.py` | yes, `source: "skill:<name>"`, unless the declaration says `advertised = false` |

Checklist for every new tool:

1. `id` is `group.name`; `title` + `description` written for a model in a hurry (what it
   does, when **not** to, the one gotcha); `tags` carry the synonyms agents type.
2. `input_schema` with `additionalProperties: false`, defaults for every optional, and a
   `description` on each non-obvious property (it is forwarded verbatim to MCP hosts).
3. `examples[0].args` is *runnable* — it is returned as `minimal_example` on a usage
   error.
4. `see_also` / `anti_patterns` populated; `capability` set if the tool needs a host
   feature; `risk`, `idempotent`, `reversible`, `stateful`, `typical_latency_ms`,
   `typical_output_bytes` honest (they drive gating, budget and ordering).
5. Tests: one success, one usage failure, one policy/sandbox refusal; if it mutates, a
   round-trip through `fs.undo`.
6. Update this doc's tables and the skill guidance that names the tool.

## 9. Versioning

`ToolManifest.version` is part of the cache key and the advertisement `digest`; bump it
on any schema or semantic change. `manifest.version` bumps with the *tool*;
`skeletonkey.__version__` with the package; the envelope gained no breaking change since
0.1.0 and must not gain one without a `docs/adr/` entry.
