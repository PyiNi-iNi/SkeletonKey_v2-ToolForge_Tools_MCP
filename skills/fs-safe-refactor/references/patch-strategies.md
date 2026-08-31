# Choosing an edit strategy

`fs.patch` tries exact match, then whitespace-normalised match. Everything else is a
signal to change the edit, not the tool.

| Symptom | Code | What actually happened | Do this |
| --- | --- | --- | --- |
| your anchor appears twice | `AMBIGUOUS_MATCH` | `old_text` is not unique; `details.failures[].at_lines` gives up to 5 line numbers | extend `old_text` to include a neighbouring unique line |
| same, and you *mean* all of them | — | intended | `replace_all: true`, then check `matched` and `at_line` per edit in the result |
| anchor not found | `PATCH_CONFLICT` | text differs (indentation, EOL, non-breaking space, or you edited a stale read) | re-read the exact region with `fs.read {start_line, end_line}` and copy from `snippet` |
| found, but the diff looks wrong | `unified_diff` | your `new_text` lost surrounding context | include the full lines in both texts; never patch mid-token when you can patch whole lines |
| file changed since you read it | `CONFLICT` (`expect_sha`) | formatter, git op, or your own earlier call | re-read, re-plan; do not drop `expect_sha` |
| `applied: 0` but ok | — | an edit was a no-op (`old_text == new_text` after normalisation) | that is a free pass, not a success - verify the file actually says what you wanted |

## Anchoring rules that avoid 90 % of failures

1. **Whole lines.** Start and end `old_text` at line boundaries. Partial-line anchors
   collide with other lines and hide indentation mistakes.
2. **One unique neighbour.** If the target line is generic (`}`), include the line
   above it. `old_text` should read like a sentence you could grep for.
3. **Never paste from a summary.** Reproduce `old_text` from `fs.read` output or
   `fs.search`'s `snippet` field. Reconstructing it from memory is where tabs,
   trailing spaces, and `→` vs `->` come from.
4. **Order matters within a call.** Edits apply sequentially to the evolving buffer,
   so a later `old_text` may need to match what an earlier edit produced. If two
   edits overlap, merge them into one.
5. **Independent files, independent calls.** One call per file keeps the diff and the
   undo token per file, which is what makes partial rollback possible.

## Fuzzy matching: what it forgives and what it does not

The fallback normalises line endings and runs of whitespace, so:

- CRLF vs LF, tab vs 4-space, and trailing-space differences match.
- A missing or extra *line* does not match. Neither does a changed character.
- Leading-indentation *amount* differences inside the anchor are forgiven, but the
  replacement re-uses the file's own leading whitespace for the first line, so an
  intentionally re-indented block should be written with the file's indentation.

If a fuzzy match succeeded but the file's indentation looks odd afterwards, your
`new_text` carried its own indentation *and* the tool re-applied the file's. Fix by
making `new_text` relative (no leading spaces on continuation lines).

## When to use `fs.write` instead

- The file is generated and small (a lockfile stub, a `__init__.py`, a config).
- You are creating it.
- The change touches > ~60 % of the file, where a pile of patches is less legible
  than one rewritten body. Then read it fully first, and keep `expect_sha` so a
  concurrent formatter still trips you.

Never reach for `shell.run` with `sed -i`/`(Get-Content) -replace` to edit files: it
bypasses the journal (no undo), normalises encodings, and reports success even when
it matched nothing. The one exception is a stream transform on a file you are about
to recreate anyway - and say so in the result notes.

## Sequencing a rename across a repo

```
1. fs.search {pattern: "\\bfoo_bar\\b", glob: "**/*.{py,md,toml}", context: 0}
2. group hits by file; drop generated/vendored paths (they are ignored anyway)
3. per file: fs.read -> fs.patch {edits: [...], expect_sha}
4. shell.run {script: "python -m compileall -q src && ruff check .", ...}
5. on failure: fs.undo_task {task_id}
```

Search with word boundaries (`\\b`), or `foo_bar_baz` gets renamed too. Case-sensitivity
defaults to the filesystem's own; pass `ignore_case: true` explicitly when you want
it, and expect more hits.
