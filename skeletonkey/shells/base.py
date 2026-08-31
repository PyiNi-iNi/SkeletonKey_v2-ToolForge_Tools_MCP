"""ShellRunner - one execution path for bash / PowerShell / python.

Non-obvious decisions baked in here (each learned from real agent-run failures):

* script delivered as a **temp file**, not `-c <string>`: dodges argv length
  limits (Windows CreateProcess caps ~32k), quoting/escaping bugs, and keeps
  tracebacks/`Set-PSDebug` pointing at a real path.
* **separate** stdout/stderr pipes (never `2>&1`) so the sentinel stream and the
  error stream can be parsed independently.
* **process-group kill** on timeout: a plain `proc.kill()` on POSIX leaves the
  children of `bash -c 'npm run build'` alive holding ports/locks. On Windows we
  need `taskkill /T` because there are no process groups in the POSIX sense.
* `completion` is derived from the sentinel, so "exit 0 but never reached the
  end of the script" is distinguishable from success. That single bit saves
  agents from acting on half-executed state.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..core.errors import E, SkeletonKeyError
from ..core.profile import CapabilityProfile, ShellProbe
from ..core.util import is_windows, new_run_id, short_hash
from .dialect import (
    RenderedScript,
    RenderOptions,
    decode_clixml,
    env_from_b64,
    extract_json,
    parse_sentinel,
    render,
    strip_ansi,
)

MAX_CAPTURE = 4_000_000  # hard read cap per stream; beyond this we truncate on purpose


@dataclass
class ShellRequest:
    script: str
    dialect: str | None = None          # None -> resolved from profile/config
    cwd: str | None = None
    env: dict[str, str] | None = None   # merged over inherited env
    env_mode: str = "inherit"           # inherit | clean | login
    timeout_s: float = 120.0
    strict: bool = True
    login: bool = False
    tty: bool = False
    stdin_text: str | None = None
    capture_env: bool = False
    expects: str | None = None          # json | lines | None
    max_output_bytes: int | None = None    # None -> the runner's limit
    background: bool = False
    argv: list[str] | None = None          # appended after the script: $1..$n / $args / sys.argv[1:]
    trace: bool = False
    cleanup_script: bool = True
    session: str | None = None          # session id for persistent cwd/env
    on_timeout: str = "kill-tree"       # kill-tree | kill-self | ignore

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k not in ("script",) and v is not None}
        d["script_bytes"] = len(self.script.encode("utf-8"))
        return d


@dataclass
class ShellOutcome:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    completed: bool = False             # sentinel seen => script ran to the end
    timed_out: bool = False
    truncated: bool = False
    dialect: str = ""
    shell_path: str = ""
    duration_ms: int = 0
    rendered_command: str = ""
    script_path: str | None = None
    clixml_decoded: bool = False
    json: Any = None
    json_error: str | None = None
    lines_out: int = 0
    lines_err: int = 0
    session_state: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    killed: bool = False
    job_id: str = ""

    @property
    def ok(self) -> bool:
        return self.completed and (self.exit_code == 0)

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if v not in (None, "", False, [], 0) and k != "stdout"}
        out["stdout_lines"] = self.lines_out
        if self.stdout:
            out["stdout"] = self.stdout
        return out


def _extra_argv(req: ShellRequest) -> list[str]:
    """Validate and materialise `argv`.

    Values are passed to the child as process arguments, never through a shell parser, so
    there is nothing to quote and nothing to be clever about - which is the point. What we
    *do* check is the things that break an execve: wrong types, NUL bytes, and absurd
    counts (a model that loops `argv += argv` should fail here, not at the OS).
    """
    raw = req.argv or []
    if not isinstance(raw, list):
        raise SkeletonKeyError(E.BAD_ARGS, "shell.run argv must be a list of strings",
                              details={"found": type(raw).__name__,
                                       "advice": "pass [\"a b\", \"c\"]; a dict is not an argv"})
    if len(raw) > 128:
        raise SkeletonKeyError(E.BAD_ARGS, f"too many argv entries ({len(raw)} > 128)",
                              details={"count": len(raw), "advice": "put bulk input in stdin_text or a file"})
    out: list[str] = []
    for i, item in enumerate(raw):
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise SkeletonKeyError(
                E.BAD_ARGS, f"argv[{i}] must be a string (numbers are accepted)",
                details={"at": f"argv[{i}]", "found": type(item).__name__,
                         "advice": "stringify structured values, e.g. json.dumps(obj)"})
        text = str(item)
        if "\x00" in text:
            raise SkeletonKeyError(E.BAD_ARGS, f"argv[{i}] contains a NUL byte",
                                  details={"at": f"argv[{i}]"})
        out.append(text)
    return out


class ShellRunner:
    """Executes one-shot scripts and (Phase 1) cwd/env-continuous sessions."""

    def __init__(self, profile: CapabilityProfile | None = None, *,
                 allowed_dialects: list[str] | None = None, tempdir: str | None = None,
                 kill_tree: bool = True, utf8_enforce: bool = True,
                 allow_legacy_powershell: bool = True, strip_ansi_output: bool = False,
                 max_output_bytes: int = 200_000, sessions_enabled: bool = True) -> None:
        self.profile = profile
        self.allowed = allowed_dialects or ["bash", "pwsh", "python", "powershell", "sh", "zsh", "fish"]
        self.tempdir = tempdir
        self.kill_tree = kill_tree
        self.utf8 = utf8_enforce
        self.allow_legacy_powershell = allow_legacy_powershell
        self.strip_ansi = strip_ansi_output
        self.max_output_bytes = max_output_bytes
        self.sessions_enabled = sessions_enabled
        self._sessions: dict[str, SessionState] = {}
        self._jobs: dict[str, BackgroundJob] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ resolve
    def resolve(self, dialect: str | None) -> ShellProbe:
        """Pick a usable shell probe, honouring the profile (adaptive)."""
        if self.profile is None:
            raise SkeletonKeyError(
                E.MISSING_SHELL, "ShellRunner has no capability profile",
                details={"why": "dialect selection must never guess: the wrong shell "
                                "silently changes quoting and exit-code semantics",
                         "fix": "pass the profile from Toolkit/Engine, or run profile.probe first"},
                next_actions=[{"tool": "profile.probe", "args": {}}],
            )
        want = (dialect or self.profile.preferred_dialect()).lower()
        if want == "powershell.exe":
            want = "powershell"
        if want not in self.allowed:
            raise SkeletonKeyError(
                E.DENY_RULE, f"dialect {want!r} is not in the allowed set",
                details={"requested": want, "allowed": self.allowed,
                         "available": self.profile.available_dialects()},
            )
        probe = self.profile.shell(want)
        if probe is None and want == "pwsh" and self.allow_legacy_powershell:
            probe = self.profile.shell("powershell")
            if probe:
                probe = ShellProbe(**{**probe.__dict__})
                probe.notes = [*probe.notes, "pwsh absent: emulated with Windows PowerShell "
                                              "(CLIXML + legacy encoding rules applied)"]
        if probe is None or not probe.usable:
            raise SkeletonKeyError(
                E.MISSING_SHELL, f"no usable {want!r} shell on this host",
                details={"requested": want, "available": self.profile.available_dialects(),
                         "tried": want,
                         "receipt": [r.to_dict() for r in self.profile.probe_receipt
                                     if want.split(".")[0] in r.command]},
            )
        return probe

    # ------------------------------------------------------------------ execute
    def run(self, req: ShellRequest) -> ShellOutcome:
        probe = self.resolve(req.dialect)
        if req.session:
            if not self.sessions_enabled:
                raise SkeletonKeyError(E.DENY_RULE, "sessions are disabled by configuration",
                                       details={"setting": "shell.sessions_enabled"})
            sess = self.session(req.session)
            req.cwd = req.cwd or sess.cwd
            merged = dict(sess.env)
            merged.update(req.env or {})
            req.env = merged or None
            req.capture_env = True

        opts = RenderOptions(
            dialect=probe.dialect, strict=req.strict, utf8=self.utf8,
            capture_state=bool(req.session), capture_env=bool(req.capture_env) or bool(req.session),
            login=req.login, stdin_text=req.stdin_text, on_windows=is_windows(), trace=req.trace,
        )
        rendered = render(req.script, shell_path=probe.path, shell_version=probe.version, options=opts)

        script_path: str | None = None
        if rendered.delivery == "file":
            script_path = self._write_script(rendered, keep=not req.cleanup_script)
            rendered.payload_path = script_path
        argv = [script_path if a == "{script}" else a for a in rendered.argv]
        argv += _extra_argv(req)
        if req.background:
            job = self._spawn_background(argv, self._env(req, probe), req, rendered, script_path)
            outcome = ShellOutcome(dialect=probe.dialect, shell_path=probe.path,
                                   rendered_command=" ".join(argv), exit_code=None, completed=False,
                                   script_path=job.script_path, job_id=job.job_id,
                                   session_state={"job_id": job.job_id},
                                   notes=[f"background job id={job.job_id}",
                                          f"logs: {job.out_path} / {job.err_path}"])
            outcome.duration_ms = 0
            return outcome

        t0 = time.monotonic()
        try:
            outcome = self._spawn_and_wait(argv, self._env(req, probe), req, rendered, probe)
        finally:
            if script_path and req.cleanup_script:
                with_suppress(lambda p=script_path: os.unlink(p))

        outcome.duration_ms = int((time.monotonic() - t0) * 1000)
        outcome.rendered_command = " ".join(argv)
        outcome.dialect = probe.dialect
        outcome.shell_path = probe.path
        outcome.script_path = script_path if not req.cleanup_script else None
        if probe.notes:
            outcome.notes.extend(probe.notes)
        if req.session:
            self._apply_session_state(req.session, outcome)
        if req.expects == "json":
            payload, jerr = extract_json(outcome.stdout)
            outcome.json, outcome.json_error = payload, jerr
        elif req.expects == "lines":
            outcome.json = [ln for ln in outcome.stdout.splitlines() if ln.strip()]
        return outcome

    # ------------------------------------------------------------------- spawn
    def _spawn_and_wait(self, argv: list[str], env: dict[str, str], req: ShellRequest,
                        rendered: RenderedScript, probe: ShellProbe) -> ShellOutcome:
        creation = {}
        if is_windows():
            creation["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | \
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            creation["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE if rendered.stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=req.cwd or None,
                bufsize=-1, **creation,
            )
        except FileNotFoundError as exc:
            raise SkeletonKeyError(E.MISSING_BINARY, f"shell binary vanished: {argv[0]}",
                                   details={"path": argv[0], "error": str(exc)}) from exc
        except OSError as exc:
            raise SkeletonKeyError(E.IO, f"could not spawn {argv[0]}: {exc}",
                                   details={"argv0": argv[0], "errno": exc.errno}) from exc

        stdin_bytes = (rendered.stdin_text or "").encode("utf-8", "surrogateescape") \
            if rendered.stdin_text is not None else None
        timed_out = False
        cap = min(max(int(req.max_output_bytes or self.max_output_bytes or MAX_CAPTURE), 4096), MAX_CAPTURE)
        windows = _WindowPair(cap)
        try:
            out_b, err_b = self._communicate(proc, stdin_bytes, req, windows)
        except TimeoutHandled as exc:
            timed_out = True
            out_b, err_b = exc.partial
        except OSError as exc:
            self._kill(proc, req)
            raise SkeletonKeyError(E.IO, f"pipe error: {exc}", details={"argv": argv[:1]}) from exc

        rc = proc.returncode if proc.returncode is not None else -1
        clipped = len(out_b) > cap or len(err_b) > cap
        if clipped:
            out_b, _ = _cap(out_b, cap)
            err_b, _ = _cap(err_b, cap)
        clipped = clipped or bool(getattr(req, "_sk_dropped", 0))
        outcome = self._finish(_decode(out_b), _decode(err_b), rc, probe, timed_out, req,
                              token=rendered.token, truncated=clipped)
        if clipped:
            outcome.notes.append(f"output clipped to {cap} bytes (head+tail; middle elided)")
        return outcome

    def _communicate(self, proc: subprocess.Popen, stdin_bytes: bytes | None,
                     req: ShellRequest, windows: _WindowPair | None = None) -> tuple[bytes, bytes]:
        """Drain both pipes concurrently, bounded, without handing stdin to Popen.

        Popen.communicate() is the obvious choice and the wrong one here: it wants to
        own stdin (we have already closed it -> "flush of closed file"), it buffers an
        unbounded amount of RAM, and its partial output on timeout is empty - so a
        killed command reports nothing at all. Instead: close stdin first (no write
        deadlock), then pump each stream into a head+tail window that is readable at
        any moment, including mid-run.
        """
        limit = req.timeout_s
        wins = windows or _WindowPair(self.max_output_bytes)
        box: dict[str, Any] = {}

        def pump() -> None:
            try:
                if stdin_bytes is not None:
                    try:
                        assert proc.stdin is not None
                        proc.stdin.write(stdin_bytes)
                        proc.stdin.close()
                    except (BrokenPipeError, ValueError, OSError):
                        pass  # the script never read stdin; that is not our failure
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="sk-pipe") as ex:
                    f_out = ex.submit(wins.drain, proc.stdout, "out")
                    f_err = ex.submit(wins.drain, proc.stderr, "err")
                    f_out.result()
                    f_err.result()
                proc.wait()
                box["done"] = True
            except Exception as exc:
                box["exc"] = exc

        th = threading.Thread(target=pump, daemon=True, name=f"sk-{short_hash(str(time.time()), 6)}")
        th.start()
        th.join(timeout=limit)
        if th.is_alive():
            # Partial capture beats no capture: an agent debugging a hung command
            # needs whatever it printed before we pulled the plug.
            self._kill(proc, req)
            th.join(timeout=5)
            raise TimeoutHandled(wins.snapshot())
        if "exc" in box:
            raise OSError(str(box["exc"]))
        if wins.dropped:
            req._sk_dropped = wins.dropped  # type: ignore[attr-defined]
        return wins.out, wins.err


    def _finish(self, stdout: str, stderr: str, rc: int, probe: ShellProbe, timed_out: bool,
                req: ShellRequest, *, token: str, truncated: bool) -> ShellOutcome:
        """Combine sentinel truth with process truth. Sentinel wins when present."""
        notes: list[str] = []
        sent = parse_sentinel(stdout, token, dialect=probe.dialect)
        clean_stdout = sent.head
        env_state: dict[str, Any] = {}
        if sent.env64:
            env_state = env_from_b64(sent.env64, dialect=probe.dialect)

        stderr_clean, cli_errors, had_clixml = decode_clixml(stderr)
        if had_clixml:
            notes.append("stderr was PowerShell CLIXML; decoded to plain text")
            if cli_errors and not stderr_clean.strip():
                stderr_clean = "\n".join(cli_errors)
        if self.strip_ansi:
            clean_stdout, stderr_clean = strip_ansi(clean_stdout), strip_ansi(stderr_clean)

        completed = sent.done and sent.rc is not None
        effective_rc = sent.rc if completed else rc
        if not completed and not timed_out:
            # No sentinel means our appendix never ran: the script called `exit`/
            # `sys.exit` ahead of it, replaced our trap, or the shell died. A clean
            # numeric exit code is still real evidence the interpreter ran the
            # script to a stop, so report completion - but say how we know.
            if rc is not None and rc >= 0 and not sent.done:
                completed = True
                notes.append(f"completion inferred from process exit status {rc}; "
                             "no sentinel (cwd/env capture unavailable this way)")
            else:
                notes.append("no completion sentinel: script exited early, was killed, or "
                             f"the shell died (process rc={rc})")
        return ShellOutcome(
            stdout=clean_stdout, stderr=stderr_clean, exit_code=effective_rc, completed=completed,
            timed_out=timed_out, truncated=truncated, lines_out=len(clean_stdout.splitlines()),
            lines_err=len(stderr_clean.splitlines()), clixml_decoded=had_clixml,
            session_state=({"cwd": sent.cwd} if sent.cwd else {}) | ({"env": env_state} if env_state else {}),
            notes=notes, killed=timed_out,
        )

    # ------------------------------------------------------------------ helpers
    def _write_script(self, rendered: RenderedScript, keep: bool = False) -> str:
        d = self.tempdir or tempfile.gettempdir()
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"sk-{new_run_id()}{rendered.suffix}")
        enc = "utf-8-sig" if rendered.bom else "utf-8"
        with open(path, "w", encoding=enc, newline="\n") as fh:  # LF: CRLF breaks shebang-ish parsing
            fh.write(rendered.payload)
        if not keep:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        rendered.payload_path = path  # type: ignore[attr-defined]
        return path

    def _env(self, req: ShellRequest, probe: ShellProbe) -> dict[str, str]:
        base: dict[str, str] = {} if req.env_mode == "clean" else dict(os.environ)
        if req.env_mode == "login":
            req.login = True
        for k, v in (req.env or {}).items():
            if v is None:
                base.pop(k, None)
            else:
                base[k] = str(v)
        if self.utf8:
            base.setdefault("PYTHONUTF8", "1")
            base.setdefault("PYTHONIOENCODING", "utf-8")
            if probe.kind == "unix":
                base.setdefault("LC_ALL", "C.UTF-8")
                base.setdefault("LANG", "C.UTF-8")
        base.setdefault("SKELETONKEY_RUN", "1")
        if req.env_mode == "clean":
            keep = ("PATH", "SystemRoot", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE", "PATHEXT",
                    "WINDIR", "APPDATA", "LOCALAPPDATA", "ProgramData", "LANG", "LC_ALL")
            base = {k: v for k, v in base.items() if k in keep or k.startswith("SKELETONKEY_")}
        return base

    def _kill(self, proc: subprocess.Popen, req: ShellRequest) -> None:
        if req.on_timeout == "ignore" or proc.poll() is not None:
            return
        if req.on_timeout == "kill-self":
            proc.kill()
            return
        if is_windows():
            if self.kill_tree:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=10, check=False)
            else:
                proc.kill()
            return
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
            time.sleep(0.15)
            if proc.poll() is None:
                os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            with_suppress(proc.kill)

    # ------------------------------------------------------------- background jobs
    def _spawn_background(self, argv: list[str], env: dict[str, str], req: ShellRequest,
                          rendered: RenderedScript, script_path: str | None) -> BackgroundJob:
        job = BackgroundJob(job_id=f"job_{new_run_id()}", argv=list(argv), pid=None,
                            script_path=script_path, dialect=req.dialect or "?", started=time.time(),
                            token=rendered.token,
                            log_dir=os.path.join(self.tempdir or tempfile.gettempdir(), "sk-jobs"))
        os.makedirs(job.log_dir, exist_ok=True)
        job.out_path = os.path.join(job.log_dir, f"{job.job_id}.out")
        job.err_path = os.path.join(job.log_dir, f"{job.job_id}.err")
        out_fh = open(job.out_path, "ab", buffering=0)  # noqa: SIM115 - owned by job
        err_fh = open(job.err_path, "ab", buffering=0)  # noqa: SIM115
        flags = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if is_windows() \
            else {"start_new_session": True}
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=out_fh, stderr=err_fh, env=env,
            cwd=req.cwd or None, **flags)
        job.pid = proc.pid
        job._proc = proc  # type: ignore[attr-defined]
        job._fhs = (out_fh, err_fh)  # type: ignore[attr-defined]
        if script_path:
            threading.Thread(target=_cleanup_after, args=(proc, script_path), daemon=True).start()
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def job(self, job_id: str) -> BackgroundJob:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise SkeletonKeyError(
                E.ENOENT, f"no such job {job_id!r}",
                details={"known": sorted(self._jobs), "jobs": [j.summary() for j in self._jobs.values()]},
            ) from None

    def jobs(self) -> list[dict[str, Any]]:
        return [j.summary() for j in self._jobs.values()]

    def job_wait(self, job_id: str, *, timeout: float = 30.0, tail_bytes: int = 8000) -> dict[str, Any]:
        job = self.job(job_id)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rc = job.poll()
            if rc is not None:
                break
            time.sleep(min(0.25, max(0.01, (deadline - time.monotonic()) / 2)))
        out = _read_tail(job.out_path, tail_bytes)
        parsed = parse_sentinel(out, job.token) if job.token else None
        return {"job_id": job_id, "running": job.running, "exit_code": job.poll(),
                "elapsed_s": round(time.time() - job.started, 2),
                "completed": bool(parsed and parsed.done),
                "exit_code_from_sentinel": (parsed.rc if parsed else None),
                "stdout_tail": parsed.head if parsed else out,
                "stderr_tail": decode_clixml(_read_tail(job.err_path, tail_bytes))[0]}

    def job_kill(self, job_id: str, *, tree: bool = True) -> dict[str, Any]:
        job = self.job(job_id)
        return job.kill(tree=tree)

    # ------------------------------------------------------------------- sessions
    def session(self, sid: str) -> SessionState:
        with self._lock:
            if sid not in self._sessions:
                self._sessions[sid] = SessionState(sid=sid, cwd=os.getcwd(), env={}, calls=0)
            return self._sessions[sid]

    def _apply_session_state(self, sid: str, outcome: ShellOutcome) -> None:
        sess = self.session(sid)
        st = outcome.session_state or {}
        if st.get("cwd"):
            sess.cwd = st["cwd"]
        if isinstance(st.get("env"), dict) and st["env"]:
            sess.env.update(st["env"])
        sess.calls += 1
        sess.last_dialect = outcome.dialect

    def sessions(self) -> list[dict[str, Any]]:
        return [s.summary() for s in self._sessions.values()]

    def session_reset(self, sid: str | None = None) -> dict[str, Any]:
        with self._lock:
            if sid is None:
                n = len(self._sessions)
                self._sessions.clear()
                return {"cleared": n}
            gone = self._sessions.pop(sid, None)
            if gone is None:
                raise SkeletonKeyError(E.ENOENT, f"no such session {sid!r}",
                                       details={"known": sorted(self._sessions)})
            return {"cleared": 1, "session": sid}

    # --------------------------------------------------------------------- misc
    def which(self, name: str) -> str | None:
        return shutil.which(name)


def _cap(data: bytes, cap: int) -> tuple[bytes, int]:
    """Keep the head and the tail, drop the middle: the sentinel lives at the end,
    and so do the interesting first lines. Returns (bytes, dropped)."""
    if len(data) <= cap:
        return data, 0
    tail_n = min(4096, cap // 4)
    head_n = cap - tail_n - 64
    if head_n <= 0:
        return data[:cap], len(data) - cap
    dropped = len(data) - head_n - tail_n
    marker = f"\n...[{dropped} bytes elided]...\n".encode("utf-8", "replace")
    return data[:head_n] + marker + data[-tail_n:], dropped


class TimeoutHandled(Exception):
    """Internal: _communicate exceeded the wall clock and already killed the tree."""

    def __init__(self, partial: tuple[bytes, bytes] = (b"", b"")) -> None:
        super().__init__("shell command timed out")
        self.partial = partial  # whatever the child had printed when we cut it off


@dataclass
class SessionState:
    """cwd/env continuity across calls - the thing that makes 'cd then build' work."""

    sid: str
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    calls: int = 0
    last_dialect: str | None = None

    def summary(self) -> dict[str, Any]:
        # Names only. A session environment routinely holds tokens, and `shell.sessions`
        # is a listing tool - the host must not have to exfiltrate values into its own
        # context (and into any spilled artifact) to see what is set.
        return {"sid": self.sid, "cwd": self.cwd, "calls": self.calls,
                "env_keys": len(self.env), "last_dialect": self.last_dialect,
                "env_names": sorted(self.env)[:40]}


@dataclass
class BackgroundJob:
    job_id: str
    argv: list[str]
    pid: int | None
    script_path: str | None
    dialect: str
    started: float
    log_dir: str
    out_path: str = ""
    err_path: str = ""
    token: str = ""
    _proc: Any = None
    _fhs: tuple[Any, Any] = ()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def poll(self) -> int | None:
        return self._proc.poll() if self._proc is not None else None

    def kill(self, *, tree: bool = True) -> dict[str, Any]:
        if self._proc is None or self.poll() is not None:
            return {"job_id": self.job_id, "was_running": False, "exit_code": self.poll()}
        if is_windows() and tree:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.pid)],
                           capture_output=True, timeout=10, check=False)
        elif tree and not is_windows():
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGKILL)  # type: ignore[arg-type]
            except (ProcessLookupError, OSError):
                self._proc.kill()
        else:
            self._proc.kill()
        for fh in self._fhs:
            try:
                fh.close()
            except (OSError, ValueError):
                pass
        return {"job_id": self.job_id, "was_running": True, "killed": True, "exit_code": self.poll()}

    def summary(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "pid": self.pid, "running": self.running,
                "exit_code": self.poll(), "dialect": self.dialect,
                "elapsed_s": round(time.time() - self.started, 2),
                "log": self.out_path}


def _cleanup_after(proc: subprocess.Popen, path: str) -> None:
    try:
        proc.wait()
    finally:
        with_suppress(lambda: os.unlink(path))


def with_suppress(fn: Any) -> None:
    try:
        fn()
    except (OSError, ValueError):
        pass


def _decode(raw: bytes | None) -> str:
    if not raw:
        return ""
    # UTF-8 first (correct for PS7 / modern tools); cp1252 fallback beats
    # latin-1 for the WinPS/OEM cases and never raises.
    for enc in ("utf-8", "utf-16-le", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _read_tail(path: str, nbytes: int) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - nbytes))
            data = fh.read()
        return _decode(data)
    except OSError:
        return ""


def resolve_python(profile: CapabilityProfile | None = None) -> str:
    """Prefer the running interpreter so venv/site-packages line up with tools."""
    if profile:
        p = profile.shell("python")
        if p and p.usable:
            return p.path
    return sys.executable or "python3"


def available_dialects(profile: CapabilityProfile | None) -> list[str]:
    return profile.available_dialects() if profile else []



class _WindowPair:
    """Bounded head+tail windows over a child's stdout/stderr."""

    def __init__(self, cap: int) -> None:
        cap = max(int(cap or MAX_CAPTURE), 8192)
        self.head_budget = cap * 3 // 4
        self.tail_budget = cap // 4
        self._head = {"out": bytearray(), "err": bytearray()}
        self._tail = {"out": bytearray(), "err": bytearray()}
        self._dropped = {"out": 0, "err": 0}
        self._lock = threading.Lock()

    def feed(self, key: str, chunk: bytes) -> None:
        with self._lock:
            head, tail = self._head[key], self._tail[key]
            room = self.head_budget - len(head)
            if room > 0:
                head += chunk[:room]
                chunk = chunk[room:]
                if not chunk:
                    return
            tail += chunk
            overflow = len(tail) - self.tail_budget
            if overflow > 0:
                del tail[:overflow]
                self._dropped[key] += overflow

    def render(self, key: str) -> bytes:
        with self._lock:
            head, tail, dropped = self._head[key], self._tail[key], self._dropped[key]
            if not dropped:
                return bytes(head + tail)
            marker = f"\n...[{dropped} bytes elided]...\n".encode("utf-8", "replace")
            return bytes(head) + marker + bytes(tail)

    def drain(self, stream: Any, key: str) -> int:
        """Read `stream` to EOF. Draining past our window matters: a child blocked
        writing into a full pipe would hang the run, and a timeout with no output is
        a worse answer than a truncated one."""
        if stream is None:
            return 0
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            self.feed(key, chunk)
        return self._dropped[key]

    @property
    def out(self) -> bytes:
        return self.render("out")

    @property
    def err(self) -> bytes:
        return self.render("err")

    @property
    def dropped(self) -> int:
        return self._dropped["out"] + self._dropped["err"]

    def snapshot(self) -> tuple[bytes, bytes]:
        return self.out, self.err
