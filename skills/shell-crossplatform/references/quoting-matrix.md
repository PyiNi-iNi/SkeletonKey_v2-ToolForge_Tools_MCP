# Quoting and escaping matrix

The three dialects disagree on almost everything except "single quotes are literal
in PowerShell and bash, and in Python they are a string".

| Concern | bash / sh | PowerShell 7 | PowerShell 5.1 | python |
| --- | --- | --- | --- | --- |
| literal string | `'...'` (no escapes at all) | `'...'` (`''` to embed one) | same | `'...'`, `\` escapes still work |
| interpolation | `"$VAR"`, `$(cmd)` | `"$var"`, `$(cmd)`; also `"$env:NAME"` | same, but `${}` needed for `"$a.b"` | f-strings only |
| path separator | `/` only | `/` and `\` both work | `/` and `\` | `os.sep`; `/` works on Win too |
| quoting a Windows path | `"C:/a b/x.txt"` | `'C:\a b\x.txt'` | same | `r"C:\a b\x.txt"` |
| command with args in a var | `cmd=(ls -la); "${cmd[@]}"` (array, not string) | `$a = @('-NoProfile','-Command'); & pwsh @a` | same splatting | `subprocess.list2cmdline` |
| here-doc | `<<'EOF'` (quoted = no expansion) | `@' … '@` (quoted = literal) | same | `textwrap.dedent` of a raw triple-quote |
| null device | `/dev/null` | `$null` (never `>nul`) | `$null` | `subprocess.DEVNULL` |
| "everything" glob | `shopt -s globstar; **/*` | `-Recurse -Filter` | `-Recurse` (no `**`) | `pathlib.Path.rglob` |
| env var missing | `"$X"` → empty; `$X` also empty | `$env:X` → `$null`; `"$env:X"` → empty | same | `os.environ.get("X")` |
| exit of last command | `$?` (0/1 for *success flag*, `PIPESTATUS` for pipes) | `$LASTEXITCODE` (native) / `$?` (bool) | same | `sys.exit(n)` |
| stderr merge | `2>&1` | `2>&1` (but see note) | `2>&1` | `stderr=subprocess.STDOUT` |

## PowerShell redirection is not a byte pipe

`native.exe > out.txt` re-encodes to the *default file encoding* (5.1: ANSI/UTF-16
depending on host) and appends a newline; binary output is corrupted. If a
redirect must be faithful:

```powershell
& native.exe | Set-Content -Path out.txt -Encoding utf8NoBOM   # 7+
[IO.File]::WriteAllText($p, ((& native.exe | Out-String)))     # both
cmd /c "native.exe > out.txt"                                   # true passthrough
```

## `$ErrorActionPreference` and native exit codes

`$EAP='Stop'` (which our preamble sets) makes *cmdlets* throw. Native executables
historically only set `$LASTEXITCODE`. PowerShell 7.3+ has
`$PSNativeCommandUseErrorActionPreference` to make non-zero native exits throw as
well — our preamble enables it when the probe reports >= 7.3, because otherwise a
pipeline like `gcc x.c; Remove-Object a.out` deletes the source after a failed
compile.

If you *expect* a failure, guard it:

```powershell
$ErrorActionPreference = 'Continue'
& git diff --quiet HEAD
$clean = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = 'Stop'
```

## bash: what `set -e` does not catch

With `set -e` (our strict default) the script aborts on a failing simple command,
but **not** when the command is: part of a `&&`/`||` list, in an `if`/`while`
condition, in a pipeline that is not the last element (pipefail fixes the last
element), or followed by `|| true`. Subshells in `$(…)` do not inherit the abort:

```bash
count=$(wc -l < missing.txt)   # writes an error to stderr; $? = 1 but with set -e it aborts
files=$(ls /nope | wc -l)      # does NOT abort: assignment swallows the status in some shells
```

So: check what you capture. `x=$(cmd) || { echo "cmd failed: $?" >&2; exit 1; }`.

## The one rule that removes all of this

Pass data through `stdin_text` and files, and keep argv short. A script that reads
its payload from stdin has no quoting surface at all:

```json
{"script": "python -c \"import sys,json; d=json.load(sys.stdin); print(d['n'])\"",
 "stdin_text": "{\"n\": 42}", "dialect": "bash"}
```
