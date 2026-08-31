---
name: vcs-git-safely
description: >-
  Drive git from an unattended session without losing work or clobbering a shared ref:
  read the state before mutating it, one intent per commit, refuse half-finished
  operations, and keep a recovery ladder that does not need `--force`.
when_to_use: >-
  Staging, committing, amending, branching, tagging, stashing, rebasing; a dirty tree left
  by an earlier attempt; a merge or rebase stopped halfway; a commit you need back.
version: "1"
tags: [git, version-control, safety, recovery]
priority: 60
requires: [shell, fs]
triggers: [git, commit, branch, tag, stash, rebase, merge conflict, reflog, dirty tree]
allowed-tools: [shell.run, shell.available, shell.quote, fs.read, fs.glob, fs.patch,
                fs.undo, fs.journal_list]
---

# Git from an unattended session

A git mistake is rarely fatal for a human who was watching, and usually fatal for an agent
that was not. The difference is that a human can undo it from memory; you can only undo it
from evidence. So: read the state, mutate one intent at a time, and leave the reflog, the
stash, and your own `fs.undo` tokens intact so recovery is mechanical rather than clever.

## 1. Read before you write

Every mutating step is preceded by a read of the exact thing it will change.

| Need | Ask |
| --- | --- |
| dirty tree, ahead/behind | `shell.run {script: "git status --porcelain=v2 --branch"}` |
| what will be committed | `git diff --cached --name-status` then `git diff --cached --stat` |
| where the pointer is | `git rev-parse --abbrev-ref HEAD`, `git rev-parse HEAD` |
| is a rebase in flight | `test -d .git/rebase-merge -o -d .git/rebase-apply` |
| who you are committing as | `git config --get user.name` (empty ⇒ the commit will fail or lie) |
| ignored-or-not | `git check-ignore -v <path>` — exit 0 means ignored, which is a *result*, not an error |

Read `exit_code`; do not infer success from a non-empty `stdout`. `git status` on a clean
repo prints one line, and `git diff` prints nothing at all.

## 2. Args never go through the string

Anything you did not write — branch names, commit messages, paths with spaces, file names
from `fs.glob` — goes in `argv` and is referenced positionally, so a branch called
`fix; rm -rf .` stays a name. The quoting rules in `shell.quote_check` apply to the fixed
part of the script; use `shell.quote` when you must compose text.

```console
script: 'git commit -q -F "$1" -- "$2"'
argv:   ["/tmp/msg.txt", "src/parser.py"]
```

`--` before paths matters: it stops a file named `-p` from being parsed as a flag.

## 3. One intent per commit, and prove it

Stage deliberately (`git add -- <paths>`), never `git add -A` in a tree you did not list
yourself — an agent's `fs.write` is not the only thing that touches a working tree. Then
verify the commit contains what you believe:

1. `git commit -q -m …`
2. `git show --stat --oneline HEAD` and check the file list and the count
3. `git status --porcelain` still empty ⇒ the intent is fully captured

If step 3 shows leftovers, commit again or amend — but never amend a commit that exists on
a remote you do not own. Amend is for the tip of your own branch only.

## 4. Refusals are correct behaviour

Stop and report instead of working around, when you find:

- `.git/index.lock` present — another process may be mid-write. Retry once after a pause;
  deleting the lock of a live process corrupts the index.
- a rebase or cherry-pick in progress (`references/recovery.md` for the ladder)
- a detached HEAD, or a branch that is not the one the task named
- a remote you cannot reach: `pull` becoming "recreate the history locally" is how work is lost
- hooks rejecting the commit: fix what the hook found. `--no-verify` exists to hide your
  failure from the next person, and `--force` on a shared ref exists to delete their work.

## 5. Recovery is a ladder, taken in order

Each rung is cheaper and less lossy than the next. Never start at the bottom.

| Lost | First rung | Then |
| --- | --- | --- |
| edits in a dirty tree | `git stash push -m "desc"` (then `git stash list`) | `fs.undo {undo_token}` if the edits came from `fs.*` |
| a wrong commit on your tip | `git reset --soft HEAD~1` (keeps the staged content) | the commit is still in `git reflog` for 90 days |
| a commit you dropped | `git reflog` → `git cherry-pick <sha>` | `git fsck --lost-found` for dangling commits |
| a file you clobbered | `git restore --source=HEAD -- <path>` | `fs.journal_list {path}` then `fs.undo` |
| a branch pointer moved | `git reflog show <branch>` | `git update-ref refs/heads/<branch> <sha>` |

`reset --hard` is the last rung, not the first: it destroys the working-tree content that
`fs.undo` could otherwise restore. Same for `clean -fd` — it deletes untracked files with no
journal and no reflog entry, which is precisely the category an agent generates most.

## 6. What to print for the human (or your own next turn)

After any history change, emit a receipt: old sha, new sha, branch, and the reflog line you
would use to go back. That one block turns "did I break it?" into a mechanical check.

```json
{"branch": "main", "old": "159bc1a", "new": "0c4d2f1",
 "undo": "git update-ref refs/heads/main 159bc1a"}
```

## Anti-patterns

| Don't | Do instead |
| --- | --- |
| `git add -A` "to be safe" | list the paths you changed, from `fs.glob`/your own log |
| `git push --force` | `--force-with-lease` on your own branch, and only after a fetch |
| `commit --amend` on a shared tip | a new commit; history rewrite is not yours to make |
| `reset --hard` to "get clean" | `stash push -u` first — it is one command to undo |
| `git clean -fdx` before a build | `git status --porcelain`, then `fs.delete` on named paths |
| parsing `git log --oneline` for shas | `git log --format=%H%x09%s` with `-z`, or `--json` if you have it |
| `cd repo && git …` with a relative path | `git -C <abs path> …`, or `cwd` — so a failed `cd` cannot aim the write at the wrong repo |
| assuming `main` exists | `git symbolic-ref --short HEAD`, or the remote's `HEAD` via `git remote show origin` |
