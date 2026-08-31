"""One implementation of "run a script and describe what happened".

`shell.run` and every skill-synthesized tool go through `run_script`, so a payload key or an
error code can never mean two different things depending on who called it. ADR 0007 made the
same argument about quoting; this is that argument applied to the envelope. The alternative -
skill tools calling `engine.call("shell.run", ...)` - was rejected on purpose: a nested call
would ask the agent to approve the *same* capability twice (once for the skill tool, once for
`shell.run`), which is how an autopilot loop ends up granting everything to make a prompt go
away.
"""

from __future__ import annotations

from typing import Any

from ..core.envelope import ToolResult
from ..core.errors import E, SkeletonKeyError, ToolError
from ..core.util import clip


def run_script(engine: Any, shells: Any, *, script: str, dialect: str | None = None,
               cwd: str | None = None, timeout_s: float = 120.0,
               env: dict[str, str] | None = None, env_mode: str = "inherit",
               strict: bool = True, expects: str | None = None, session: str | None = None,
               background: bool = False, stdin_text: str | None = None,
               capture_env: bool = False, keep_script: bool = False,
               argv: list[Any] | None = None, max_output_bytes: int | None = None,
               ctx: Any = None, extra_data: dict[str, Any] | None = None,
               result_key: str | None = None, via: str | None = None,
               owner: str | None = None) -> ToolResult:
    """Execute `script` and return the envelope `shell.run` is documented to return.

    `extra_data` is merged into `data` (skill tools use it to name their provenance), and
    `result_key` additionally lifts the parsed payload into `data[result_key]` so a synthesized
    tool has one obvious place to look instead of a shell-shaped bag.
    """
    from .base import ShellRequest

    resolved_cwd = cwd or (ctx.cwd if ctx else None) or engine.config.workspace
    # cwd is sandbox-checked too: an agent must not `cd /` and keep going
    if resolved_cwd:
        try:
            resolved_cwd = engine.fs.sb.resolve(resolved_cwd, intent="list").real
        except SkeletonKeyError as exc:
            if exc.code == "SANDBOX_VIOLATION":
                exc.details["note"] = "shell cwd is sandboxed like every other path"
            raise
    cap = int(max_output_bytes or engine.config.shell.max_output_bytes)
    req = ShellRequest(script=script, dialect=dialect, cwd=resolved_cwd, env=env, env_mode=env_mode,
                       timeout_s=float(timeout_s), strict=strict, expects=expects, session=session,
                       background=background, stdin_text=stdin_text, capture_env=capture_env,
                       cleanup_script=not keep_script, argv=list(argv) if argv else None,
                       max_output_bytes=cap, owner=owner)
    out = shells.run(req)
    data = shell_payload(out, cap)
    if extra_data:
        data.update(extra_data)
    if via:
        data["args_via"] = via
    if owner:
        data["owner"] = owner
    if req.argv:
        # echo what was passed: the envelope is the record of the call, and a
        # reproduction needs the script *and* its arguments
        data["argv"] = list(req.argv)
    if result_key:
        # one stable key for a synthesized tool's real answer, whatever the shell shape was
        if out.json is not None:
            data[result_key] = out.json
        elif expects == "lines":
            data[result_key] = out.stdout.splitlines() if out.stdout else []
        else:
            data[result_key] = out.stdout

    if background:
        # the turn shape: job_id + the exact next call, both in `data`, so a loop
        # can branch on them without parsing hints
        jid = out.session_state.get("job_id")
        data["job_id"] = jid
        data["next_call"] = {"tool": "shell.job_wait", "args": {"job_id": jid, "timeout_s": 30}}
        return ToolResult.success(
            data=data,
            next_actions=[{"tool": "shell.job_wait", "args": {"job_id": jid, "timeout_s": 30}}],
            hints=["job is running detached; poll with shell.job_wait",
                   "to wait for a specific line instead of for exit: "
                   "shell.job_watch {job_id, until: <regex>} (watching never kills)"],
            context={"job_id": jid})

    hints: list[str] = []
    next_actions: list[dict[str, Any]] = []
    if not out.completed and not out.timed_out:
        hints.append("script did not reach its final line: the rest did not run. Read stdout for "
                     "where it stopped before retrying the whole script.")
    if out.timed_out:
        hints.append(f"timed out after {timeout_s}s and the process tree was killed")
        next_actions.append({"tool": "shell.run", "args": {"script": clip(script, 200),
                                                            "timeout_s": min(1800, timeout_s * 3)},
                             "why": "raise the timeout or background=true"})
        next_actions.insert(0, {"tool": "shell.run", "args": {"background": True},
                                "why": "or run it detached"})
    if out.exit_code not in (0, None) and not out.timed_out:
        hints.append(f"exit {out.exit_code}: read stderr_tail; the command ran, it just failed")
    if expects == "json" and out.json_error:
        hints.append(f"json parse failed: {out.json_error}")
        next_actions.append({"tool": "shell.run", "args": {"expects": "text"},
                             "why": "inspect raw output instead of asserting json"})
    result = ToolResult.success(data=data, hints=hints, next_actions=next_actions,
                                context={"session": session} if session else {})
    if out.exit_code not in (0, None) or out.timed_out:
        result.ok = False
        result.error = shell_error(out, timeout_s)
        result.data = data  # keep the evidence attached to the failure
    return result


def shell_payload(out: Any, cap: int) -> dict[str, Any]:
    """The `data` shape `shell.run` promises. Keys here are a contract, not a dump."""
    payload: dict[str, Any] = {
        "exit_code": out.exit_code, "completed": out.completed, "timed_out": out.timed_out,
        "truncated": out.truncated, "duration_ms": out.duration_ms, "dialect": out.dialect,
        "stdout": clip(out.stdout, cap), "stderr_tail": clip(out.stderr, min(cap, 6000)),
        "stdout_lines": out.lines_out, "stderr_lines": out.lines_err,
    }
    if out.json is not None:
        payload["json"] = out.json
    if out.json_error:
        payload["json_error"] = out.json_error
    if out.clixml_decoded:
        payload["clixml_decoded"] = True
    st = out.session_state or {}
    if st.get("cwd"):
        payload["cwd_after"] = st["cwd"]
    if st.get("job_id"):
        payload["job_id"] = st["job_id"]
    if out.notes:
        payload["notes"] = out.notes
    # only present when keep_script was asked for: the way a payload that failed in
    # CI gets attached to a report without anyone retyping it
    if getattr(out, "script_path", None):
        payload["script_path"] = out.script_path
    return payload


def shell_error(out: Any, timeout_s: float) -> ToolError:
    """Map a failed run onto the taxonomy, with the evidence attached."""
    if out.timed_out:
        return ToolError.from_code(E.TIMEOUT, f"timed out after {timeout_s}s",
                                   details={"exit_code": out.exit_code, "killed": out.killed,
                                            "stderr_tail": clip(out.stderr, 1200)})
    return ToolError.from_code(
        E.NONZERO_EXIT, f"{'script aborted early' if not out.completed else 'command failed'} "
                        f"(exit {out.exit_code})",
        details={"exit_code": out.exit_code, "completed": out.completed, "dialect": out.dialect,
                 "stderr_tail": clip(out.stderr, 1500), "stdout_tail": clip(out.stdout, 1500)})
