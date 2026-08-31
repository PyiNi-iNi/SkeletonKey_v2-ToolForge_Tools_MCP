# ADR-0003 — One sentinel protocol, random token per call

- **Status:** accepted
- **Date:** 2026-08
- **Deciders:** Dime

## Context

`subprocess.run(...)` gives an exit code and two pipes. It does not give the final working
directory, the environment a session ended with, or a reliable signal that the script ran
to its last line rather than being killed. For an agent, the difference between "the
command failed" and "my wrapper lost the output" decides whether a mutating command is
safe to retry.

Naive approach: append `echo "EXIT:$?"` and parse it. Failure modes we actually hit in
earlier prototypes:

- the marker appears in the program's own output (a build log that prints `EXIT:0`);
- a script that aborts (or `exec`s into something) never prints it, so the wrapper cannot
  tell truncation from success;
- ANSI colours, progress bars and `tqdm` carriage returns glue the marker to other text;
- on Windows PowerShell 5.1, stderr arrives as CLIXML, so "read stderr" is not a strategy
  for real diagnostics;
- a fixed marker lets a hostile or merely chatty child **fabricate** a clean exit.

## Decision

All three dialects end with the same protocol, on stdout:

```
<<<SK1|<token>|rc=<int>|done=1>>>
<<<SK1|<token>|cwd=<base64>>>          only when a session is in play
<<<SK1|<token>|env64=<base64 k=v\0>>>\ only when env capture was asked for
```

- `token = short_hash(new_run_id(), 10)` — random **per call**. A missing token means "we
  never got there": the result is `output_unparsable: true` with an unknown exit code, and
  mutating scripts are then never auto-retried. A token that does not match is ignored, so
  a child printing a forged sentinel cannot make a failure look clean.
- `done=1` distinguishes "ran to the end" from "the trap fired on an abort", which is why
  `ShellOutcome.ok == (completed and exit_code == 0)` and not `exit_code == 0`.
- state lines are base64 so that a path with a newline, or an env value with a NUL or a
  `=` (very common: base64 secrets, `PATH` with `\r` on Windows), cannot desynchronise the
  parser.
- everything the wrapper prints is ignorable by construction: the parser scans for
  `SENTINEL_PREFIX` and treats all other bytes as the script's output.
- PowerShell emits the sentinel with `Write-Host` (bypasses the pipeline, so `$null = …`
  assignments and `Out-String` in user code cannot swallow it) and stderr is
  `decode_clixml()`-unwrapped before an agent sees it.

## Rejected alternatives

- **Separate fd / NUL-delimited framing.** Cleaner, and unusable on Windows (`pepipe`
  semantics differ; PowerShell cannot dup fds portably). A one-line sentinel per dialect is
  the same robustness with a fifth of the machinery.
- **Parse the last line for `exit code: N`.** Fabricable, and lost to any `tr -d '\n'`.
- **Ask the model to read a JSON file the wrapper writes.** Works, but doubles the failure
  surface (temp dir, cleanup, permissions) for information that fits in one line.

## Consequences

- (+) One parser for three dialects; `parse_sentinel` is pure and fuzz-friendly.
- (+) Sessions are honest: `cd` and `export` survive because the *next* call re-applies
  what this one reported, exactly like a real shell.
- (−) A script that redirects stdout to `/dev/null` for its whole run also hides the
  sentinel. Accepted and reported as `output_unparsable` rather than guessed, because
  silently "assuming 0" is the worse error.
- (−) `timeout` must outlive the appendix: the engine adds `+2.0 s` slack so a killed
  process group cannot beat its own trap to the wire.

## Verification

`tests/test_shell_runner.py`: `exit 3` vs. truncated script vs. `kill -9` vs. a script
that prints a **forged** sentinel (must be ignored) vs. `set -e` abort (trap must still
fire). `tests/test_dialects.py`: the appendix/first-statement contract per dialect, and the
`sh` "no env capture" note.
