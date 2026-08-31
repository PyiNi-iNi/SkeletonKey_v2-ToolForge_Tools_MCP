# ADR 0007: arguments are passed as argv; quoting is a separate, explicit tool

Date: 2026-08-31
Status: accepted
Affects: `skeletonkey/shells/{base,dialect}.py`, `tools/builtin.py` (`shell.run`,
`shell.quote`), `skills/shell-crossplatform/SKILL.md`, Phase 2's skill→tool compiler

## Context

`shell.run` took only `script` and `cwd`. Any value an agent wanted to hand to a program had
to be spliced into program text first, in every dialect: a path with a space, a filename with
an apostrophe, a heredoc body with an embedded quote. The toolkit's own skill even advised
"build your JSON/args in Python, emit exactly one line" — a workaround for a missing
affordance.

Nothing enforced correctness there. A quoting bug needs exactly one unquoted `"` in an
untrusted string, and Phase 2 turns agents into the authors of these payloads. A docs lint
also proved the tool did not exist while `docs/SHELL-DIALECTS.md` described it (see
`tests/test_docs.py`), which is the failure mode of documenting an intention.

## Options considered

1. **`argv` on the request, quoted by the OS-level spawn** — the values go to
   `execve`/`CreateProcess` directly and never meet a shell parser. Chosen.
2. **A `shell.quote` tool only** — agents still embed the result in script text, which is
   correct for one token and wrong inside a `"..."` string, a here-doc body, or hand-written
   JSON. Kept as the *secondary* affordance, because that residual case is real.
3. **Auto-detect `"'"` patterns and fix them** — rejected: it makes the failure silent, and
   a silently "fixed" command is a command nobody can reproduce from the ledger.
4. **Refuse unquoted values in scripts** (a deny-like lint) — rejected: too many legitimate
   one-liners trip it, and an over-broad rule trains agents to route around the toolkit.

## Decision

`argv` on the shell request is validated where a wrong type means corruption rather than an error:
list of `str` (ints/floats stringified, `bool` refused), ≤ 128 entries, no NUL — then spliced
after the script path, so bash gets `$1..$n`/`"$@"`, PowerShell gets `$args`, Python gets
`sys.argv[1:]`. Quoting is factored into `shells/dialect.py` (`quote_arg`, `quote_args`,
`command_line`, `DIALECT_FAMILY`), which `shell.quote` wraps for agents; handler code and the
tool share one implementation, so they cannot drift. the result records the effective list in `data` under `argv`, so a replay
has the whole invocation, and the argv file is cleaned up with the script.

## Consequences

Two quoting tests run real bash (argv round-trip; embed-and-run), so PowerShell and bash
literal forms differ by construction, not by memory. `fs.chmod` remains the one gap where an
agent must still shell out for a file operation — the roadmap's step 0c. The renderer contract
in `docs/SHELL-DIALECTS.md` now states both halves: the preamble is ours, the body is verbatim,
and values that must live in the body are quoted per family.
