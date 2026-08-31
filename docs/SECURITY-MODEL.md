# Security model

Scope: `skeletonkey` as shipped in P0–P1 (32 tools, `shell.run` included). Written from
the code, not ahead of it; each layer names the file that implements it and the test that
pins it.

## Adversary and trust boundary

The threat we design against is **a misled autonomous agent**, not an attacker who has
already compromised the process:

| We protect | From |
| --- | --- |
| files outside `roots` | an agent that "just tidies up" `~/.bashrc` |
| secret-bearing paths (`.env`, keys) | an agent that reads them into the context window |
| the context window | one `cat` of a 4 GB log (byte budget, spill artifact) |
| the user's work-in-progress | an agent that edits 60 files wrong (journal + `undo_task`) |
| the audit trail | a rerun that cannot explain itself (hash-chained ledger) |
| the host process | a runaway loop (`budget.task_max_calls`, timeouts, kill-tree) |

| We do **not** protect | Because |
| --- | --- |
| containment of `shell.run` payloads | running arbitrary commands **is** the tool; see §Gaps |
| a hostile process/user with our config | `policy.deny` is non-overridable *per call*, not per file |
| multi-tenant isolation | single-user, local (Non-goals) |
| OS-level sandboxing | no seccomp/nsjail/Job Objects (Non-goals, ADR) |

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
2. **Symlink policy.** `fs.follow_symlinks = "within-roots"` (default) resolves the final
   target and re-checks it against the roots, so `link → /etc/passwd` is refused while
   repo-internal links keep working. `never` refuses any link; `always` is opt-in.
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
6. **`policy.deny`** — `tool-id-or-glob(arg-glob)` rules evaluated before everything else
   in `_authorize`, before any approval token, and reported as
   `{"advice": "deny rules cannot be overridden per-call by design"}`. Defaults:
   `fs.delete(**/.ssh/**)`, `fs.delete(**/cookies*)`. The glob is tested against every
   string argument, plus the basename of path-ish keys.
   `core/engine.py::_match_deny`
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
   callback that throws is `INTERNAL`, never consent.**
9. **Escalation.** `policy.escalate = ["fs.write", …]` re-risks tools at *dispatch* time
   (not advertisement time, so `tools/list` stays stable across a mid-task config edit).
10. **Budget.** `max_output_bytes` (spill, never truncation), `max_result_tokens`,
    `task_max_calls` / `task_max_mutations` / `task_max_wall_s` / `task_max_tokens_out`,
    `budget.max_read_bytes` / `budget.max_write_bytes`, `shell.timeout_s` (hard kill + tree kill,
    `+2 s` slack so the sentinel wins the race), `shell.max_output_bytes`.
    `BUDGET_EXCEEDED` names `details.exceeded[]`.
11. **Reversibility.** `fs.journal = true`: before-images staged under
    `<state>/journal/shadow/` *before* the write, `undo_token` returned, `fs.undo`
    refusing to roll over a file it did not change (warns; `expect_sha` in P3 makes it a
    hard `CONFLICT`), `undo_task` for whole-turn retract. Deleting removes the file, and
    the shadow copy is kept until `state.keep_snapshots` pruning.
12. **Redaction.** `state.redact = true` masks values in everything that persists: ledger
    `result_preview`, `error.message`/`hint`, `details`, dry-run plans, session env dumps.
13. **Audit.** `<state>/ledger.ndjson`, one row per call, hash-chained; `ledger.verify()`
    reports `broken_at`, and a torn tail line is trimmed on open (a crash must not make the
    whole log unreadable). `ledger.stats()` is agent-visible via `registry.stats`.

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

## What is deliberately *not* here

No network egress from the toolkit (`transport.network` tools are `risk: "network"` and
`require_approval` by default; the P1 surface has none), no plugin trust model beyond
"`tools.dropin_dirs` is operator input, so drop-ins run as the agent's user", and no
permissions tool: `fs.*` has no `chmod`/`chown` in P1 on purpose, because a half-model of
Windows ACLs is worse than none - the agent sets `icacls` (or `chmod`) through `shell.run`,
and `fs.move`/`fs.write` preserve the mode they found. A real `fs.chmod` is `PLAN.md` §10
step 0c; it must answer `UNSUPPORTED_PLATFORM` with the `icacls` recipe rather than pretend
on ACL-only hosts.

## Known gaps (P3 closes these)

| Gap | Now | After P3 |
| --- | --- | --- |
| `shell.run` script content | deny on `script` glob only (over-matches, so not shipped as a default) | argv-prefix + secret-path matcher, deny stays non-overridable |
| Rate limits | per-task caps only | per-tool (`fs.delete` ≤ 20/min) + mutation circuit breaker |
| Undo safety | warns on divergence | `expect_sha` hard `CONFLICT`, `fs.redo` (P3) |
| Deletion | journal copy | recycle-bin tier (`fs.trash = "os-trash"`) |
| Grant audit | `metrics.approval_grant` | explicit `policy.grant` (P3) + ledger `receipt` |

## Test map

| Claim | Test |
| --- | --- |
| `../../../etc/passwd` refused (also over MCP, session survives) | `test_sandbox.py`, `test_mcp_stdio.py` |
| symlink to outside a root refused | `test_sandbox.py` |
| `.env` denied, existence not leaked | `test_sandbox.py`, `test_tools_builtin.py` |
| deny rule beats an approval token | `test_engine_policy.py` |
| `read_only` withholds **and** refuses, preview still runs | `test_engine_policy.py`, `test_tools_builtin.py` |
| throwing approver ≠ consent | `test_engine_policy.py` |
| ledger chain detects a 1-byte tamper | `test_ledger_redaction.py` |
| redaction of 10 secret shapes in both previews and errors | `test_ledger_redaction.py` |
| spill artifact holds the full payload; unwritable spill dir reports `spill_error` | `test_envelope.py`, `test_tools_builtin.py` |
| undo works after a process restart | `test_journal.py`, `test_mcp_stdio.py` |
