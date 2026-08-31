# Recipes, in the shape the toolkit expects

Each recipe is one `shell.run` call: `script` holds the fixed part (what you wrote),
`argv` holds the variable part (what the task, the repo, or a file gave you). Dialect
differences are called out only where the *script text* differs — git's own output is
byte-identical on both hosts.

## Inspect, then decide

```json
{"script": "git -C \"$1\" status --porcelain=v2 --branch", "argv": ["/abs/repo"],
 "dialect": "bash", "expects": "lines", "timeout_s": 20}
```

`expects: "lines"` puts the branch line, the `1 A. N` file lines, and the untracked `?`
lines in `stdout_lines`, so the decision "is this tree dirty" is a count, not a regex.

## Commit a named set of paths

```json
{"script": "set -euo pipefail\ngit -C \"$1\" add -- \"$2\"\ngit -C \"$1\" diff --cached --quiet || git -C \"$1\" commit -q -F \"$3\"\ngit -C \"$1\" show --stat --oneline HEAD\ngit -C \"$1\" status --porcelain",
 "argv": ["/abs/repo", "src/parser.py", "/abs/repo/.git/SK_COMMIT_MSG"],
 "dialect": "bash", "expects": "lines"}
```

The message rides in a file written with `fs.write`, not on the command line: a message with
backticks, quotes, or a `$` in it survives `argv` but not a second round of parsing, and a
file leaves no trace in the process list. `diff --cached --quiet` exits 1 when there is
something to commit, hence the `||` — and with `set -e` the *whole* command still succeeds,
because the left side of `||` is allowed to fail.

PowerShell 5.1 has no `||`, so the same script becomes:

```json
{"script": "$ErrorActionPreference = 'Stop'\ngit -C $1 add -- $2\ngit -C $1 diff --cached --quiet\nif ($LASTEXITCODE -ne 0) { git -C $1 commit -q -F $3 }\ngit -C $1 show --stat --oneline HEAD\ngit -C $1 status --porcelain",
 "argv": ["/abs/repo", "src/parser.py", "/abs/repo/.git/SK_COMMIT_MSG"],
 "dialect": "pwsh", "expects": "lines"}
```

and `$1`/`$2`/`$3` become `$args[0]`, `$args[1]`, `$args[2]` if the runner is handing argv
as an array rather than positionally — `shell.selftest` tells you which, for this host.

## Refuse a dirty tree without a human

```json
{"script": "test -z \"$(git -C \"$1\" status --porcelain)\"; echo \"dirty=$?\"",
 "argv": ["/abs/repo"], "dialect": "bash"}
```

Exit 0 with `dirty=0` means clean. Read it as data; do not `git stash` to force your way
past it — an unexpected dirty tree is usually the signature of an earlier failed attempt,
and the content in it is the evidence for what went wrong.

## Verify the tip is what you think

```json
{"script": "git -C \"$1\" log -n 3 --format=%H%x1f%s%x1f%an <%ae>%x1f%ad --date=iso-strict",
 "argv": ["/abs/repo"], "dialect": "bash", "expects": "lines"}
```

`%x1f` (unit separator) instead of a space, because subjects contain spaces and authors
contain angle brackets; split on the `\x1f` byte rather than with a regex.

## Tags, releases, and the one that must not move

```json
{"script": "git -C \"$1\" tag -a \"$2\" -m \"$2\" && git -C \"$1\" show --no-patch --format=%(refname) refs/tags/$2",
 "argv": ["/abs/repo", "v0.3.0"], "dialect": "bash"}
```

Then, and only then, `git push origin refs/tags/v0.3.0`. A tag that exists upstream is
append-only: to correct it, add a new one and leave the old; deleting and re-pointing a
published tag is the git equivalent of overwriting a file you did not read first.

## Before you check anything in

`shell.quote_check` on any script you are about to put *into* the repo as a hook or CI step
— checked-in scripts outlive the session that wrote them, so `>nul`, an unquoted `rm -rf $VAR`,
and `Out-File` without an encoding are all worth catching while they are still text.
