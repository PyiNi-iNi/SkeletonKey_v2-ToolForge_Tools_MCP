# Recovery ladder, rung by rung

Each step states what it destroys, because the whole point of the ladder is to pick the
least destructive step that recovers the content you named.

## Rung 0 — nothing is lost yet

`git status --porcelain=v2 --branch` and `git stash list`. If the content you want is
*listed* as modified, you are here, and the next rung needs no thought.

## Rung 1 — park the tree

```bash
git stash push -u -m "autopilot: parked before <what you are about to do>"
git stash list                     # refs/stash@{0} is yours
git stash apply --index stash@{0}  # apply keeps the stash entry; pop does not
```

`-u` includes untracked files, which is where an agent's newly created files live. It does
**not** include ignored files (`-a` would, and `-a` will happily swallow a 4 GB `node_modules`,
so ask before using it). If you stashed to get a clean tree for a rebase, `pop` after; if you
stashed because the content looked lost, `apply` and leave the entry in place.

## Rung 2 — move a branch pointer back

```bash
git reflog show --date=iso main | head -20
git update-ref refs/heads/main <sha-from-reflog>
```

`update-ref` is the mechanical undo for `reset`, `commit`, `merge`, and `rebase` on a local
branch: it does not touch the working tree, so run `git status` afterwards and decide what
the diff means. `reset --soft` keeps the index, `--mixed` (default) keeps the files,
`--hard` keeps nothing — the last is the only one of the three that needs a recovery plan.

## Rung 3 — a dropped commit

`git reflog` covers commits reachable from a ref within its expiry (90 days for reachable,
30 for unreachable, default). Beyond that, only the object itself survives:

```bash
git fsck --lost-found              # writes dangling commits under .git/lost-found
git log -1 --format=%B <sha>       # is this the one?
git cherry-pick <sha>              # bring it back as a new commit on the current branch
```

Cherry-picking beats `reset` to a dangling sha when other work has landed since: it does not
move anything you now want to keep.

## Rung 4 — mid-operation states

| State on disk | How to leave it, deliberately |
| --- | --- |
| `.git/rebase-merge/` | resolve the stopped commit, `git add -- <paths>`, `git rebase --continue`; or `git rebase --quit` (keeps the branch where it was) rather than `--abort` (throws away completed steps) |
| `.git/MERGE_HEAD` | `git commit` to conclude, or `git merge --abort` only while you are certain no conflict resolution is worth keeping |
| `.git/CHERRY_PICK_HEAD` | same shape as merge: `--continue` / `--skip` / `--abort` |
| `.git/index.lock` | check for a live process (`ps`, or the file's age) before removing it; a stale lock is older than any process and can be unlinked, a fresh one means you are racing something |
| a corrupt index | `git read-tree HEAD` rebuilds it from the commit without touching files |

`--quit` versus `--abort` is the distinction an unattended session gets wrong most often:
`--abort` discards the work done so far in the operation, `--quit` stops and keeps it.

## Rung 5 — the file was never committed

If the file came from `fs.write`/`fs.patch`, git cannot help: it has no history of it.

```console
$ sk fs journal                             # the whole journal, newest first
$ sk call fs.journal_list '{"path": "<path>"}'   # filtered to one file
```

then `fs.undo {undo_token}` on the token of the write you want unwound. This is the case the
journal exists for, and the only one where a file an agent deleted is recoverable —
`git clean -fd` and `rm` have no equivalent, which is why `fs.delete` is the
destructive-but-undoable path and `shell.run {script: "rm …"}` is not.
