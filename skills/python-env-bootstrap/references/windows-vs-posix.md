# The same bootstrap on both host families

Everything below is the same operation twice, once for a POSIX family dialect and once for
PowerShell. The differences are not cosmetic: the layout, the launcher, and the failure modes
each change.

## Create the environment

```json
{"script": "set -euo pipefail\nroot=\"$1\"\nbase=\"$(command -v python3 || command -v python)\"\n\"$base\" -c 'import ensurepip' || { echo \"ensurepip missing: install the python3-venv package for $base\"; exit 3; }\n\"$base\" -m venv \"$root/.venv\"\n\"$root/.venv/bin/python\" -c 'import sys; print(sys.executable, sys.prefix != sys.base_prefix)'",
 "argv": ["/abs/repo"], "dialect": "bash", "timeout_s": 120}
```

```json
{"script": "$ErrorActionPreference = 'Stop'\n$root = $args[0]\n$base = (Get-Command py -ErrorAction SilentlyContinue)\nif ($base) { $py = 'py' } else { $py = (Get-Command python).Source }\n& $py -3 -m venv (Join-Path $root '.venv')\n& (Join-Path $root '.venv\\Scripts\\python.exe') -c 'import sys; print(sys.executable, sys.prefix != sys.base_prefix)'",
 "argv": ["C:\\repo"], "dialect": "pwsh", "timeout_s": 180}
```

Why the shapes differ:

- **The `ensurepip` probe.** On Debian/Ubuntu and their derivatives, `python3-venv` is a
  separate package; without it `python3 -m venv` fails *after* creating the directory, with
  the message about `ensurepip` three lines down. Probing first turns a half-created venv
  into one clear exit code (3 here) and one sentence about what to install.
- **`py -3`, not `python`.** On Windows the useful entry point is the launcher, which can pick
  a version; bare `python` may resolve to the Store alias instead (below).
- **Backslashes.** `Join-Path` avoids hand-built paths; in a POSIX script the same care is
  quoting. A Windows path inside a bash script is what `shell.quote_check` flags as
  `win-path`, and it is a real hazard here, not a style note.
- **Timeouts.** venv creation on Windows copies files instead of linking them (the default
  there); on a large base interpreter that is seconds slower than POSIX, so a 30 s timeout
  that never fires in CI on Linux will fire on a Windows runner.

## The Store alias

`%LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe` is an *app execution alias*: it is on
`PATH`, it satisfies `Get-Command python`, and when invoked it opens the Microsoft Store and
exits with either no output or a nonzero code, depending on the build. It is not an
interpreter. Detect it before using anything found on `PATH`:

```json
{"script": "$p = (Get-Command python -ErrorAction SilentlyContinue).Source\nif ($p -like '*\\WindowsApps\\*') { \"alias: $p\"; exit 4 } else { \"real: $p\" }",
 "dialect": "pwsh", "timeout_s": 20}
```

or, when the launcher is present, skip the question and go through `py -3`, which never
resolves to the alias.

## Line endings and the venv's own scripts

A venv contains generated shell scripts whose shebang and line endings were fixed at creation
time. Two consequences:

1. `.venv/bin/*` written on Linux and read on Windows (or the reverse, via a shared checkout
   with `core.autocrlf` on) is a script with CRLF endings and a `#!` that does not exist
   there. Nothing in the venv is portable across hosts — recreate it per host.
2. Do not commit `.venv` at all, and check that the ignore rule is actually in effect with
   `git check-ignore -v .venv`. If `fs.sniff` reports CRLF in a script that should be LF,
   the checkout is lying to you and the fix is the checkout configuration, not the file.

## Which interpreter for which job

| Job | Which interpreter | Why |
| --- | --- | --- |
| the app / tests | the venv python | it is the only one whose `sys.path` matches the runtime |
| `uv`, `poetry` themselves | their own isolated install | a manager installed into the venv it manages gets upgraded out from under its own lockfile |
| a one-off data munging | any interpreter that has that one library | no venv is the right tool for reading a JSON file |
| anything under CI | whatever the job's `PATH` says, printed at the start | the print costs nothing and the alternative is guessing which image layer won |
