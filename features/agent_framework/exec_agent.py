"""Sandboxed Python execution + a generate→execute→feedback code agent.

The sandbox is a *defense*, not a *security boundary*: isolated
interpreter (``-I``), minimal env, temp cwd, wall-clock timeout with kill.
For hostile code use a real container (the toolkit's fsx sandbox).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from features.common.errors import ExecutionError
from features.common.openai_compat import ChatMessage

__all__ = ["ExecReport", "PythonExecAgent", "CodeReport", "CodeAgent"]

_CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


@dataclass
class ExecReport:
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float
    timed_out: bool = False
    code: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_sec": round(self.duration_sec, 6),
            "timed_out": self.timed_out,
            "ok": self.ok,
        }


class PythonExecAgent:
    """Runs Python code in an isolated subprocess.

    Isolation layers: ``-I`` (no PYTHONPATH/user-site), a minimal env, a
    temp cwd, and a preamble that strips any site-packages/dist-packages
    entries from ``sys.path`` so the code sees a bare stdlib regardless
    of venv.
    """

    _PREAMBLE = (
        "import sys as _sys\n"
        "_bad = ('site-packages', 'dist-packages')\n"
        "_sys.path = [p for p in _sys.path if not any(b in p for b in _bad)]\n"
    )

    def __init__(self, timeout: float = 10.0, workdir: str | None = None,
                 extra_env: dict[str, str] | None = None,
                 max_output: int = 100_000, sandbox_preamble: bool = True):
        self.timeout = timeout
        self.workdir = workdir
        self.extra_env = extra_env or {}
        self.max_output = max_output
        self.sandbox_preamble = sandbox_preamble

    def _env(self) -> dict[str, str]:
        # minimal, known environment — never inherit the full host env
        env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "USER", "LANG",
                                          "PYTHONHASHSEED", "TZ") if k in os.environ}
        env.setdefault("PATH", os.defpath)
        env.setdefault("HOME", tempfile.gettempdir())
        env.update(self.extra_env)
        return env

    async def run(self, code: str) -> ExecReport:
        cwd = self.workdir or tempfile.mkdtemp(prefix="sk-exec-")
        os.makedirs(cwd, exist_ok=True)
        program = (self._PREAMBLE + "\n" + code) if self.sandbox_preamble else code
        t0 = time.monotonic()
        timed_out = False
        out: bytes = b""
        err: bytes = b""
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", program,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=cwd, env=self._env(),
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                await proc.wait()
        except Exception as exc:  # noqa: BLE001
            raise ExecutionError(f"failed to start interpreter: {exc}") from exc
        duration = time.monotonic() - t0
        return ExecReport(
            exit_code=proc.returncode if proc is not None and proc.returncode is not None else -1,
            stdout=out.decode("utf-8", "replace")[: self.max_output],
            stderr=err.decode("utf-8", "replace")[: self.max_output],
            duration_sec=duration,
            timed_out=timed_out,
            code=code,
        )


@dataclass
class CodeReport:
    task: str
    code: str
    report: ExecReport
    rounds: int
    success: bool
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "code": self.code,
            "rounds": self.rounds,
            "success": self.success,
            "exec": self.report.to_dict(),
            "history": self.history,
        }


class CodeAgent:
    """Generate code for a task, execute it, feed errors back (N rounds)."""

    SYSTEM = (
        "You are a Python coding agent. Respond with ONLY a ```python code block "
        "that prints its result. No prose outside the block."
    )

    def __init__(self, engine: Any, exec_agent: PythonExecAgent | None = None,
                 max_rounds: int = 3, success_stdout_check: bool = True):
        self.engine = engine
        self.exec_agent = exec_agent or PythonExecAgent()
        self.max_rounds = max_rounds
        self.success_stdout_check = success_stdout_check

    @staticmethod
    def extract_code(text: str) -> str:
        m = _CODE_BLOCK_RE.search(text)
        if m:
            return m.group(1).strip()
        return text.strip()

    def _prompt(self, task: str, history: list[dict[str, Any]]) -> list[ChatMessage]:
        messages = [ChatMessage(role="system", content=self.SYSTEM),
                    ChatMessage(role="user", content=task)]
        for h in history:
            messages.append(ChatMessage(role="assistant", content=h["code"]))
            messages.append(ChatMessage(
                role="user",
                content=f"Last run failed (exit {h['exit_code']}).\n"
                        f"stderr:\n{h['stderr'][:2000]}\nFix and reply with a new code block."))
        return messages

    async def solve(self, task: str, model: str | None = None) -> CodeReport:
        from features.inference_engine.backends.base import GenerationParams

        history: list[dict[str, Any]] = []
        code = ""
        report: ExecReport | None = None
        for round_no in range(1, self.max_rounds + 1):
            code = self.extract_code(
                await self.engine.generate(model, self._prompt(task, history),
                                           GenerationParams(max_tokens=512)))
            report = await self.exec_agent.run(code)
            history.append({"round": round_no, "code": code,
                            "exit_code": report.exit_code,
                            "stderr": report.stderr, "stdout": report.stdout})
            success = report.ok and (
                (not self.success_stdout_check) or report.stdout.strip() != "")
            if success:
                return CodeReport(task=task, code=code, report=report,
                                  rounds=round_no, success=True, history=history)
        assert report is not None
        return CodeReport(task=task, code=code, report=report,
                          rounds=self.max_rounds, success=False, history=history)
