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
- `warnings` is for truthful-but-non-fatal facts (truncation, provider fallback,
  "content had changed since this entry wrote it"). A warning never fails a call, and a
  failure never hides inside a warning.
- Serialization is bounded by `budget.max_output_bytes`: over it, `data` becomes
  `{"inlined": …, "spilled": true, "total_bytes": N}` plus an `artifacts[]` entry whose
  `path` holds the **complete** payload and whose `fetch_rest` is a legal `fs.read`
  call. The payload is never cut mid-JSON.

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
| `IO`, `INTERNAL` | everything else | `trace_id` |

Every refusal names the fix. "Permission denied" without an advice string is a bug.

## 4. Risk classes and approval

`risk` ∈ `none | read | write | destructive | network | privileged`; the manifest also
carries booleans (`destructive`, `reversible`, `idempotent`, `parallel_safe`,
`open_world`, `stateful`) that the engine, MCP annotations and the cache read.

Resolution order in `Engine._authorize`: `tools.disable` → `deny` rules (never
overridable) → `dry_run` → approval (`escalate`, `require_approval`, `auto_approve`,
`confirm_destructive`, approver callback) → profile gate. A throwing approver is treated
as `INTERNAL`, never as consent. `grant_options` are `once | task | session | deny`; a
grant becomes `grant:<tool>` in `ctx.granted` and the same token replays the call.

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

## 7. Composition and caching

- Tools may call other tools through `engine.call` (that is how `registry.search` and the
  skill loader reuse the registry). Budgets, policy and the ledger apply per inner call;
  the ledger therefore holds one row per *tool* call, not per turn.
- The idempotency cache (5 s TTL) applies only when
  `idempotent ∧ ¬is_mutating ∧ stateful == "none"`. A pure read that reflects live state
  (`shell.sessions`, `fs.journal_list`, `registry.stats`) must set `stateful: "session"`
  — otherwise a polling agent is served its own last answer and calls it fresh.
- `idempotency_key` (call-level) collapses retries of the same logical mutation; the key
  is part of the cache key and of `registry.stats` attribution, never a security control.

## 8. Adding a tool

| Where | How | Advertised? |
| --- | --- | --- |
| built-in | `tools/builtin.py`: `_spec(...)` + handler + `add(name, fn)` | yes, gated by `capability` |
| drop-in | `tools/*.py` with `TOOL_SPECS`/`register`, or `tool.toml` in `tools.dropin_dirs` | yes, `source: "dropin"` |
| entry-point | `[project.entry-points."skeletonkey.tools"]` | yes, `source: "entry-point"` |
| skill | `skills/<name>/tool.toml` | **hidden** until P2 synthesis; callable, returns `NOT_IMPLEMENTED` |
| synthesized (P2) | compiled from a skill script | yes, `source: "synthesized"` |

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
