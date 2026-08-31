---
name: fs-safe-refactor
description: >-
  Change files so the result is verifiable and reversible: read-then-patch with a
  content hash, dry-run first, batch edits, and an undo token for every mutation.
when_to_use: >-
  Editing, moving, renaming or deleting files; multi-file refactors; any change you
  cannot trivially re-derive; anything under version control you might need to unwind.
version: "1"
tags: [filesystem, refactor, safety, undo]
priority: 65
requires: [fs]
allowed-tools: [fs.read, fs.write, fs.patch, fs.search, fs.glob, fs.list, fs.stat, fs.move, fs.delete, fs.mkdir, fs.undo, fs.undo_task, fs.journal_list]
---

# Safe filesystem changes

Every mutating `fs.*` call is journaled and reversible, and refuses to act on
content it did not read. The discipline below is what makes an unattended run
recoverable: **locate → read → propose → apply → verify**, never *guess → overwrite*.

## 1. Locate, don't assume

```
fs.search {pattern: "def handler\\(", glob: "src/**/*.py", context: 2}
fs.glob   {pattern: "src/**/test_*.py"}
```

`fs.search` returns `matches[].path/line/column/snippet` plus, when it finds
nothing, `zero_match_advice` - read it before concluding "the code isn't here". A
zero-hit search on `node_modules` means an ignore rule, not a missing symbol.

## 2. Read before you write; keep the hash

`fs.read` returns `sha256` and `next_offset`. Pass the hash back as `expect_sha`:

```
fs.patch {path: "a.py", expect_sha: "<sha from fs.read>", edits: [...]}
```

If anything changed the file since you read it - a formatter, a git checkout, your
own earlier call - the patch fails with `CONFLICT` instead of overwriting work.
That is the whole point: it converts a silent clobber into a retry. On `CONFLICT`,
re-read, re-plan, re-apply. Never drop `expect_sha` to "just get it done".

For a **new** file use `fs.write {path, content, create_dirs: true}` (no
`overwrite`), so an existing file makes it fail with `EEXIST` rather than replacing it.

## 3. Prefer `fs.patch` over rewriting

Rewriting a whole file with `fs.write` throws away everything you did not
reproduce - comments, imports, the CRLF the file already had, the license header.
`fs.patch` sends `{old_text, new_text}` pairs:

- `old_text` must be unique in the file unless you pass `occurrence: N` or
  `replace_all: true`. Ambiguity is reported as `AMBIGUOUS_MATCH` with the matching
  line numbers - widen `old_text` until it is unique rather than flipping
  `replace_all` on a hunch.
- Matching is exact first, then whitespace-tolerant. The tool never rewrites
  regions you did not touch, so formatting and line endings survive.
- The result carries `unified_diff` and `bytes_before/after`: verify the diff is
  what you meant *before* moving on. `lines_changed` of 4 when you expected 40 is
  the cheapest bug detector there is.
- Batch several edits in one call. They apply in order, and a single failed edit
  fails the whole call without writing anything - there is no half-applied state.

## 4. EOL, encoding, and the traps that follow

- The file's own newline style is preserved (`newline: "preserve"`). Override only
  when the *task* is to convert. When you do pass `content` built from
  `fs.read.content`, remember read gives you text with the file's newlines intact;
  `splitlines()` then `"\n".join()` silently converts CRLF → LF.
- A `\r` inside `old_text` you typed by hand will not match a CRLF file. If a patch
  fails only on Windows-authored files, suspect line endings before logic.
- `fs.sniff` reports encoding/EOL/BOM/line stats without guessing. Use it on
  anything that is not obviously UTF-8 text; it also flags UTF-16 and binary so you
  do not "read" a .dll into context.
- Never edit a file you have not looked at. A 2 MB minified JS file read in full is
  also a 2 MB hole in your context budget: `fs.read {path, offset, limit_lines}` or
  `start_line`/`end_line`.

## 5. Preview, then commit

Anything that can mutate accepts `dry_run: true` and returns the plan instead of
acting - the `READ_ONLY_MODE` envelope contains `details.plan` with the computed
diff. For a 12-file refactor, dry-run once, read the diffs, then run for real. It
costs one extra round trip and removes the class of mistake where the first file's
diff tells you the pattern was wrong.

## 6. Undo is a first-class result

Each mutation returns `undo_token`; the batch id is the `task_id` on the context.

```
fs.journal_list {task_id}          # what we would undo, newest first
fs.undo {undo_token}               # one change
fs.undo_task {task_id}             # every change in the task, in reverse order
```

`fs.delete` embeds `{"undo": ...}` in its data - delete is *not* permanent while the
journal holds it, so do not build your own `*.bak` copies. Undo restores content and
metadata for writes/patches/deletes/moves; directories are snapshotted as a tar.
Inline snapshots cover files ≤96 KB; larger files keep a shadow copy. If
`fs.undo` says the shadow copy is gone (a `prune` happened), rebuild from VCS
instead of improvising - and say which you did.

## 7. Deletion and moves

- `fs.delete` on a directory requires `recursive: true`; without it the call fails
  with that advice. Prefer moving to a scratch dir under a root over deleting, and
  say why in the commit message.
- `fs.move` refuses to clobber unless `overwrite: true`; it journals both sides, so a
  bad rename is one `fs.undo` away.
- Policy deny rules (`.env`, `.ssh/**`, `*.pem`, credentials) are not bypassed by
  any flag, and `SANDBOX_VIOLATION` means "wrong root", not "try harder". Adding a
  root is a human decision; report the path you needed and stop.

## 8. Batch etiquette for autopilots

1. Discover the target set once (`fs.glob`/`fs.search`), don't re-derive per file.
2. Read each file, collect `(path, sha, edits)`.
3. Apply. Collect `unified_diff` per file.
4. Verify with the project's own check (formatter, tests) via `shell.run`.
5. If verification fails, `fs.undo_task` the whole batch - not file by file.

Never interleave `fs.write` of a file with `shell.run` that regenerates it (a
formatter, a codegen step) in the same batch: the second edit's `expect_sha` will
conflict, and by then you have partially reformatted code. Order: generate, then
read, then patch.

## Quick reference

| Situation | Call |
| --- | --- |
| change one line | `fs.patch` + `expect_sha` |
| same change in N places | `fs.patch` with `replace_all: true` |
| same change in N files | N `fs.patch` calls, one per file |
| rename a symbol across a repo | `fs.search` → per-file `fs.patch` → run tests |
| new file | `fs.write {overwrite: false, create_dirs: true}` |
| move/rename | `fs.move` (journals both sides) |
| remove a scratch dir | `fs.delete {recursive: true}` (undo holds it) |
| "undo that" | `fs.undo_task {task_id}` |
| not sure it's text | `fs.sniff` first |
