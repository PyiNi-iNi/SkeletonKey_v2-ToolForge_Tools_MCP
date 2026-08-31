# Security model

Scope: `skeletonkey` as shipped in P0–P3 (37 tools registered, 35 advertised —
`shell.selftest` is never advertised, and `skills.install` stays hidden while the
install gate is closed). Written from the code, not ahead of it; each layer names the
file that implements it and the test that pins it.

## Adversary and trust boundary

The threat we design against is **a misled autonomous agent**, not an attacker who has
already compromised the process:

| We protect | From | Test |
| --- | --- | --- |
| files outside `roots` | an agent that "just tidies up" `~/.bashrc` | `test_sandbox.py`, `test_mcp_stdio.py::test_sandbox_escape_is_a_tool_error_not_a_broken_connection` |
| secret-bearing paths (`.env`, keys) | an agent that reads them into the context window | `test_sandbox.py`, `test_ledger_redaction.py` |
| the context window | one `cat` of a 4 GB log (byte budget, spill artifact) | `test_envelope.py::test_budget_spills_large_data_to_artifact` |
| the user's work-in-progress | an agent that edits 60 files wrong (journal + `undo_task`) | `test_journal.py`, `test_fs_ops.py` |
| the audit trail | a rerun that cannot explain itself (hash-chained ledger) | `test_ledger_redaction.py` |
| the host process | a runaway loop (`budget.task_max_calls`, timeouts, kill-tree) | `test_engine_policy.py::test_call_budget_is_a_hard_stop_with_summarize_and_stop`, `test_policy_property.py` |

| We do **not** protect | Because | Test |
| --- | --- | --- |
| containment of `shell.run` payloads | running arbitrary commands **is** the tool; script-content rules are the open gap below | `test_policy_property.py` (the walls that *do* exist hold `shell.run` too) |
| a hostile process/user with our config | `policy.deny` is non-overridable *per call*, not per file | `test_engine_policy.py::test_deny_rule_cannot_be_negotiated` (per-call) |
| multi-tenant isolation | single-user, local (Non-goals) | — (non-goal, no test) |
| OS-level sandboxing | no seccomp/nsjail/Job Objects (Non-goals, ADR) | — (non-goal, no test) |

`fs.deny` is a **policy on `fs.*`**, not a wall around the machine: `shell.run {script:
"cat ~/.ssh/id_rsa"}` will read it, because the shell opens the file itself. Anyone who
needs that blocked must add a policy rule on script content (weak), run the agent as a
separate user, or run it in a container. This is stated here rather than discovered later.

## The layers, in evaluation order

`Engine.call` → `_guard_gate` → `_authorize` → `_charge_budget` → handler.

1. **Roots.** The top-level `roots` key — a root of the config, not an `[fs]` sub-key
   (`[fs].roots` would silently do nothing, so the example config and `tests/test_docs.py`
   both pin the real spelling). Every `fs.*` path is resolved and required to fall under a
   root; outside →
   `SANDBOX_VIOLATION` with `details.roots`. `policy.deny_outside_roots = true` is not
   relaxable by a call argument.
   `fsx/sandbox.py` · `tests/test_sandbox.py`
2. **Symlink policy + provenance.** `fs.follow_symlinks = "within-roots"` (default)
   resolves the final target and re-checks it against the roots, so `link → /etc/passwd`
   is refused while repo-internal links keep working. `never` refuses any link; `always`
   is opt-in. Every `fs.*` result echoes where the path landed in `data.via` (matched
   root, each symlink hop, the resolved final target), so a host sees a traversal instead
   of guessing at one; the default policy is exercised on a real link in
   `tests/test_sandbox.py::test_follow_symlinks_within_roots_follows_an_in_root_link`.
3. **`fs.deny`** (12 default globs: `.env`, `.env.*`, `id_rsa`, `id_ed25519`, `.ssh/**`,
   `*.pem`, `*.key`, `credentials`, `.gitconfig`, `.aws/credentials`, `.netrc`,
   `secrets.*`) — refused with `DENY_RULE` naming the pattern; unreadable *and*
   unwriteable, and the deny check happens before the existence check so a refusal does
   not leak whether the file exists.
4. **`fs.ignore`** (13 defaults: `.git/**`, `node_modules/**`, `__pycache__/**`,
   `.venv/**`, `.next/**`, `target/**`, `*.pyc`, `.DS_Store`, …) — walk-level skipping for
   `list`/`glob`/`search`. Ignored ≠ denied: `fs.read(".git/config")` is still allowed,
   because a diff of the index is legitimate and reading `.git/config`'s credentials is
   not what the pattern is for. Both lists are anchored per walked directory (`.git/**`,
   not `**/.git/**`), and setting either list **replaces** the defaults — `ignore = []`
   walks `.git` and `node_modules` for real.
5. **Windows path hardening.** `fs.reject_device_names = true` (`.`, `CON`, `PRN`,
   `AUX`, `NUL`, `COM1`…, trailing dots/spaces that silently truncate on write);
   `fs.long_path_prefix = true` (the `\\?\` prefix is applied for the syscall and
   stripped from the returned logical path so a drive-letter path never comes back mangled).
6. **Policy rules as data.** `[[policy.rule]]` entries (action = deny | allow |
   escalate | rate_limit; matchers for tool globs, path globs, argv prefixes, env
   *names*) plus the legacy spellings (`deny` strings, `escalate` list) compile to
   `CompiledPolicy` in `core/policy.py` and are evaluated in `_authorize` before any
   handler and before any approval token. A deny is reported as
   `{"advice": "deny rules cannot be overridden per-call by design"}` and outranks an
   allow for the same call; a malformed rule is reported and skipped, never guessed.
   Defaults: `fs.delete(**/.ssh/**)`, `fs.delete(**/cookies*)`. The glob is tested
   against every string argument, plus the basename of path-ish keys.
   `core/policy.py`, `core/engine.py::_match_deny` · `tests/test_engine_policy.py`
7. **`policy.read_only`.** Plan mode, not a gag: mutating tools are *withheld from
   advertisement* (so a host cannot pick one by accident) **and** refused at dispatch
   (`READ_ONLY_MODE` + `details.plan`) unless the tool declares a `dry_run` property, in
   which case the handler's real preview runs and writes nothing.
   `tests/test_engine_policy.py`
8. **Approval.** `require_approval = [destructive, privileged, network]` (risk levels, or
   explicit tool ids) + `confirm_destructive` + `auto_approve = [none, read, write]`.
   A refusal is `APPROVAL_REQUIRED` carrying `prompt_payload()` — `kind, tool, risk,
   reason, description, destructive, reversible, args_preview, approve_token,
   grant_options, advice`. `args_preview` is redacted and length-capped: the human sees
   intent, not the payload. Grants are `once | task | session | deny`; a `task`/`session`
   grant becomes `grant:<tool>` in `ctx.granted` and is echoed in
   `metrics.approval_grant` so a reviewer can see it in the ledger. **An approver
   callback that throws is `INTERNAL`, never consent.** P3 makes the prompt show
   intent, not just arguments: write-risk prompts carry `diff_preview` (a unified diff
   for `fs.write` against the current file, dry-run-applied edits for `fs.patch`) and
   `args_preview` shows *values* (a `BaseException.args` trap had silently turned it
   into argument names since P0 — regression-pinned). And approval is a tool, not a
   side channel: `policy.grant {tool, scope}` records a grant in the calling task's
   context and returns a `receipt` (`granted_by, tool, scope, task_id, session_id, ts`)
   plus its own ledger row; a grant for a tool that itself needs approval is
   approval-gated, which is what closes the unattended self-grant hole.
   `core/engine.py`, `tools/builtin.py` · `tests/test_tools_builtin.py`,
   `tests/test_mcp_stdio.py::test_policy_grant_over_the_wire`
9. **Escalation.** `policy.escalate = ["fs.write", …]` (or a `[[policy.rule]]` with
   `action = "escalate"`) re-risks tools at *dispatch* time (not advertisement time, so
   `tools/list` stays stable across a mid-task config edit); a `policy.grant` for the
   tool is re-evaluated at the escalated risk.
   `tests/test_engine_policy.py::test_escalate_rule_reevaluates_risk_only_for_matched_calls`
10. **Budget.** `max_output_bytes` (spill, never truncation), `max_result_tokens`,
    `task_max_calls` / `task_max_mutations` / `task_max_wall_s` / `task_max_tokens_out`,
    `budget.max_read_bytes` / `budget.max_write_bytes`, `shell.timeout_s` (hard kill + tree kill,
    `+2 s` slack so the sentinel wins the race), `shell.max_output_bytes`.
    `BUDGET_EXCEEDED` names `details.exceeded[]`. P3 adds per-tool `rate_limit` rules —
    the default ships `fs.delete` ≤ 20 per 60 s — and a mutation-burst breaker: a call
    past the limit is `BUDGET_EXCEEDED` naming `details.exceeded` (the rule) and is not
    executed, and previews are not charged.
    `tests/test_engine_policy.py::test_rate_limit_refuses_before_dispatch_and_names_the_rule`,
    `::test_mutation_burst_breaker_stops_a_runaway`, `::test_default_rate_limit_covers_fs_delete`
11. **Reversibility.** `fs.journal = true`: before-images staged under
    `<state>/journal/shadow/` *before* the write, `undo_token` returned, `undo_task`
    for whole-turn retract. P3 hardens the rollback itself: `fs.undo {token,
    expect_sha}` refuses with `CONFLICT` (file untouched) when the file no longer holds
    the sha the caller recorded — the P1 warn-and-proceed is unchanged without it,
    regression-pinned; and `fs.redo {path?}` re-applies the last *undone* change,
    journaled itself (fresh `undo_token`) and refused with `CONFLICT` over a file that
    drifted since the undo, a re-created path, or a pruned after-image. Deletion honors
    `fs.trash`: `journal` (default), `os-trash` (platform recycle bin, with the journal
    as a second copy; a host with no trash API gets `UNSUPPORTED_PLATFORM` and deletes
    nothing), `delete` (hard, unjournaled). The after-image is retained on disk, so
    redo survives a restart.
    `fsx/journal.py`, `fsx/ops.py` · `tests/test_journal.py`, `tests/test_fs_ops.py`,
    `tests/test_mcp_stdio.py::test_os_trash_tier_over_the_wire`
12. **Redaction.** `state.redact = true` masks values in everything that persists: ledger
    `result_preview`, `error.message`/`hint`, `details`, dry-run plans, session env dumps.
13. **Audit.** `<state>/ledger.ndjson`, one row per call, hash-chained; `ledger.verify()`
    reports `broken_at`, and a torn tail line is trimmed on open (a crash must not make the
    whole log unreadable). `ledger.stats()` is agent-visible via `registry.stats`.
    Every row also carries a `context_receipt` (`exposed_results`, `withheld`,
    `stop_reason`) inside the chain: the advertised set the host could call, every
    registered tool it could not with the gate's reasons, and the call's outcome —
    after the fact, an agent (or an eval) can read *why* it never saw a tool.

## Secrets handling

Nineteen named patterns in `core/redact.py`: `aws_key`, `gh_token`, `github_pat`, `slack`,
`discord`, `jwt`, `stripe`, `anthropic`, `openai`, `google`, `hf`, `azure` (SAS), `pem`,
`url_creds`, `auth_header`, `conn_str`, `bearer`, `cli_flag`, `kv_secret`. Two rules make
them usable:

- the substitution replaces **exactly the captured value span**, so
  `{"token": "abc…xyz"}` becomes `{"token": "***REDACTED***"}` and the ledger line stays
  parseable JSON. Rebuilding from groups lost the closing quote; that is called out in a
  code comment and pinned by a test, because an audit line nobody can parse is worse than
  one that leaks;
- shaped secrets keep a 4-character tail (`***GITHUB_TOKEN:...k9f***`) and `pem` becomes
  `***REDACTED_PRIVATE_KEY***`, so a human can still identify *which* credential it was.

`redact_obj` also masks dict **keys** that look secret-bearing (used for dry-run plans and
error `details`), and `looks_secrety` suppresses whole log lines. Applied to: ledger
previews, `error.message`/`hint`/`details`, dry-run `plan.args`, session env dumps, and
MCP `logs/` output. Each pattern has a case in `tests/test_ledger_redaction.py`.

`shell.sessions` returns environment **names only**; the values come back only when a call
explicitly asks `capture_env: true`, and those go to the state dir, not to the log.

Anything written to `budget.spill_dir` or `journal/shadow/` is **raw** (that is the point:
they are re-readable). Set `state.dir` somewhere with `700` on multi-user machines, and
treat `<state>/` like a browser profile — out of backups you sync, out of bug reports.

`spill_dir` defaults to `<state>/spill`, i.e. **inside** the workspace root, which is what
lets a host obey `fetch_rest` (it is just `fs.read`, still sandboxed). Point it outside the
roots and spilled payloads become unreadable; point it at an unwritable path and the
envelope degrades honestly — inlined prefix plus `artifacts[].meta.spill_error`, never a
silent loss.

## Skills: adding capability at runtime

P2 lets a *pack on disk* add callable tools, which is a new shape of exposure and worth naming
precisely: `shell.run` could already run anything, so the skill layer does not add code
execution to the threat model — it removes the review step from it. The mitigations, in the
order the code applies them:

1. **A skill tool is a subprocess.** `skills/compiler.py` produces a manifest whose handler runs
   one script through the same executor as `shell.run`. No Python from a skill is imported, so
   a pack cannot reach `Registry._tools`, the config object, or the journal's shadow directory.
2. **Values never enter the script text.** Properties bind to argv (`flags`), to one JSON argv
   element (`argv_json`), or to stdin (`stdin_json`). A declaration that tries to interpolate
   `{path}` into a body is refused at load time; that refusal is the whole prompt-injection
   story for this layer (ADR 0007).
3. **The child's environment is pruned by default.** `env_mode = "clean"` drops your environment
   and keeps the bootstrap keys a process needs to find its binaries (`PATH`, `TEMP`,
   `SystemRoot`, `HOME` and friends, `_CLEAN_KEEP` in `shells/base.py`) plus whatever the call
   passed in `env`. That filter runs on the *inherited* environment, not on the merged one: the
   first version filtered after merging, which silently deleted caller-supplied variables — an
   env knob that drops what it just accepted is worse than no knob.
4. **The script is inlined from inside the skill directory.** A `handler_script` must be a
   relative path inside the pack with an allowed extension; `..`, absolute paths, and foreign
   extensions are load errors. Inlining (rather than `cd <skill> && ./x.sh`) keeps every
   payload in the runner's own temp dir under `state.dir`.
5. **Install is opt-in; uninstall is approval-gated.** `skills.allow_install` defaults to false,
   and `skills.install` is not even advertised while it is closed (`dry_run` answers anyway, so
   a plan can be reviewed without the privilege). `skills.uninstall` is not gated behind that
   flag — removing capability is not escalating it — but it is marked `destructive` so the same
   policy that guards a directory delete guards it, and it refuses while the skill still has a
   running job.
6. **Restores are checked against the destination.** A journaled directory is a tar; Python
   before 3.11.4 has no `filter="data"`, so `fsx/journal.py::_extract_guarded` refuses members
   that resolve outside the restore target, that are links pointing out of it, or that are not
   files/directories. The archive is one we wrote from a directory the agent chose, which is
   precisely when "we wrote it" stops being a security argument.
7. **What a skill cannot claim.** `risk` stops at `write`; `destructive = true` in a `[[tool]]`
   is refused outright, and shadowing a built-in id needs `tools.override_builtin`. Those
   ceilings are hardcoded in `skills/compiler.py`; P3's rule engine gives the engine
   deny/allow/escalate/rate-limit, but the compiler's ceilings remain fixed (open item).

The residual, stated plainly: with `skills.allow_install = true`, an agent that can write files
inside the roots can write a skill and then run it. That is why the default is false, why the
copy is journalled (undo), and why the file list is small, extension-filtered and symlink-free.


## What is deliberately *not* here

No network egress from the toolkit (`transport.network` tools are `risk: "network"` and
`require_approval` by default; the P1 surface has none), no plugin trust model beyond
"`tools.dropin_dirs` is operator input, so drop-ins run as the agent's user", and no
ownership tool: `fs.chmod` exists (mode bits, journalled, `recursive` re-checking every
path it walks), `chown` is not a verb here at all (it needs the Windows
runner before it is worth writing: P6), and neither half pretends about Windows. The rule was
"do not ship a half-model of ACLs", and the shipped shape keeps it: on NT a chmod sets the
read-only attribute and *nothing else*, so the call re-stats the path after writing and
returns `effective` plus `partial_apply` when the bits you asked for did not stick - the
`icacls` recipe is offered as an unverified template, not as a claim. Refusing to report
success for a permission the OS ignored is the security-relevant part: `0600` on Windows
does not mean "nobody else can read this".

## What P3 closed, and the one gap that remains

| Gap | P0–P2 | P3 |
| --- | --- | --- |
| Rate limits | per-task caps only | per-tool `rate_limit` rules (default: `fs.delete` ≤ 20/min) + a mutation-burst breaker; `BUDGET_EXCEEDED` names the rule and the call does not run |
| Undo safety | warns on divergence | `expect_sha` hard `CONFLICT`, file untouched (warn-and-proceed unchanged without it); `fs.redo` journaled, drift-refusing, restart-safe |
| Deletion | journal copy only | `fs.trash` tiers: `journal` \| `os-trash` (recycle bin + journal) \| `delete`; a no-trash host refuses and deletes nothing |
| Grant audit | `metrics.approval_grant` | `policy.grant` returns a `receipt` + ledger row; a self-grant for a gated tool is itself approval-gated |
| **`shell.run` script content** | deny on `script` glob only (over-matches, so not shipped as a default) | **still open** — argv-prefix + secret-path matcher; deny stays non-overridable |

## Test map

| Claim | Test |
| --- | --- |
| `../../../etc/passwd` refused (also over MCP, session survives) | `test_sandbox.py`, `test_mcp_stdio.py::test_sandbox_escape_is_a_tool_error_not_a_broken_connection` |
| symlink to outside a root refused | `test_sandbox.py::test_symlink_in_parent_directory_also_denied` |
| default symlink policy follows an in-root link (real link, real test) | `test_sandbox.py::test_follow_symlinks_within_roots_follows_an_in_root_link` |
| `.env` denied, existence not leaked | `test_sandbox.py`, `test_tools_builtin.py` |
| deny rule beats an approval token | `test_engine_policy.py::test_deny_argv_prefix_blocks_shell_run_even_with_an_approval_token` |
| deny rule outranks an allow for the same call | `test_engine_policy.py::test_deny_outranks_an_allow_rule_for_the_same_call` |
| 21st `fs.delete` in a burst → `BUDGET_EXCEEDED` naming the rule, not executed | `test_tools_builtin.py::test_fs_delete_burst_hits_the_default_rate_limit`, `test_engine_policy.py::test_rate_limit_refuses_before_dispatch_and_names_the_rule` |
| `read_only` withholds **and** refuses, preview still runs | `test_engine_policy.py::test_read_only_mode_blocks_mutations_with_a_plan`, `test_tools_builtin.py` |
| throwing approver ≠ consent | `test_engine_policy.py::test_a_throwing_approver_never_counts_as_consent` |
| grant receipt + self-grant gated on a UI-less host | `test_tools_builtin.py`, `test_mcp_stdio.py::test_policy_grant_unblocks_the_same_connection_with_an_approver` |
| stale `expect_sha` → `CONFLICT`, file untouched; no `expect_sha` → P1 warning pinned | `test_journal.py::test_undo_with_stale_expect_sha_conflicts_and_touches_nothing`, `test_tools_builtin.py::test_fs_undo_without_expect_sha_keeps_the_p1_warning` |
| `fs.redo` round-trips write/create/delete/move/chmod/mkdir; drift → `CONFLICT` | `test_journal.py::test_redo_refuses_to_roll_over_a_drifted_file`, `test_mcp_stdio.py::test_fs_redo_and_expect_sha_over_the_wire` |
| redo survives a restart; after-image pruned → refuse, don't guess | `test_journal.py::test_redo_survives_a_journal_restart`, `::test_redo_of_an_entry_without_after_image_is_a_conflict_not_a_guess` |
| os-trash: file in the bin, journal second copy after a restart; no-trash host deletes nothing | `test_fs_ops.py::test_os_trash_moves_to_the_recycle_bin_and_keeps_a_journal_copy`, `test_mcp_stdio.py::test_os_trash_tier_over_the_wire` |
| `read_only` / deny wall ⇒ zero writes for **every** mutating tool | `test_policy_property.py::test_read_only_wall_means_zero_writes`, `::test_deny_rule_wall_means_zero_writes` |
| `via` provenance: matched root + symlink hops, over the wire | `test_sandbox.py::test_via_reports_root_and_chain_for_multi_hop_links`, `test_mcp_stdio.py::test_fs_read_carries_via_provenance_over_the_wire` |
| ledger chain detects a 1-byte tamper | `test_ledger_redaction.py` |
| redaction of 10 secret shapes in both previews and errors | `test_ledger_redaction.py` |
| spill artifact holds the full payload; unwritable spill dir reports `spill_error` | `test_envelope.py`, `test_tools_builtin.py` |
| undo works after a process restart | `test_journal.py`, `test_mcp_stdio.py` |
