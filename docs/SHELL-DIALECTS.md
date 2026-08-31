# Shell dialects: bash, PowerShell, python

`skeletonkey/shells/dialect.py` renders and parses; `skeletonkey/shells/base.py` runs,
sessions and jobs. The contract for a **script**, in every dialect:

1. your body is executed **verbatim** — no `set -e` insertion into your text, no
   re-indentation, no line reordering (ADR-0002);
2. your **stdout and stderr are captured separately**; the payload around them is
   protocol noise you must ignore;
3. your **final printed value is the return value**: text by default, JSON if the call
   asked for `expects: "json"`;
4. a **non-zero exit is the failure signal**; write the real reason to stderr and it
   comes back as a `NONZERO_EXIT` envelope.

## Rendered payload shape

```
<preamble>            # dialect setup (see below)
<user body, verbatim>
<appendix>            # exit-code capture + sentinel + state capture
```

The appendix reads the exit code *before* anything else (`SK1_RC=$?` as its first line),
so a `set -e` abort mid-body still produces a sentinel instead of an
`output_unparsable` false alarm.

### bash / sh (`posix`)

```bash
#!/bin/sh
set -u                      # + -e -o pipefail when strict (default true)
… LC_ALL=C, UTF-8 setup …
<user body>
SK1_RC=$?
__sk_finish() { … rc, done=1 … printf '<<<SK1|<token>|rc=%s|done=1>>>\n'
                [cwd=…] [env64=…] ; }
trap '__sk_finish "$?" 1' EXIT
trap '__sk_finish 130 0' INT
trap '__sk_finish 143 0' TERM
```

Invoked directly as `/bin/bash --noprofile --norc <script-file>`: the payload is written
to a temp file under the state dir and `{script}` is substituted with its path, so a
user's rc files never enter the payload. Those isolation flags are dropped only when the
caller passes `login: true`. `sh` is the fallback, and `sh` cannot capture the environment
(`compgen` is a bash-ism), which is reported in `notes`, not silently skipped. The child
gets its own process group (`start_new_session` on POSIX,
`CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` on Windows), which is what makes a timeout
tree-kill real instead of leaving a `node` orphan running after we report failure.

`python` is not a fallback for shell work: it is a separate dialect with its own
contract (see below).

### PowerShell (`pwsh` 7+, `powershell` 5.1)

```powershell
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest      # '2.0' on 5.1: Latest forbids idioms agents write constantly
[Console]::OutputEncoding = …UTF8   # + InputEncoding, $OutputEncoding when utf8
<user body>
# appendix: sentinel via Write-Host, cwd/env captured in a finally, then EAP -> Continue
```

5.1 is a supported dialect, not a deprecated afterthought: strict mode is pinned to 2.0
there, `Set-Variable -Options AllScope` is avoided, the sentinel is printed with
`Write-Host` (so it bypasses the pipeline), and **stderr is CLIXML** — which
`dialect.decode_clixml()` unwraps back into text (a `<Objs … S='…'>` blob in an agent's
face is a bug, and there is a test for it). pwsh startup is slow, so the engine's timeout
slack is scaled by `timeout_scale = 1.15` for this family.

### argv, per dialect

| dialect | argv |
| --- | --- |
| `bash` | `[/bin/bash, --noprofile, --norc, <file>]` (`-l` instead of the first two when `login: true`) |
| `sh` / `zsh` / `fish` | `[<shell>, (-f │ --no-config)?, <file>]` |
| `pwsh` | `[pwsh, -NonInteractive, -NoProfile, -NoLogo, -ExecutionPolicy, Bypass, -File, <file>]` |
| `powershell` 5.1 | same, minus `-NoLogo` (and the payload gets a **UTF-8 BOM**: 5.1 reads BOM-less files as ANSI and mangles non-ASCII literals) |
| `python` | `[python, -u, <file>]` — deliberately not `-I`/`-E`, so an activated venv keeps working (said out loud in `notes`) |

Delivery is always a script **file** under `<state>/shell/` — never `-c`, because a payload
containing `"`, a backtick or `$(…)` must not survive a second round of shell parsing, and
a file is what lets a replay re-execute the exact bytes. `RenderedScript.delivery`
(`file | stdin | arg`) reserves the other modes for P7 remote targets; only `file` is
produced today. The file is deleted after the run unless the call passes `keep_script:
true`, which returns `data.script_path` — inside the state dir, hence readable with our own
`fs.read`, which is how a payload that failed only in CI gets attached to a bug report
without anyone retyping it.

### python

`python_preamble` wraps the body:

```python
import sys, json
def __sk_run():
    <user body indented under it>
try:
    __sk_out = __sk_run()
    …print(json)
finally:
    sys.stdout.flush()
```

Rules: `SystemExit(n)` becomes the exit code, and `main()` is called **only if you define it**; `sys.argv[1:]` is `json.dumps(args)`,
a JSON object (`load = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}`) whose keys
are your top-level names, read with `.get()`; printing JSON to stdout is the other way to
return; an **uncaught exception is an error** (stderr carries the traceback) while
`raise SystemExit(0)` is fine. Module imports at top level, no third-party deps unless the
skill declares them.

## The sentinel

```
<<<SK1|<token>|rc=<int>|done=1>>>        end of stream marker + exit code
<<<SK1|<token>|cwd=<b64>>>              final working directory      (session)
<<<SK1|<token>|env64=<b64 k=v\0 pairs>>> environment snapshot        (session)
```

`token` is random per call (ADR-0003): a *missing* token means the process died before
the appendix ran (`output_unparsable: true`, exit code unknown — so a mutating command is
never retried blind), and a token mismatch means something else wrote to our stdout.
`parse_sentinel` scans lines by `SENTINEL_PREFIX` and ignores anything else.

## Environment and cwd capture (why it exists)

The point of `capture_state` is not the env dump: **each session re-sets `cwd` and the
captured environment at the start of every call**, which is exactly what a real shell does
and what makes `cd ../..` persist. `env_mode` is `inherit` (default), `clean` (only `env` survives) or `login` (profile
sourced, slower and less reproducible); there is no allow-list mode. `shell.run` returns env
values only when you pass `capture_env: true`; `shell.sessions` returns names only.

## Arguments and quoting

`shell.run {script, argv}` appends `argv` to the process command line, after the script:
bash sees `$1..$n` (and `"$@"`), PowerShell sees `$args`, python sees `sys.argv[1:]`. No
shell parser ever touches those bytes — we hand the list to `execve`/`CreateProcess` — so
`$HOME` stays `$HOME`, `*glob*` stays a literal, and `it's` needs no surgery. That is why
`argv` is the default answer to "how do I get this value into the script".

Rules the runner enforces, because a mistake here is silent corruption: every entry must be
a string (numbers are accepted and stringified; a `bool` and anything else is `BAD_ARGS`,
since `True` becoming `"True"` in a path is a bug nobody catches), at most 128 entries, and
no NUL bytes (which would truncate the argument at the syscall boundary). Structured input
goes in as one element: `argv: [json.dumps(obj)]`, read back with
`json.loads(sys.argv[1])` in python or `"$1"` + `jq` elsewhere — or use `stdin_text`, which
has none of these limits.

`shell.quote {args[], dialect, shape}` exists for the remaining case — a value that has to
be *inside* the program text (a `sed` expression, a here-doc line, a `printf` format). It
renders each value with that dialect's literal rules: `shlex.quote` for posix, doubled
single quotes for PowerShell (its literal form: no `$` expansion, no backtick escape), and a
source literal for python. `shape: "tokens"` returns the list, `"command"` the joined line,
`"both"` (default) the two plus the unquoted `argv` you should probably use instead.

What quoting does **not** cover: a quoted token is correct where one token is expected, not
inside a double-quoted PowerShell string, not inside a here-doc body, and not as part of a
JSON document you are assembling by hand — for all three, pass a file (`fs.write`) or one
`argv` element holding `json.dumps(...)`. The renderer will not save you from a `"` in a
here-doc; nothing can, so the guidance is structural: build the payload out of `argv` and
files, and keep `script` as the fixed program text.

## Background jobs

`shell.run {background: true}` returns `{job_id, pid, status: "running", next_call:
{tool: "shell.job_wait", args: {job_id}}}`. `shell.jobs` lists every job with the output
*tail* (the ring buffer, not the whole log) and `stdout_bytes`; the full log is a spill
artifact, so `fetch_rest` is a legal `fs.read`. `shell.job_wait {job_id, timeout_s,
tail_bytes}` blocks and reports `timed_out: true` without killing anything when the timeout
loses the race. `shell.job_kill {job_id, tree}` terminates the process group (SIGTERM then
SIGKILL on POSIX), which is the only way a `npm run watch` child does not outlive its
parent. There are deliberately no shorter aliases for those two - the ids are
`shell.job_wait` and `shell.job_kill`, and `shell.jobs` takes no arguments at all.

## Failure modes, honestly

| Symptom | Meaning |
| --- | --- |
| `exit_code != 0`, `output_unparsable: false` | the script failed **and** we know its exit code — the normal failure |
| `output_unparsable: true` | the process died before the appendix (killed, segfault, `exit` inside a subshell) — treat as unknown, do not auto-retry a mutating command |
| `MISSING_BINARY` | the dialect is not on this host (`details.available_dialects` lists what is) |
| `DENY_RULE` + `details.allowed` | the dialect is on the host but policy denies it (`shell.deny_dialects` is subtracted from the allow-list; `shell_available` reports the same thing, so the two never disagree) |
| `TIMEOUT` + `next_action: background` | killed after `timeout + 2 s` slack; retry with `background: true` |

## Testing this on a Linux box

PowerShell behaviour is verified two ways without Windows: `render()` output is asserted
as a *string* (preamble order, sentinel protocol, `Write-Host`, CLIXML decode) and the
runner is exercised end-to-end on `posix`. `@pytest.mark.win` marks the tests that need a
real `powershell`/`pwsh`; they `skip` with a reason elsewhere, and CI (P6) runs them on
`windows-latest`.
