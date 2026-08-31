# ADR 0008: policy is data, approval is a tool, undo is a precondition

Date: 2026-08-31
Status: accepted
Affects: `core/policy.py`, `core/engine.py` (`_authorize`), `fsx/journal.py`,
`fsx/ops.py` (`delete`), `tools/builtin.py` (`policy.grant`, `fs.undo`, `fs.redo`),
`tests/test_policy_property.py`

## Context

P0–P2 gave the engine walls (roots, deny, read-only, approval, budgets) but the rules
behind them were half config-shape, half code: deny and escalate were lists of strings,
rate limiting did not exist, an approval grant was a metric rather than an auditable
record, and an undo that found the file had moved on *warned and proceeded*. For an
unattended loop, a warning on a rollback is a coin flip: the agent has no way to say
"only if the file is still what I saw".

## Options considered

1. **Keep the string rules, bolt on rate limits.** Least code, but every new rule kind
   (argv prefixes, env names, per-tool windows) forks the matcher again, and a host
   cannot see its own policy as data.
2. **One rule table, one matcher, one compile step.** `[[policy.rule]]` rows with an
   `action` (deny | allow | escalate | rate_limit) and a matcher description, compiled
   to `CompiledPolicy` once per config load; the engine consults that structure and
   nothing else. Legacy spellings (`deny` strings, `escalate` list) compile into the
   same rows, so existing configs keep working.
3. **Make grants an operator-only API outside the tool surface.** Safest, but the
   whole point is that *the agent* asks, and the audit must then show the ask and the
   answer together.

## Decision

- **Policy is data.** `core/policy.py` compiles every rule into matchers (tool glob,
  path glob, argv prefix, env *name*, rate window). `Engine._authorize` evaluates deny
  before anything else — before any approval token, before allow — and deny's refusal
  names the rule and carries the non-negotiable advice. A malformed rule is reported
  and skipped, never guessed at. `allow` only removes an approval requirement; it
  cannot clear a deny, and it never touches the `read_only` wall.
- **Approval is a tool with a receipt.** `policy.grant {tool, scope}` is registered like
  any other tool: it returns a `receipt` (`granted_by`, `tool`, `scope`, `task_id`,
  `session_id`, `ts`) and writes its own ledger row. The self-grant hole is closed by
  construction — a grant for a tool that itself needs approval is approval-gated, and a
  declining approver is final. The prompt itself now shows intent: `diff_preview` for
  write-risk tools, and `args_preview` that shows values (the `BaseException.args`
  trap that had silently turned it into argument names is regression-pinned).
- **Undo is a precondition, and redo is a journal entry.** `fs.undo {expect_sha}`
  refuses with `CONFLICT` when the file no longer holds the recorded sha; without it,
  the P1 warn-and-proceed stands unchanged and is pinned. Redo is the mirror, not a
  second undo: the journal retains the after-image (a `<token>__after` file that
  survives restart), `fs.redo {path?}` re-applies only the last *undone* change,
  journals the redo itself, and refuses with `CONFLICT` over drift, re-creation, or a
  pruned after-image rather than guessing.
- **Deletion is a tier.** `fs.trash = journal | os-trash | delete`. `os-trash` uses the
  platform recycle bin (`gio trash`, PowerShell `Shell.Application`) with the journal
  kept as a *second* copy, so emptying the bin is not the end of the line; a host
  without a trash API refuses with `UNSUPPORTED_PLATFORM` before anything is recorded.
- **The property is tested.** `tests/test_policy_property.py` makes one write attempt
  with every mutating tool under `read_only` and under a deny-all rule, and asserts
  zero change by diffing a full workspace snapshot — the error code may evolve, the
  disk cannot lie. The burst table must match the mutating set exactly.

## Consequences

A config file is now the whole policy, reviewable and diffable; the docs lint resolves
every rule key a document names against the loader, so the docs cannot promise a knob
that does not exist. Grants are auditable end to end (ask, answer, receipt, ledger row).
Undo and redo are safe to script: both fail loudly on divergence, and the only silent
case is the unchanged P1 warning, which the tests pin in place. The cost is one extra
shadow file per content write (the after-image), bounded by the same
`state.keep_snapshots` pruning as everything else. The open gap — `shell.run` script
content rules — is unchanged: deny on `script` globs stays non-overridable, and the
argv-prefix/secret-path matcher is the next rule kind, which the data-driven table
exists to absorb.
