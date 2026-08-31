---
name: shell-crossplatform
description: >-
  Write shell calls that behave identically on Windows and Linux/macOS: choosing
  bash vs pwsh vs python, quoting, exit codes, encoding, and long-running jobs.
when_to_use: >-
  Running any command; porting a command between platforms; when a command fails
  only on one host; when output looks mangled, empty, or full of CLIXML noise.
version: "2"
tags: [shell, windows, linux, portability, pwsh, bash]
priority: 70
requires: [shell]
allowed-tools: [shell.run, shell.quote, shell.quote_check, shell.selftest, shell.available,
                shell.jobs, shell.job_wait, shell.job_watch, shell.job_kill]
---

# Cross-platform shell calls

The toolkit runs your script verbatim inside a per-shell preamble/appendix. Most
"the command worked locally but failed there" incidents are one of five causes:
wrong dialect chosen, quoting, exit-code semantics, encoding, or line endings.

## 1. Pick the dialect deliberately

- POSIX host: `bash`. Windows host: `pwsh` (PowerShell 7) — it is UTF-8 by
  default, has `&&`/`||`, and `Get-ChildItem`-style structured output.
- `python` is the escape hatch when you need identical behaviour on both, e.g.
  text munging, JSON, CSV, path arithmetic. It costs a process start and buys
  determinism.
- Do not assume `pwsh` exists on Windows (`allow_legacy_powershell` may fall back
  to 5.1) and do not assume `bash` exists on Windows (Git-Bash may be absent).
  Call `shell.available` once per task and read `preferred_dialect`,
  `dialects`, and each probe's `version`.
- If a script must run on both, write it in `python`, not in a lowest-common
  subset of bash.

```
shell.available {}                      -> dialects, versions, caveats
shell.run {script, dialect: "pwsh"}     -> explicit when you know why
```

## 2. Quoting and paths

- Never embed a Windows path in a bash script with backslashes: `"C:\temp\x"` is
  an escape soup. Use forward slashes (`C:/temp/x`) — Win32 accepts them — or a
  single-quoted PowerShell string.
- PowerShell 5.1 does not honour single quotes as literal for *variables* inside
  double quotes the way 7 does; interpolate explicitly: `"$env:TEMP\x"`.
- Pass data to a script on **stdin** (`stdin_text`) instead of interpolating it
  into the script text. That removes the entire quoting-bug class and keeps the
  rendered command readable in the ledger.
- Filenames with spaces: quote per dialect (`"..."` for both; `'...'` is *not*
  quoting in cmd-style strings).

## 3. Exit codes are the only status

- The runner detects completion with a signed sentinel, but the **contract** for
  you is `exit_code` plus `completed` and `timed_out`.
- `completed=false` with a non-negative `exit_code` means the script ran to a
  normal stop but never reached our appendix (it called `exit`/`sys.exit`);
  trust `exit_code`. `timed_out=true` means we killed the process group.
- bash: strict mode adds `set -o pipefail; set -e`, so `false | cat` fails and an
  unhandled error aborts the rest of the script. If you *want* the lenient
  behavior, pass `strict: false`.
- PowerShell: `$ErrorActionPreference='Stop'` is set for you, and on 7.3+
  `$PSNativeCommandUseErrorActionPreference` makes a native non-zero exit throw.
  A native tool failing mid-script therefore raises instead of continuing; catch
  it if the failure is expected: `cmd /c tool.exe; if ($LASTEXITCODE -ne 0) { … }`.
- Never signal success with stdout text. `exit 0` / `exit 1` is the channel.

## 4. Encoding and line endings

- The preamble sets `LC_ALL`/`LANG` to a UTF-8 locale and `PYTHONUTF8=1`;
  PowerShell gets `chcp 65001` plus `[Console]::OutputEncoding`. Do not fight it.
- Read/write files through `fs.*` tools when you can: they preserve the file's own
  CRLF/EOL and encoding instead of normalizing them, which a shell redirect
  (`>`, `Out-File`) will not.
- `Out-File` on 5.1 defaults to UTF-16LE; if a script must write a file, use
  `Set-Content -Encoding utf8NoBOM` (7+) or `[IO.File]::WriteAllText()`.
- Redirection in PowerShell is *not* byte-preserving. `command > file.txt`
  re-encodes and appends a newline.

## 5. Noise you will see, and what it means

- `#< CLIXML <Objs …` on stderr: PowerShell serializing error/stream records for a
  redirected host. The runner decodes it (`clixml_decoded: true`) — if you still
  see raw XML, redirect the specific stream: `2>&1 | Out-String`.
- "warning: PowerShell is in NonInteractive mode" — expected; we always pass
  `-NonInteractive`, so never prompt for input. Read credentials from env/files.
- A `SKELETONKEY_RUN=1` env var is set for every child: use it to detect that a
  script is running under us (e.g. skip progress bars).

## 6. Long-running work

Never `sleep`-poll inside a blocking call. Use:

```
shell.run {script: "make -j8", background: true}
    -> data {job_id, next_call: {tool: "shell.job_wait", ...}}
shell.job_wait  {job_id, timeout_s: 60, tail_bytes: 4000}   # "is it done?"
shell.job_watch {job_id, until: "<regex>", timeout_s, poll_s}  # "is it ready?"
shell.job_kill  {job_id}
```

`job_wait` blocks for the exit; `job_watch` blocks for a *line* (a build that prints
`OK` long before it exits is a `job_watch`). A `job_watch` timeout returns
`timed_out: true` and leaves the job running — watching never kills.

Timeouts kill the whole process group; the `kill_tree` key in the `[shell]` section of config
is what turns that off, and a single call cannot choose for itself, because a child that reaps
its own children is rare enough not to earn a per-call escape hatch.

## 7. State across calls

Each call is a fresh process. To keep `cd`/`export`/`$env:` between calls, pass the
same `session` id; the runner captures cwd (and env, on bash) in the appendix and
replays it. `shell.sessions` shows what is held; `shell.session_reset` drops it.
Prefer absolute paths over sessions when the task is short — sessions are shared
state and an autopilot may reuse them.

## 8. Tools this pack ships

The pack is not only prose: `tool.toml` compiles two callables.

- `shell.quote_check {script, target_dialect?}` — static pass over script text for the
  hazards in the table below, with the line and the fix. Run it before a long or risky
  script, not before every one-liner; it is lexical, so a match in a comment is still a
  match.
- `shell.selftest {}` (unadvertised, `tools`-visible) — this host's real behaviour as one
  JSON object: separators, encoding, `pipefail`/`errexit` semantics, redirection
  fidelity. Call it once per dialect and diff the two `result` objects when a script has
  to run on both; that is cheaper than trusting any document, including this one.

Both are ordinary manifests: they go through the sandbox, the budget, the ledger, and
`expects` parsing like every other call. `docs/SKILLS-SPEC.md` is how you would add a
third.

## Anti-patterns

| Don't | Do instead |
| --- | --- |
| `bash -c "for f in $(ls *.txt); do sed -i … ; done"` | `fs.glob` + `fs.patch` per file |
| `&& echo OK` to prove success | read `exit_code` |
| `Get-Content file -Raw` then string-replace | `fs.read` + `fs.patch` (undoable) |
| assuming `/tmp` exists on Windows | `shell.run {script: '$env:TEMP'}` or config tempdir |
| piping secrets as argv | `stdin_text` or a file under a sandbox root |
| `chmod`/`icacls` in a script for a file you could just name | `fs.chmod {path, mode}` - journalled, identical on both platforms, and it reports the bits Windows dropped |
