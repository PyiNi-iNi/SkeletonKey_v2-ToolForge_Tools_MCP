# ADR-0006 — Journal-and-undo in the toolkit, not delegated to git

- **Status:** accepted
- **Date:** 2026-08
- **Deciders:** Dime

## Context

The obvious way to make agent edits reversible is "commit first, `git reset` later". Four
reasons that is not the primitive we build on:

1. most agent sessions run in a dirty tree — the user's uncommitted work is exactly what the
   agent is being asked to change, so "commit" would either swallow it into the agent's
   commit or refuse to run at all;
2. plenty of target directories are not repositories at all (a scratch dir, a site root, a
   `node_modules` patch, a config folder), and the toolkit must behave identically there;
3. `git` only covers tracked files: `fs.write` of a new file is invisible, `chmod`/mtime and
   rename-with-content-change are only partially modelled, and an agent's `shell.run` that
   writes through a build script is invisible entirely;
4. undo must be *per-turn*, per-call, and atomic-with-the-call. The agent needs an
   `undo_token` in the same envelope as the write, before the user has saved anything.

## Decision

`fsx/journal.py` is a content-addressed before-image store owned by the toolkit:

- **staged before the write**, never after: `_commit` moves the shadow copy into place as
  part of the same operation, so an interrupted write leaves a recoverable entry, not a gap;
- small edits are inlined (`<token>__staged`, ≤ 96 KiB), larger files are copied
  (`<token>__` + name), directory deletions are captured as a tar
  (`<token>__tree.tar`) — restoring a deleted directory never needs `rmtree`/`copytree`
  heuristics;
- a file that did **not** exist is recorded as `action: "create"`; undo removes it, and a
  directory is `os.rmdir`'d **only when empty** (never recursive);
- entries are indexed in `<state>/journal/index.ndjson` with `sha_before`/`sha_after`,
  `mode`, `mtime`, `task_id`; a torn tail line is dropped at open, the same policy as the
  ledger;
- `undo` restores content **and** mode+mtime, reports `changes`, and warns when the current
  content matches neither what we wrote nor what we recorded (`sha_after` divergence) rather
  than clobbering someone's later edit;
- `undo_task {task_id}` walks newest-first and stops on the first failure, because a partial
  retract that keeps going produces a tree no one can reason about;
- pruning trims at `state.keep_snapshots` **and reclaims disk** (deleting shadow files, not
  just index rows);
- if the journal is disabled, `record_*` return `""` and `undo*` raise `NOT_IMPLEMENTED`
  whose `details` name the config key to flip (`state.journal`). Disabling it is a
  decision, never something that quietly eats an undo.

`git` stays the *user's* tool: `fs.undo` composes with it (a restore is a write, so it is
journal-undoable too, giving a two-level retract) and the `fs-safe-refactor` skill tells
agents to `git diff` **before** trusting an undo summary.

## Rejected alternatives

- **`git stash` + `git checkout`.** Destroys the user's staging area; unrecoverable if the
  tree was already dirty; wrong for non-repos.
- **Shadow copy of the whole workspace before each task.** Simple and correct, but O(repo)
  per turn; a 2 GB checkout per task is not a tool, it is a backup product.
- **`content_hash` only, storing no bytes.** Then undo is "here is the sha you should have
  kept", which is advice, not reversibility.
- **Delegating to a VCS adapter chosen at runtime (hg/jgit/svn).** Multiplies the failure
  surface for a promise we can make unconditionally with ~200 lines and a temp dir.

## Consequences

- (+) `undo_token` exists for every `fs.*` mutation on every host, with or without `git`,
  and survives a process restart (it is on disk, indexed).
- (+) Disk growth is bounded and observable: `fs.journal_summary` reports `shadow_bytes`,
  `by_action`, `index`, `root`; `PLAN.md`'s metrics track undo usage.
- (−) Shadow copies hold pre-edit bytes, including files the deny list would not let us read
  again. They live in `<state>/journal/shadow/` — treat it like a browser profile
  (`docs/SECURITY-MODEL.md` §Secrets handling).
- (−) `shell.run`'s own writes are *not* journaled (we cannot intercept `sed -i`); the skill
  guidance routes multi-file edits through `fs.patch` precisely so that they are. Stated in
  `docs/TOOL-CONTRACT.md` §6 and in the anti-patterns of `shell.run`.

## Verification

`tests/test_journal.py` (27 cases): inline-vs-copy thresholds, create-undo not removing a
non-empty directory, mode+mtime restore, undo idempotence, `undo_task` ordering
(`[t2, t1]`) and stop-on-failure, unknown-token error listing known tokens, index survives a
restart, torn tail dropped, pruning reclaims bytes, `discard` rolling back an uncommitted
change, divergence warning, and `undo` refusing a target that left the roots while still
working for one inside them. `tests/test_mcp_stdio.py` proves an undo over the wire after a
restart.
