# Security model

Scope: `skeletonkey` as shipped in P0–P2 (35 tools registered, 33 advertised —
`shell.run`, `fs.*`, and the two tools synthesized from a skill pack). Written from the
code, not ahead of it; each layer names the file that implements it and the test that
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
   is refused outright, and shadowing a built-in id needs `tools.override_builtin`. P3's policy
   engine is what will make those ceilings configurable instead of hardcoded.

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

## Known gaps (P3 closes these)

| Gap | Now | After P3 |
| --- | --- | --- |
| `shell.run` script content | deny on `script` glob only (over-matches, so not shipped as a default) | argv-prefix + secret-path matcher, deny stays non-overridable |
| Rate limits | per-task caps only | per-tool (`fs.delete` ≤ 20/min) + mutation circuit breaker |
| Undo safety | warns on divergence | hard `CONFLICT` on `expect_sha` mismatch; `fs.redo` re-applies the last undone change (journaled itself) |
| Deletion | journal copy | `fs.trash` tiers: `journal` \| `os-trash` (recycle bin + journal) \| `delete`; no-trash host refuses, deletes nothing |
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
