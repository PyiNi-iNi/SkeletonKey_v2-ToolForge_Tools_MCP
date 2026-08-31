# ADR-0005 — Never fabricate a default; report what is unknown

- **Status:** accepted
- **Date:** 2026-08
- **Deciders:** Dime

## Context

An autonomous loop reads a result, decides, acts. A plausible-looking field that the
system invented is worse than a missing one, because the loop has no reason to doubt it.
Three places where the temptation is strongest, all hit in earlier prototypes:

- `fs.search` returns `{"count": 0}`. Is that "no matches", "the pattern was a literal
  string when you needed `--fixed-strings`", "the path is ignored by `fs.ignore`", or
  "rg is not installed and the walker skipped a directory"? All four look identical to the
  agent, and three of them end with the agent reporting *"there are no TODOs in this
  repo"* to a human.
- `fs.read` has to say what the file *is* — encoding, newline, BOM — before an edit. Guess
  `utf-8`/LF, rewrite a cp1252 CRLF file, and you have corrupted a user's file while
  reporting success.
- A tool is not advertised because the host lacks a binary. Returning an empty tool list,
  or "not found", teaches the agent nothing about what it may try next.

## Decision

Every surface distinguishes *empty*, *unknown*, and *blocked*:

| Surface | Rule |
| --- | --- |
| `fs.search` with no hits | `data.zero_match_advice` names the provider and `scanned_files`, and the flags worth retrying with. A zero is a fact about *this search*, never about the repo |
| `fs.sniff` | `encoding` / `newline` / `binary` derived from the bytes; `newline: "none"` for a file with no newline at all (not `"lf"`), and `binary: true` instead of a mojibake `content` |
| gates | `TOOL_NOT_ADVERTISED` carries `gate.unmet[]` **and** the probe `receipt[]` (command, exit code) that produced it, plus a `registry.search` next action |
| dialects | a denied dialect returns `DENY_RULE` with `details.allowed`/`details.available`; the reporting surface (`shell_available`) is filtered by the same policy so the two can never disagree |
| truncation | `data.inlined` + `spilled: true` + `total_bytes` + an artifact with the complete payload — never a cut-off JSON body |
| provider choice | `data.provider` always names the backend that answered (`ripgrep` / `python`); the walker adds a `notes` line about the semantics it lacks (`.gitignore` rules), `prefer: "ripgrep"` on a host without `rg` is a `MISSING_BINARY` naming `details.fallback` (and `prefer: "grep"` reports itself as `python`, because the walker will not claim to be something it is not) |
| journal | `fs.undo` warns when the file no longer matches what it wrote, instead of restoring over someone's edit |
| skills | a declared-but-unwired tool is registered and answers `NOT_IMPLEMENTED` with the phase that wires it, rather than being silently invisible |

Nothing in the codebase may write a "sensible" default for a value it did not observe. If
a probe did not run, the field is absent and the reason is in `notes`.

## Rejected alternatives

- **Return `count: 0` and let the agent re-check.** Cheaper to implement; it converts a
  tool bug into a wrong conclusion the human inherits.
- **`encoding: "utf-8"` when detection fails.** The file then round-trips wrong with an
  `ok: true` envelope — the single most damaging failure mode this toolkit can have.
- **Throw on unknown input.** An exception is not more honest than `"none"`, and it costs
  the agent its turn; the point is to *inform*, not to refuse.

## Consequences

- (+) Refusal-then-recovery becomes measurable: `PLAN.md` §6 tracks it, and an envelope
  that explains itself is what makes the number move.
- (+) Docs and skill bodies can be written as "if the result says X, do Y", because the
  result never says something it did not check.
- (−) More fields to design and more tests (`test_search_finds_content_and_explains_zero_matches`,
  `test_sniff_describes_the_bytes_before_the_agent_edits_them`, `test_sniff` parametrised
  over the encoding/newline table). Accepted: this is the product.
- (−) Envelopes are larger than a bare `{"count": 0}`; the byte/token budget in
  `core/envelope.py` is what keeps that from being a cost.

## Verification

`tests/test_fs_ops.py::test_sniff` (the BOM / no-newline / UTF-16-without-BOM rows),
`tests/test_tools_builtin.py::test_search_finds_content_and_explains_zero_matches`,
`test_sniff_describes_the_bytes_before_the_agent_edits_them`,
`tests/test_journal.py::test_undo_warns_when_the_content_is_not_what_we_wrote`,
`tests/test_tools_builtin.py::test_deny_dialects_denies` (policy + reporting agree).
