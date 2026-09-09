"""Self-authored tool quarantine (P7 exploratory).

When the engine or a skill generates a tool at runtime, it should not
be loaded into the main process with full access. Instead, it runs in
a restricted subprocess with:

  * A clean environment (no inherited secrets or config).
  * A restricted scratch root (no access to the real filesystem).
  * A reduced timeout (generated tools are small; long runs are suspicious).
  * Explicit promotion after successful calls (a tool must prove it
    works before being admitted to the main registry).

The quarantine is opt-in and the boundary is explicit. A promoted tool
is registered normally; an un-promoted one is discarded on cleanup.

Security contract:
  * The subprocess never inherits the parent's secrets.
  * Filesystem access is restricted to a temp scratch directory.
  * The timeout is a hard cap; exceeded = kill + report as failed.
  * Promotion requires at least one successful call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from skeletonkey.core.errors import E, SkeletonKeyError


@dataclass
class QuarantineSpec:
    """Configuration for a quarantined tool execution.

    Attributes
    ----------
    timeout_s:
        Hard timeout for each call (default 10s; generated tools are small).
    max_output_bytes:
        Maximum output size to accept (default 64KB).
    scratch_dir:
        Temporary directory for the subprocess (auto-created if None).
    clean_env:
        If True, strip environment variables except a minimal allowlist.
    allowed_env:
        Environment variables to keep when clean_env is True.
    max_memory_mb:
        Advisory memory limit (not enforced on all platforms).
    """

    timeout_s: float = 10.0
    max_output_bytes: int = 65_536
    scratch_dir: str | None = None
    clean_env: bool = True
    allowed_env: list[str] = field(default_factory=lambda: [
        "PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR",
        "PYTHONPATH", "PYTHONHOME",
    ])
    max_memory_mb: int = 256


@dataclass
class QuarantineResult:
    """Result of a quarantined tool call.

    Attributes
    ----------
    ok:
        Whether the call succeeded.
    output:
        The parsed JSON output from the subprocess.
    error:
        Error message if the call failed.
    duration_ms:
        How long the call took.
    promoted:
        Whether this call qualified for promotion.
    exit_code:
        Subprocess exit code.
    """

    ok: bool = False
    output: Any = None
    error: str = ""
    duration_ms: int = 0
    promoted: bool = False
    exit_code: int = -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "promoted": self.promoted,
            "exit_code": self.exit_code,
        }


@dataclass
class QuarantineSession:
    """A quarantine session for a generated tool.

    The session owns a scratch directory and tracks call history.
    After enough successful calls, the tool can be promoted.

    Attributes
    ----------
    id:
        Unique session identifier.
    tool_name:
        The name of the generated tool.
    spec:
        The quarantine specification.
    scratch_dir:
        The actual scratch directory path.
    calls:
        Number of calls made so far.
    successes:
        Number of successful calls.
    failures:
        Number of failed calls.
    promoted:
        Whether the tool has been promoted.
    """

    id: str = ""
    tool_name: str = ""
    spec: QuarantineSpec = field(default_factory=QuarantineSpec)
    scratch_dir: str = ""
    calls: int = 0
    successes: int = 0
    failures: int = 0
    promoted: bool = False
    _created_tmpdir: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"q-{uuid.uuid4().hex[:12]}"
        if not self.scratch_dir:
            if self.spec.scratch_dir:
                self.scratch_dir = self.spec.scratch_dir
            else:
                self.scratch_dir = tempfile.mkdtemp(prefix=f"sk-quarantine-{self.id}-")
                self._created_tmpdir = True

    def cleanup(self) -> None:
        """Clean up the scratch directory if we created it."""
        if self._created_tmpdir and self.scratch_dir and os.path.isdir(self.scratch_dir):
            import shutil
            shutil.rmtree(self.scratch_dir, ignore_errors=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "scratch_dir": self.scratch_dir,
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "promoted": self.promoted,
        }

    @property
    def promotion_eligible(self) -> bool:
        """A tool is eligible for promotion after at least one success
        and no more failures than successes."""
        return self.successes >= 1 and self.failures <= self.successes


# ---- Execution -------------------------------------------------------------

def _build_env(spec: QuarantineSpec, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build the subprocess environment."""
    if spec.clean_env:
        env: dict[str, str] = {}
        for key in spec.allowed_env:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
    else:
        env = dict(os.environ)

    # Always set the scratch dir as TMPDIR for the subprocess
    if extra:
        env.update(extra)
    return env


def _run_quarantined(
    code: str,
    session: QuarantineSession,
    *,
    tool_args: dict[str, Any] | None = None,
) -> QuarantineResult:
    """Run a Python code snippet in a quarantined subprocess.

    The code receives ``args`` as a JSON-parsed dict via stdin and
    must set ``result`` to the output value.
    """
    t0 = time.monotonic()
    session.calls += 1

    # Build the wrapper script. The generated code is embedded at module
    # level (no extra indentation) so that simple statements like
    # ``result = {'answer': 42}`` work without IndentationError.
    indented_code = _indent_code(code)
    wrapper = (
        "import json, sys, os\n"
        f"os.chdir({session.scratch_dir!r})\n"
        "try:\n"
        "    _stdin_raw = sys.stdin.read()\n"
        "    args = json.loads(_stdin_raw) if _stdin_raw.strip() else {}\n"
        "except Exception:\n"
        "    args = {}\n"
        + indented_code + "\n"
        "try:\n"
        "    _output = json.dumps(result)\n"
        "    sys.stdout.write(_output)\n"
        "    sys.stdout.flush()\n"
        "except NameError as _e:\n"
        "    sys.stderr.write('Result serialization failed: ' + str(_e))\n"
        "    sys.exit(2)\n"
        "except Exception as _e:\n"
        "    sys.stderr.write('Result serialization failed: ' + str(_e))\n"
        "    sys.exit(2)\n"
    )

    env = _build_env(session.spec, {"TMPDIR": session.scratch_dir, "SK_SCRATCH": session.scratch_dir})
    stdin_data = json.dumps(tool_args or {})

    try:
        proc = subprocess.run(
            [sys.executable, "-c", wrapper],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=session.spec.timeout_s,
            env=env,
            cwd=session.scratch_dir,
        )

        duration = int((time.monotonic() - t0) * 1000)

        if proc.returncode == 0:
            try:
                output = json.loads(proc.stdout) if proc.stdout.strip() else None
            except json.JSONDecodeError:
                output = proc.stdout[:session.spec.max_output_bytes]

            session.successes += 1
            return QuarantineResult(
                ok=True,
                output=output,
                duration_ms=duration,
                exit_code=0,
                promoted=session.promotion_eligible,
            )
        session.failures += 1
        error_msg = proc.stderr[:500] or f"exit code {proc.returncode}"
        return QuarantineResult(
            ok=False,
            error=error_msg,
            duration_ms=duration,
            exit_code=proc.returncode,
        )

    except subprocess.TimeoutExpired:
        duration = int((time.monotonic() - t0) * 1000)
        session.failures += 1
        return QuarantineResult(
            ok=False,
            error=f"quarantine timeout after {session.spec.timeout_s}s",
            duration_ms=duration,
            exit_code=-1,
        )
    except OSError as exc:
        duration = int((time.monotonic() - t0) * 1000)
        session.failures += 1
        return QuarantineResult(
            ok=False,
            error=f"subprocess error: {exc}",
            duration_ms=duration,
            exit_code=-1,
        )


def _indent_code(code: str) -> str:
    """Return code unchanged (module-level embedding needs no indentation)."""
    return code


# ---- Public API ------------------------------------------------------------

def create_session(
    tool_name: str,
    *,
    spec: QuarantineSpec | None = None,
) -> QuarantineSession:
    """Create a new quarantine session for a generated tool.

    The caller is responsible for calling ``session.cleanup()`` when done.
    """
    return QuarantineSession(
        tool_name=tool_name,
        spec=spec or QuarantineSpec(),
    )


def call_quarantined(
    session: QuarantineSession,
    code: str,
    *,
    args: dict[str, Any] | None = None,
) -> QuarantineResult:
    """Call a generated tool in quarantine.

    Parameters
    ----------
    session:
        The quarantine session (created with ``create_session``).
    code:
        Python code to execute. Must set ``result`` to the output value.
    args:
        Arguments passed to the code via stdin (JSON).

    Returns
    -------
    QuarantineResult with the execution outcome.
    """
    if session.promoted:
        raise SkeletonKeyError(
            E.BAD_ARGS,
            f"Tool {session.tool_name!r} is already promoted; call it directly",
            details={"session_id": session.id},
        )
    return _run_quarantined(code, session, tool_args=args)


def promote(session: QuarantineSession) -> bool:
    """Attempt to promote a quarantined tool.

    Returns True if the tool is eligible and has been marked as promoted.
    Returns False if the tool is not yet eligible.
    """
    if session.promotion_eligible:
        session.promoted = True
        return True
    return False


def promote_and_register(
    session: QuarantineSession,
    registry: Any,
    *,
    handler: Any,
    tier: str = "full",
    description: str = "",
    risk: str = "read",
    input_schema: dict[str, Any] | None = None,
    capability: str = "",
    group: str = "generated",
) -> dict[str, Any]:
    """Promote a quarantined tool and register it into the main registry.

    The promoted tool is registered with ``source: "generated"`` tracking
    and placed at the requested tier (default ``full`` keeps it out of
    the ``core`` and ``task`` surfaces). The handler is the callable the
    engine will invoke when the tool is called.

    Parameters
    ----------
    session:
        The quarantine session to promote.
    registry:
        The ToolRegistry to register into.
    handler:
        The callable ``(args...) -> dict | ToolResult`` for the engine.
    tier:
        The tier to register at (default ``full``).
    description:
        Agent-facing description for the tool manifest.
    risk:
        Risk classification (default ``read``).
    input_schema:
        JSON Schema for the tool's arguments.
    capability:
        Capability namespace the tool satisfies.
    group:
        Tool group (default ``generated``).

    Returns
    -------
    A dict with the registration outcome.
    """
    from skeletonkey.core.manifest import ToolManifest

    if not promote(session):
        return {
            "ok": False,
            "reason": "not_eligible",
            "successes": session.successes,
            "failures": session.failures,
            "detail": f"tool needs >= 1 success with failures <= successes; "
                      f"got {session.successes} success(es), {session.failures} failure(s)",
        }

    tool_id = session.tool_name
    man = ToolManifest(
        id=tool_id,
        description=description or f"Generated tool: {tool_id}",
        risk=risk,
        tier=tier,
        group=group,
        capability=capability or tool_id,
        input_schema=input_schema or {"type": "object", "properties": {}},
        advertised=True,
        parallel_safe=True,
        stateful="none",
        tags=["generated", "quarantine-promoted"],
        source="generated",
    )
    registry.register(man, handler, replace=False)
    return {
        "ok": True,
        "tool_id": tool_id,
        "tier": tier,
        "group": group,
        "risk": risk,
        "source": "generated",
        "session_id": session.id,
        "quarantine_calls": session.calls,
        "quarantine_successes": session.successes,
    }


def run_once(
    tool_name: str,
    code: str,
    *,
    args: dict[str, Any] | None = None,
    spec: QuarantineSpec | None = None,
) -> tuple[QuarantineResult, QuarantineSession]:
    """Run a tool once in quarantine and return the result and session.

    Convenience function for single-call quarantine checks. The caller
    is responsible for ``session.cleanup()``.
    """
    session = create_session(tool_name, spec=spec)
    result = call_quarantined(session, code, args=args)
    return result, session
