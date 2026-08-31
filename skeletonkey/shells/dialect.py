"""Shell dialect rendering: preamble + user script + appendix, per dialect.

Design rule (ADR-0002): **never rewrite the user's script.** We only prepend a
preamble (encoding/strictness) and append an appendix (exit-code sentinel,
session state). Rewriting (indenting into a try/except, injecting `set -e`
mid-stream) shifts line numbers in tracebacks and breaks here-docs/heredoc-ish
PowerShell here-strings, which is exactly what an autonomous agent needs to read.
Consequence: when a script dies early, no sentinel arrives, and that is
information (`completed=False`, process return code used) rather than a lie.

Quoting
-------
`quote_arg` / `quote_args` produce a token that is safe to *embed in a script body* for one
dialect. Prefer `shell.run {argv: [...]}` whenever the value is an argument rather than part
of the program text: argv goes straight to `execve`/`CreateProcess`, so no shell parser ever
sees it and there is nothing to get wrong.

Sentinel protocol
-----------------
`<<<SK1|<token>|rc=<n>|done=1>>>` on stdout, followed by NUL-delimited state.
`token` is random per call so script output that *prints* the literal sentinel
pattern is rejected (we know the token, the script cannot guess it in advance).
"""

from __future__ import annotations

import re
import shlex
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

# strip_ansi is re-exported from here: shells/__init__ and base.py both pull it
# through this module, so the ANSI/OSC handling lives in exactly one place.
from ..core.util import new_run_id, short_hash, strip_ansi  # noqa: F401 (re-export)

SENTINEL_PREFIX = "<<<SK1"
STATE_SEP = "\x00"


class UnsupportedDialect(ValueError):
    """Renderer has no rules for this dialect - fail loudly, never guess a shell."""


@dataclass
class RenderOptions:
    dialect: str
    strict: bool = True
    utf8: bool = True
    capture_state: bool = False        # cwd + (env where supported)
    capture_env: bool = False
    login: bool = False                # source profile / -l
    stdin_text: str | None = None
    extra_args: list[str] = field(default_factory=list)
    on_windows: bool = False
    shell_major: int = 5
    trace: bool = False                 # print commands as executed (bash -x / Set-PSDebug)
    dry_run_wrap: bool = False          # emit a wrapper that reports intent, not execution


@dataclass
class RenderedScript:
    dialect: str
    argv: list[str]
    payload: str
    token: str
    delivery: str = "file"             # file | stdin | arg
    suffix: str = ".sh"
    bom: bool = False
    stdin_text: str | None = None
    parse_notes: list[str] = field(default_factory=list)
    payload_path: str | None = None    # set by the runner once written to disk

    @property
    def display_command(self) -> str:
        return " ".join(self.argv)


# --------------------------------------------------------------------- preamble


def bash_preamble(opts: RenderOptions, token: str) -> str:
    """Preamble + the *finisher* function, so the sentinel survives `exit N`.

    Agent scripts end with `exit 0`, `exit 1`, `exec ...`, or die on `set -e` far
    more often than humans do. If the completion report only ran as the script's
    last statement, every one of those looked like an unfinished run. An EXIT trap
    covers all of them. A script that installs its own EXIT trap replaces ours;
    the runner then falls back to the process exit status (see ShellRunner._finish).
    """
    lines: list[str] = []
    if opts.strict:
        # errexit: fail fast. pipefail: don't let `cmd | head` hide a broken cmd.
        # `set -u` is deliberately omitted: unbound-var aborts are more often agent
        # noise than real bugs when scripts read env vars conditionally.
        lines.append("set -o pipefail 2>/dev/null || true")
        lines.append("set -e")
    if opts.utf8:
        lines.append('export LC_ALL="${LC_ALL:-C.UTF-8}" LANG="${LANG:-C.UTF-8}"')
    if opts.trace:
        lines.append("set -x")
    lines.append("__sk_emitted=''")
    finisher = ['__sk_finish() {']
    finisher.append('  [ -n "$__sk_emitted" ] && return 0')
    finisher.append('  __sk_emitted=1')
    finisher.append('  __sk_rc="$1"')
    # done=0 when a signal cut the script short: the sentinel proves our wrapper ran,
    # not that the user's script reached its end. Reporting done=1 here once turned a
    # timeout into "completed" - which is exactly the lie an agent must not get.
    finisher.append('  __sk_flag="${2:-1}"')
    finisher.append('  __sk_cwd="$(pwd 2>/dev/null || echo "")"')
    if opts.capture_env and opts.dialect == "bash":
        # NUL-delimited k=v survives newlines in values; base64+tr keeps it on one
        # line so the line-oriented sentinel protocol still works. Degrades to empty
        # on hosts without base64/tr - reported, never guessed.
        finisher.append("  __sk_env=''")
        finisher.append("  if command -v base64 >/dev/null 2>&1 && command -v tr >/dev/null 2>&1; then")
        finisher.append("    __sk_env=$(for __sk_k in $(compgen -e 2>/dev/null); do "
                        "printf '%s=%s\\000' \"$__sk_k\" \"${!__sk_k}\"; done "
                        "| base64 | tr -d '\\012')")
        finisher.append("  fi")
    elif opts.capture_env:
        finisher.append('  __sk_env=""  # env capture needs bash (compgen); cwd-only here')
    finisher.append(f'  printf "{SENTINEL_PREFIX}|{token}|rc=%s|done=%s>>>\n" '
                    f'"$__sk_rc" "$__sk_flag"')
    if opts.capture_state or opts.capture_env:
        finisher.append(f'  printf "{SENTINEL_PREFIX}|{token}|cwd=%s>>>\n" "$__sk_cwd"')
    if opts.capture_env:
        finisher.append(f'  printf "{SENTINEL_PREFIX}|{token}|env64=%s>>>\n" "$__sk_env"')
    finisher.append('}')
    lines.append("\n".join(finisher))
    lines.append("trap '__sk_finish \"$?\" 1' EXIT")
    lines.append("trap '__sk_finish 130 0' INT")
    lines.append("trap '__sk_finish 143 0' TERM")
    return "\n".join(lines) + "\n"


def powershell_preamble(opts: RenderOptions) -> str:
    modern = (opts.shell_major or 7) >= 7
    lines: list[str] = [
        "$ErrorActionPreference = 'Stop'",
        # 5.1's "Latest" strict mode forbids innocuous idioms agents write constantly
        # ($undefined.Count, property access on nullable), so we pin 2.0 there.
        f"Set-StrictMode -Version {'Latest' if modern else '2.0'}",
    ]
    if opts.trace:
        lines.append("Set-PSDebug -Trace 1")
    if opts.utf8:
        if opts.on_windows:
            # chcp fixes the console; the two Encoding assignments below are what
            # actually fix *redirected* (piped) output. Both are needed.
            lines.append("try { & chcp.com 65001 > $null } catch { }")
        lines.append("$OutputEncoding = [System.Text.UTF8Encoding]::new($false)")
        lines.append("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)")
        if modern:
            lines.append("$PSStyle.OutputRendering = 'PlainText'")
    if modern and opts.shell_major >= 7 and opts.strict:
        # 7.3+: native (non-cmdlet) failure sets $? = false, so the sentinel sees it.
        # Below 7.3 a native failure is silent unless the script checks $? itself.
        lines.append("if ($PSVersionTable.PSVersion.Major -gt 7 -or "
                     "($PSVersionTable.PSVersion.Major -eq 7 -and $PSVersionTable.PSVersion.Minor -ge 3)) { "
                     "$PSNativeCommandUseErrorActionPreference = $true }")
    return "\n".join(lines) + "\n"


def python_preamble(opts: RenderOptions) -> str:
    lines = ["import sys as __sk_sys", "import os as __sk_os"]
    if opts.utf8:
        lines.append("__sk_sys.stdout.reconfigure(encoding='utf-8', errors='replace') if "
                     "hasattr(__sk_sys.stdout,'reconfigure') else None")
        lines.append("__sk_sys.stderr.reconfigure(encoding='utf-8', errors='replace') if "
                     "hasattr(__sk_sys.stderr,'reconfigure') else None")
    lines.append("__sk_rc = 0")
    return "\n".join(lines) + "\n"


PREAMBLE = {"bash": bash_preamble, "sh": bash_preamble, "zsh": bash_preamble, "fish": bash_preamble,
            "pwsh": powershell_preamble, "powershell": powershell_preamble, "python": python_preamble}


# ---------------------------------------------------------------------- appendix


def bash_appendix(opts: RenderOptions, token: str) -> str:
    """Normal-path completion. The real work lives in `__sk_finish` (see preamble);
    calling it here keeps output ordering deterministic, and the guard flag means a
    script that also exits (or is trapped) cannot emit the sentinel twice."""
    return '__sk_finish "$?"\n'


def powershell_appendix(opts: RenderOptions, token: str) -> str:
    lines = [
        # $? is True/False from the last statement; $LASTEXITCODE carries native tools.
        "$__sk_rc = if ($?) { 0 } else { 1 }",
        "if ($null -ne $LASTEXITCODE -and 0 -ne $LASTEXITCODE) { $__sk_rc = $LASTEXITCODE }",
    ]
    if opts.capture_state or opts.capture_env:
        lines.append('$__sk_cwd = (Get-Location).ProviderPath')
    if opts.capture_env:
        # base64 per value: survives newlines/tabs/Unicode in env vars
        lines.append("$__sk_lines = Get-ChildItem Env: | ForEach-Object { "
                     '"{0}={1}" -f $_.Name, ($_.Value -replace "`n", " ") }')
        lines.append('$__sk_env = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($__sk_lines -join "`n")))')
    lines.append(f'Write-Output "{SENTINEL_PREFIX}|{token}|rc=$__sk_rc|done=1>>>"')
    if opts.capture_state or opts.capture_env:
        lines.append(f'Write-Output "{SENTINEL_PREFIX}|{token}|cwd=$__sk_cwd>>>"')
        if opts.capture_env:
            lines.append(f'Write-Output "{SENTINEL_PREFIX}|{token}|env64=$__sk_env>>>"')
    # exit with the *script's* status, not PowerShell's default (which is 0 unless told)
    lines.append("exit $__sk_rc")
    return "\n".join(lines) + "\n"


def python_appendix(opts: RenderOptions, token: str) -> str:
    lines = [
        f"print('{SENTINEL_PREFIX}|{token}|rc=' + str(__sk_rc) + '|done=1>>>')",
    ]
    if opts.capture_state or opts.capture_env:
        lines.append(f"print('{SENTINEL_PREFIX}|{token}|cwd=' + __sk_os.getcwd() + '>>>')")
    if opts.capture_env:
        lines.append("import base64 as __sk_b64, json as __sk_json")
        lines.append(f"print('{SENTINEL_PREFIX}|{token}|env64=' + __sk_b64.b64encode("
                     "__sk_json.dumps(dict(__sk_os.environ)).encode()).decode() + '>>>')")
    return "\n".join(lines) + "\n"


# python needs the user body wrapped to observe exceptions - done with an
# exec() wrapper instead of re-indenting the script (keeps tracebacks honest).
PYTHON_WRAPPER = (
    "import sys as __sk_sys, os as __sk_os, traceback as __sk_tb\n"
    "__sk_rc = 0\n"
    "try:\n"
    "    exec(compile(__sk_script, '<skeletonkey>', 'exec'), {'__name__': '__main__', '__builtins__': __builtins__})\n"
    "except SystemExit as __sk_e:\n"
    "    __sk_rc = (1 if __sk_e.code is None else (int(__sk_e.code) if not isinstance(__sk_e.code, str) else 1))\n"
    "    if isinstance(__sk_e.code, str) and __sk_e.code:\n"
    "        print(__sk_e.code, file=__sk_sys.stderr)\n"
    "except BaseException as __sk_e:\n"
    "    __sk_tb.print_exc()\n"
    "    __sk_rc = __sk_rc or 1\n"
)


# --------------------------------------------------------------------- render


def make_token() -> str:
    return short_hash(new_run_id(), 10)


DIALECT_FAMILY = {
    "bash": "posix", "sh": "posix", "zsh": "posix", "fish": "posix",
    "pwsh": "powershell", "powershell": "powershell",
    "python": "python",
}


def dialect_family(dialect: str) -> str:
    """`posix` | `powershell` | `python`, or raise. `fish` is treated as posix: our
    single-quoted form is identical there, and we would rather state one rule than
    pretend to a fish-specific one we never test."""
    fam = DIALECT_FAMILY.get(dialect)
    if fam is None:
        raise UnsupportedDialect(f"no quoting rules for dialect {dialect!r}")
    return fam


def quote_arg(value: Any, dialect: str = "bash") -> str:
    """Render one value as a literal token for a *script body*.

    Not needed for `shell.run {argv}` (that never passes a shell parser), and not a
    general escaping: the result is correct for embedding where a single token is
    expected, not inside a double-quoted PowerShell string or a heredoc body.
    """
    text = value if isinstance(value, str) else str(value)
    if "\x00" in text:
        raise UnsupportedDialect("NUL is not representable in a script argument")
    fam = dialect_family(dialect)
    if fam == "posix":
        return shlex.quote(text)
    if fam == "powershell":
        # Single quotes are PowerShell's literal form: no $ expansion, no backtick
        # escape, and doubling is the only rule. A trailing backslash does not need
        # the treatment it demands inside double quotes.
        return "'" + text.replace("'", "''") + "'"
    return repr(text)  # a python literal; round-trips through ast.literal_eval


def quote_args(values: list[Any], dialect: str = "bash") -> list[str]:
    return [quote_arg(v, dialect) for v in values]


def command_line(argv: list[str], dialect: str = "bash") -> str:
    """A copy-pasteable rendering of a command, quoting each token for `dialect`."""
    return " ".join(quote_arg(a, dialect) for a in argv)


def render(script: str, *, shell_path: str, shell_version: tuple[int, ...] = (0,),
           options: RenderOptions | None = None, **kw: Any) -> RenderedScript:
    """Produce the payload + argv for one script execution.

    All three dialects are symmetric: preamble + verbatim body + appendix. No
    body rewriting, ever (see module docstring / ADR-0002).
    """
    opts = options or RenderOptions(dialect=kw.pop("dialect", "bash"), **kw)
    token = make_token()
    major = shell_version[0] if shell_version else 0
    if major:
        opts.shell_major = major
    body = (script or "").replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"

    # ------------------------------------------------------------------ python
    if opts.dialect == "python":
        payload = python_preamble(opts) + body + python_appendix(opts, token)
        return RenderedScript(
            dialect="python", argv=[shell_path, "-u", "{script}"], payload=payload, token=token,
            delivery="file", suffix=".py", stdin_text=opts.stdin_text,
            parse_notes=(["PYTHONPATH/argv isolation left on: -u only (no -I/-E) so venvs keep working"]
                         if not opts.strict else []),
        )

    # -------------------------------------------------------------- powershell
    if opts.dialect in ("pwsh", "powershell"):
        pre = powershell_preamble(opts)
        payload = pre + body + powershell_appendix(opts, token)
        legacy = opts.dialect == "powershell" and 0 < major < 6
        argv = [shell_path, "-NonInteractive"]
        if not opts.login:
            argv.append("-NoProfile")
        if not legacy:
            argv.append("-NoLogo")
        argv += ["-ExecutionPolicy", "Bypass", "-File", "{script}"]
        if legacy:
            # WinPS 5.1 reads BOM-less files as ANSI -> mangles non-ASCII literals.
            return RenderedScript(dialect=opts.dialect, argv=argv, payload="\ufeff" + payload, token=token,
                                  delivery="file", suffix=".ps1", bom=True,
                                  parse_notes=["UTF-8 BOM written for the Windows PowerShell 5.1 parser"])
        return RenderedScript(dialect=opts.dialect, argv=argv, payload=payload, token=token,
                              delivery="file", suffix=".ps1")

    # ---------------------------------------------------------------- unix-like
    if opts.dialect in ("bash", "sh", "zsh", "fish"):
        payload = bash_preamble(opts, token) + body + bash_appendix(opts, token)
        argv = [shell_path]
        if opts.dialect == "bash":
            argv += (["-l"] if opts.login else ["--noprofile", "--norc"])
        elif opts.dialect == "zsh":
            argv += ([] if opts.login else ["-f"])
        elif opts.dialect == "fish":
            argv += ["--no-config"] if not opts.login else []
        argv += ["-c", "{script}"] if opts.extra_args and opts.extra_args[0] == "inline" else ["{script}"]
        return RenderedScript(dialect=opts.dialect, argv=argv, payload=payload, token=token,
                              delivery="file", suffix=".sh", stdin_text=opts.stdin_text)

    raise UnsupportedDialect(f"no renderer for dialect {opts.dialect!r}")


def _dedup(items: list[str]) -> list[str]:
    seen: list[str] = []
    for i in items:
        if i not in seen:
            seen.append(i)
    return seen


# ---------------------------------------------------------------- parse output
_SENT_RE = re.compile(r"<<<SK1\|(?P<token>[0-9a-f]{6,16})\|(?P<rest>[^>]*)>>>")


@dataclass
class SentinelData:
    token: str = ""
    rc: int | None = None
    done: bool = False
    cwd: str | None = None
    env_raw: str | None = None
    env64: str | None = None
    head: str = ""            # stdout with sentinel lines removed
    tail: str = ""


def parse_sentinel(stdout: str, token: str, *, dialect: str = "bash") -> SentinelData:
    """Split our protocol out of the child's output.

    Two properties that both cost a bug once:
      * the payload is returned byte-exact - no rstrip, and text sharing a line with
        a sentinel (a script that ended without a newline) is kept, not eaten;
      * only lines carrying *this run's* token count, so anything else that looks
        like a sentinel in the output is passed through as data.
    """
    data = SentinelData(token=token, head=stdout or "")
    if not stdout:
        return data
    first: re.Match[str] | None = None
    for m in _SENT_RE.finditer(stdout):
        if m.group("token") != token:
            continue
        if first is None:
            first = m
        for piece in m.group("rest").split("|"):
            k, _, v = piece.partition("=")
            if k == "rc":
                data.rc = int(v) if v.lstrip("-").isdigit() else None
            elif k == "done":
                data.done = v == "1"
            elif k == "cwd":
                data.cwd = v or None
            elif k == "env":
                data.env_raw = (data.env_raw or "") + v
            elif k == "env64":
                data.env64 = v or None
    if first is not None:
        data.head = stdout[:first.start()]
        data.tail = stdout[first.start():]
    return data


def env_from_b64(blob: str | None, *, dialect: str) -> dict[str, str]:
    """Decode the base64 env blob from a sentinel line (per-dialect separator)."""
    if not blob:
        return {}
    import base64
    import json

    try:
        decoded = base64.b64decode(blob).decode("utf-8", "replace")
    except Exception:
        return {}
    if dialect == "python":
        try:
            return {str(k): str(v) for k, v in json.loads(decoded).items()}
        except ValueError:
            return {}
    sep = "\x00" if dialect in ("bash", "sh", "zsh", "fish") else "\n"
    out: dict[str, str] = {}
    for line in decoded.split(sep):
        k, sep, v = line.partition("=")
        if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            out[k] = v
    return out


def env_from_nul(blob: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in blob.split(STATE_SEP):
        if not chunk:
            continue
        k, sep, v = chunk.partition("=")
        if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            out[k] = v
    return out


# ------------------------------------------------------------------ CLIXML
_CLIXML_HEAD = "#< CLIXML"
_PSE = "{http://schemas.microsoft.com/powershell/2004/04}"


def decode_clixml(text: str) -> tuple[str, list[str], bool]:
    """Expand PowerShell's redirected-error XML into readable lines.

    Returns (clean_text, error_messages, had_clixml). Without this, an agent
    sees `&#xD;` soup instead of `Get-Process : Cannot bind argument...`.
    """
    if not text:
        return text, [], False
    if _CLIXML_HEAD not in text and "<Objs" not in text:
        return text, [], False
    had = True
    errors: list[str] = []
    plain: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == _CLIXML_HEAD or not stripped:
            continue
        if not stripped.startswith("<Objs"):
            plain.append(line)
            continue
        try:
            root = ET.fromstring(stripped)
        except ET.ParseError:
            # tolerate truncated streams by extracting S/T fragments
            for m in re.finditer(r"<(?P<tag>[ST]) S=\"(?P<stream>\w+)\">(?P<body>.*?)</(?P=tag)>", stripped, re.S):
                msg = _unescape(m.group("body"))
                if m.group("stream") == "Error":
                    errors.append(msg)
                else:
                    plain.append(msg)
            continue
        for node in root:
            stream = node.attrib.get("S", "")
            body = "".join(node.itertext())
            msg = _unescape(body)
            if stream == "Error":
                errors.append(msg)
            elif stream in ("Warning", "Verbose", "Debug"):
                plain.append(f"[{stream.lower()}] {msg}")
            elif stream == "Progress":
                continue
            else:
                plain.append(msg)
    out = "\n".join([*plain, *errors]).rstrip()
    return out, errors, had


def _unescape(text: str) -> str:
    return (text.replace("&#xD;", "\r").replace("&#xA;", "\n").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&"))


# ---------------------------------------------------------------- JSON extract


def extract_json(text: str) -> tuple[Any, str | None]:
    """Best-effort: parse whole text, else the last balanced {...}/[...] block.

    Agents piped through `Write-Output`/`print` often get banners around JSON;
    failing to parse that is a false negative we can cheaply avoid.
    """
    if not text:
        return None, "empty output"
    t = text.strip()
    try:
        return __import__("json").loads(t), None
    except ValueError:
        pass
    dec = __import__("json").JSONDecoder()
    best: tuple[int, Any] | None = None
    for i, ch in enumerate(t):
        if ch not in "{[":
            continue
        try:
            obj, end = dec.raw_decode(t[i:])
        except ValueError:
            continue
        cand = (i + end, obj)
        if best is None or cand[0] > best[0]:
            best = cand
    if best is None:
        return None, "no JSON object found in output"
    return best[1], None


