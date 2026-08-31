# The journal, in the detail you need when undo misbehaves

Every mutating `fs.*` call records an entry *before* touching disk, so a crash
between "planned" and "applied" still leaves a recoverable trail. This page is what
the code actually does — including the places where it deliberately refuses.

## What is stored

| Change | Snapshot | Undo does | Redo re-applies |
| --- | --- | --- | --- |
| `fs.write` (existing file) | previous bytes + the new bytes | restores them, plus mode and mtime | writes the new bytes back |
| `fs.write` (new file) | the new bytes — `action: "create"` | deletes the file | writes the file back |
| `fs.patch` | previous bytes + the new bytes (the whole file, not the hunks) | restores the pre-patch file | writes the post-patch file back |
| `fs.delete` (file) | the bytes | writes the file back | deletes it again |
| `fs.delete` (dir) | a tar of the tree | unpacks the tree | deletes the tree again |
| `fs.move` | both sides | moves it back, and restores an overwritten destination | moves it forward again |
| `fs.chmod` | the previous mode bits | restores the previous mode | re-applies the requested mode |
| `fs.mkdir` | nothing | removes the directory **only if it is empty** | recreates the empty directory |

A change that is *retained* keeps both images, which is what `fs.redo` needs: entries
whose after-image was pruned (or that predate after-image capture) refuse the redo with
`CONFLICT` rather than guessing.

Before-images at or below `inline_limit` (96 KiB) are written to a small *staged*
file as well as held in memory, so an undo still works after the process restarts —
an in-RAM-only snapshot would be a trap for a long-running agent. Larger ones are
copied verbatim. Metadata restored with the content: mode and mtime. Ownership,
ACLs and creation times are not — undo is not a root operation.

## Layout on disk

```
.sk/
  ledger.ndjson                     call audit: what ran (hash-chained)
  profile.json                      cached capability probe
  journal/index.ndjson              one JSON line per journaled change (append-only)
  journal/shadow/<token>__<name>     the before-image, when it was too big to inline
  journal/shadow/<token>__staged     the before-image, when it was small
  journal/shadow/<token>__after      the after-image (what fs.redo re-applies)
  journal/shadow/<token>__tree.tar   directory snapshots
  shell/                            scratch scripts, job logs, session state
```

`index.ndjson` is append-only but **not** hash-chained — the ledger is the tamper-evident
record, the journal is a working set. Each journal line carries `sha_before` and
`sha_after`, which is what makes "did the file move under us?" answerable.

## Lifecycle

- `state.keep_snapshots` (default 200) bounds the live window, and trimming happens as
  entries arrive, not lazily: the shadow files of the evicted entries are deleted with
  them. So the undo window for an *old* change silently disappears — a long task should
  checkpoint with `git commit` rather than expecting the journal to reach back.
- `fs.journal_list {limit}` shows what is still reversible, right now.
- The journal is per-workspace, not per-process. Two concurrent runs in one workspace
  share it; `task_id` is what separates your entries from someone else's, so pass a real
  one (the autopilot context does) whenever anything else might be writing.
- `state.journal = false` disables snapshots but keeps the ledger. Then `fs.undo` and
  `fs.undo_task` return `NOT_IMPLEMENTED` naming that config key, and **deletes are
  permanent** — say so out loud before deleting anything.

## Undo semantics you can rely on

1. `fs.undo {token: "und_…"}` (the `undo_token` argument name is accepted too, since that
   is the field the mutating results carry). Idempotent: the second call reports
   `{undone: false, note: "already undone"}` instead of re-applying.
2. `dry_run: true` returns the plan — `op`, `target`, `display` and any warning — without
   touching the disk.
3. `fs.undo_task {task_id}` reverses that task's entries newest-first and **undoes all of
   them**: a per-file problem does not stop the others, because a half-reverted turn is
   the worst state to hand back. Each entry's own caveats come back under
   `results[].warnings`, and `failed[]` lists anything that could not be reversed at all.
4. Diverged content is warned about, not refused: if the file no longer holds what this
   entry wrote, undo still restores the before-image (that *is* the request) and appends
   `"content had changed since this entry wrote it…"`. If a human edited the file in the
   meantime, re-apply their change right after — the warning tells you to. When the
   rollback must be *conditional* instead, pass `expect_sha` (the sha from the last
   `fs.read`, full or 16-char prefix): a mismatch is a hard `CONFLICT` and nothing is
   written.
5. `fs.redo {path?}` re-applies the most recently *undone* change. It is the mirror of
   undo, not a second undo: only an already-undone entry is eligible, and the redo is
   journaled itself (fresh `undo_token`). It refuses with `CONFLICT` — never a silent
   overwrite — when the file changed after the undo, the path was re-created, or the
   after-image is gone. `ENOENT: nothing to redo` means there is no undone entry (yet)
   on that path.
6. Undo re-checks the sandbox against the **current** roots. If a path left the roots
   between the mutation and the undo, you get `SANDBOX_VIOLATION` and nothing is written.
7. Undo never deletes a populated directory and never touches paths it did not record.
   A `fs.mkdir` that created a whole parent chain only removes the leaf, and only if empty.
8. Re-`fs.stat` after an undo. `undone: true` means the snapshot was applied; it cannot
   promise the *build* is consistent again — re-run your tests, that is the check.

## Diagnosing the common failures

- `ENOENT: unknown undo token` — the entry was pruned, the token came from another
  workspace, or `details.known` shows a different id. `details.total` tells you whether
  the journal is empty (nothing was ever journaled) or trimmed.
- `NOT_IMPLEMENTED: the change journal is disabled` — `state.journal = false`. Fix the
  config before mutating, or restore from VCS.
- `before-image was lost` — the shadow file was deleted (state dir cleaned, or the
  entry was evicted). `fs.journal_list {path: "src/a.py"}` shows whether a newer
  snapshot for that path exists; if not, it is `git checkout -- src/a.py` or nothing.
- `SANDBOX_VIOLATION` on undo — the recorded path is outside today's roots.
- `PATCH_CONFLICT` when you re-apply after an undo — your re-read raced with a formatter.
  Read again; the file is fine.
- `CONFLICT: does not hold the content you recorded` — the `expect_sha` you passed to
  `fs.undo` does not match the file's current sha. Read the file, re-decide, retry with
  the fresh sha. `details.actual_sha` is there to diff against.
- `CONFLICT: changed after the undo` on `fs.redo` — someone (or some tool) edited the
  file after the undo. The file is exactly as you left it; re-derive the change against
  what is there now.
- `CONFLICT: …not retained…` on `fs.redo` — the after-image was pruned or predates
  capture. Re-apply the change by hand (it will be journaled), rather than trusting a
  guess.

## Relationship to git

The journal is a *scratch* undo for the current run: cheap, automatic, bounded. It is
not version control — no history, no branches, no merge. For a multi-step task, commit
at each verified checkpoint and use `fs.undo_task` only to retract the step you are still
standing on. If `shell.run {script: "git status --porcelain"}` shows unrelated dirty
files, do not `git checkout .` to "get back to a known state": that destroys someone
else's work-in-progress, including any file the journal has already pruned.
