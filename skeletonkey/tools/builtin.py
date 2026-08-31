"""Built-in Phase-1 tools: filesystem, multi-shell, profile, registry, journal.

Every handler is `(args...) -> dict | ToolResult`. Raise `SkeletonKeyError` for
anything the agent can act on; the engine turns it into a coded envelope. The
manifest metadata below is the contract the autopilot plans against, so risk /
reversibility / requirements are filled in for real, not decorative.
"""

from __future__ import annotations

from typing import Any

from ..core.envelope import ToolResult
from ..core.errors import E, SkeletonKeyError
from ..core.manifest import Requirement, ToolManifest
from ..core.util import clip

# --------------------------------------------------------------------- schemas

_PATH = {"type": "string", "description": "Path, relative to a workspace root or absolute inside one."}
_OB = {"type": "object", "additionalProperties": True}


def _env_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": {"type": ["string", "null"]},
            "description": "Extra env vars; null value unsets."}


TOOL_SPECS: list[ToolManifest] = []


def _spec(**kw: Any) -> None:
    TOOL_SPECS.append(ToolManifest(**kw))


# ============================================================== shell tool set
_spec(
    id="shell.run", title="Run a shell script",
    description="Execute a script in bash, PowerShell (7 or 5.1), or python and capture "
                "stdout/stderr/exit code. The script is delivered as a temp file, is never "
                "rewritten, and 'completed' tells you whether it reached the end. Use this for "
                "anything multi-step; use fs.* tools for file edits.",
    capability="exec.script", risk="write", parallel_safe=False, idempotent=False, reversible=False,
    open_world=True, typical_latency_ms=400, timeout_s=1800.0,
    require_any=[Requirement("capability", "shell.unix"), Requirement("capability", "shell.powershell"),
                  Requirement("shell", "python")],
    tags=["shell", "bash", "pwsh", "powershell", "python", "exec", "command", "run"],
    anti_patterns=["don't use shell.run to edit files - fs.patch gives you a diff and undo",
                   "don't chain unrelated commands in one call; a failure mid-script loses the rest"],
    see_also=["fs.patch", "shell.job_wait", "fs.search"],
    examples=[{"args": {"script": "git status --short", "dialect": "bash"}}],
    input_schema={
        "type": "object",
        "properties": {
            "script": {"type": "string", "minLength": 1, "description": "Script body, verbatim."},
            "dialect": {"type": "string", "enum": ["bash", "pwsh", "powershell", "python", "sh", "zsh", "fish"],
                        "description": "Omit to use the host's preferred shell."},
            "cwd": {"type": "string", "description": "Working directory (inside a root)."},
            "timeout_s": {"type": "number", "minimum": 0.1, "maximum": 1800},
            "env": _env_schema(),
            "env_mode": {"type": "string", "enum": ["inherit", "clean", "login"]},
            "strict": {"type": "boolean", "default": True,
                       "description": "bash: set -e + pipefail. pwsh: Stop + native error action."},
            "expects": {"type": "string", "enum": ["text", "json", "lines"],
                        "description": "json parses the last JSON value in stdout."},
            "session": {"type": "string", "description": "Persistent cwd/env bucket id."},
            "background": {"type": "boolean", "default": False},
            "stdin_text": {"type": "string"},
            "capture_env": {"type": "boolean", "default": False},
            "keep_script": {"type": "boolean", "default": False,
                            "description": "Leave the rendered payload on disk and return it as "
                                           "data.script_path, so a failure can be replayed byte for "
                                           "byte or read back with fs.read. Deleted by default."},
            "max_output_bytes": {"type": "integer", "minimum": 256, "maximum": 2_000_000},
        },
        "required": ["script"],
        "additionalProperties": False,
    },
)

_spec(
    id="shell.available", title="List usable shells",
    description="Which shell dialects this host actually has, their versions, and the caveats "
                "each one carries. Call this before choosing a dialect, not after guessing.",
    capability="profile.shells", risk="none", typical_latency_ms=1,
    tags=["shell", "dialect", "capability", "probe", "windows", "bash"],
    input_schema={"type": "object", "properties": {"refresh": {"type": "boolean", "default": False}},
                  "additionalProperties": False},
)

_spec(
    id="shell.jobs", title="List background jobs",
    description="Background jobs started by shell.run(background=true), with pid, running state "
                "and log paths.",
    capability="exec.jobs", risk="none", typical_latency_ms=1, stateful="session",
    tags=["shell", "jobs", "background"],
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)

_spec(
    id="shell.job_wait", title="Wait for a background job",
    description="Block until a background job finishes or the wait times out, then return tails of "
                "its stdout/stderr and its exit code.",
    capability="exec.jobs", risk="none", typical_latency_ms=2000, stateful="session",
    tags=["shell", "jobs", "wait", "background"],
    input_schema={"type": "object",
                  "properties": {"job_id": {"type": "string"},
                                 "timeout_s": {"type": "number", "minimum": 0, "maximum": 1800, "default": 30},
                                 "tail_bytes": {"type": "integer", "minimum": 256, "maximum": 200_000,
                                                "default": 8000}},
                  "required": ["job_id"], "additionalProperties": False},
)

_spec(
    id="shell.job_kill", title="Kill a background job",
    description="Terminate a background job and its whole process tree.",
    capability="exec.jobs", risk="destructive", destructive=True, idempotent=True, typical_latency_ms=50,
    stateful="session", tags=["shell", "jobs", "kill"],
    input_schema={"type": "object", "properties": {"job_id": {"type": "string"},
                                                    "tree": {"type": "boolean", "default": True}},
                  "required": ["job_id"], "additionalProperties": False},
)

_spec(
    id="shell.sessions", title="List shell sessions",
    description="Active persistent shell sessions (cwd and env carried across calls).",
    capability="exec.sessions", risk="none", typical_latency_ms=1, stateful="session",
    tags=["shell", "session", "cwd", "env"],
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)

_spec(
    id="shell.session_reset", title="Reset a shell session",
    description="Drop a session's carried cwd/env (or all sessions). Use when a session drifts.",
    capability="exec.sessions", risk="write", typical_latency_ms=1, stateful="session",
    tags=["shell", "session", "reset"],
    input_schema={"type": "object", "properties": {"session": {"type": "string"},
                                                    "all": {"type": "boolean", "default": False}},
                  "additionalProperties": False},
)

# ================================================================ filesystem
_spec(
    id="fs.read", title="Read a file",
    description="Read a text file with paged access: line offsets, explicit ranges, or byte caps. "
                "Reports encoding/newline so edits can preserve them.",
    capability="fs.read", risk="read", parallel_safe=True, typical_latency_ms=15,
    tags=["file", "read", "view", "open", "lines", "source"],
    see_also=["fs.search", "fs.list", "fs.patch"],
    anti_patterns=["don't read whole huge files - use offset/limit_lines", "don't read binaries for text"],
    input_schema={"type": "object",
                  "properties": {"path": _PATH,
                                 "offset": {"type": "integer", "minimum": 0, "default": 0,
                                            "description": "0-based first line."},
                                 "limit_lines": {"type": "integer", "minimum": 1, "maximum": 20000},
                                 "start_line": {"type": "integer", "minimum": 1},
                                 "end_line": {"type": "integer", "minimum": 1},
                                 "max_bytes": {"type": "integer", "minimum": 128}},
                  "required": ["path"], "additionalProperties": False},
)

_spec(
    id="fs.write", title="Write or create a file",
    description="Atomic create/overwrite of a whole file. Preserves newline style and encoding "
                "unless told otherwise, records an undo token, and can refuse if the file changed "
                "since you read it (expect_sha).",
    capability="fs.write", risk="write", idempotent=True, reversible=True, parallel_safe=False,
    typical_latency_ms=20, tags=["file", "write", "create", "save", "overwrite", "new"],
    see_also=["fs.patch", "fs.read", "fs.undo"],
    anti_patterns=["prefer fs.patch for edits to existing files - it shows a diff and keeps history"],
    input_schema={"type": "object",
                  "properties": {"path": _PATH,
                                 "content": {"type": "string", "description": "Full new file body."},
                                 "overwrite": {"type": "boolean", "default": True},
                                 "create_dirs": {"type": "boolean", "default": True},
                                 "newline": {"type": "string", "enum": ["preserve", "lf", "crlf", "native"],
                                             "default": "preserve"},
                                 "encoding": {"type": "string"},
                                 "expect_sha": {"type": "string",
                                                "description": "Refuse if the file's sha256 differs."},
                                 "dry_run": {"type": "boolean", "default": False}},
                  "required": ["path", "content"], "additionalProperties": False},
)

_spec(
    id="fs.patch", title="Edit a file with targeted replacements",
    description="Apply ordered find/replace edits to a file and return a unified diff. Whitespace "
                "tolerant, refuses ambiguous matches, checks for concurrent modification, and is "
                "undoable. This is the default way to change code.",
    capability="fs.edit", risk="write", idempotent=False, reversible=True, parallel_safe=False,
    typical_latency_ms=25, tags=["file", "edit", "patch", "replace", "modify", "code", "diff"],
    see_also=["fs.read", "fs.write", "fs.undo"],
    anti_patterns=["don't use fs.write to change one line of a big file",
                   "don't paste a snippet you did not read from fs.read"],
    examples=[{"args": {"path": "src/app.py",
                       "edits": [{"old_text": "PORT = 8080", "new_text": "PORT = 9090"}]}}],
    input_schema={"type": "object",
                  "properties": {"path": _PATH,
                                 "edits": {"type": "array", "minItems": 1, "maxItems": 200,
                                           "items": {"type": "object",
                                                     "properties": {
                                                         "old_text": {"type": "string", "minLength": 1},
                                                         "new_text": {"type": "string"},
                                                         "replace_all": {"type": "boolean", "default": False},
                                                         "occurrence": {"type": "integer", "minimum": 1}},
                                                     "required": ["old_text", "new_text"],
                                                     "additionalProperties": False}},
                                 "expect_sha": {"type": "string"},
                                 "dry_run": {"type": "boolean", "default": False}},
                  "required": ["path", "edits"], "additionalProperties": False},
)

_spec(
    id="fs.search", title="Search file contents",
    description="Grep across the workspace. Uses ripgrep when installed (honouring its rules) and a "
                "pure-python walker otherwise, with the same result shape. Report which provider "
                "answered in `data.provider`.",
    capability="search.text", risk="read", parallel_safe=True, typical_latency_ms=180,
    tags=["search", "grep", "rg", "find", "text", "code", "pattern", "regex"],
    anti_patterns=["don't grep for filenames - use fs.glob", "don't search all of / - scope with path/glob"],
    input_schema={"type": "object",
                  "properties": {"pattern": {"type": "string", "minLength": 1},
                                 "path": {"type": "string", "default": "."},
                                 "regex": {"type": "boolean", "default": False},
                                 "ignore_case": {"type": "boolean", "default": False},
                                 "word": {"type": "boolean", "default": False},
                                 "fixed": {"type": "boolean", "default": False},
                                 "context": {"type": "integer", "minimum": 0, "maximum": 10, "default": 0},
                                 "glob": {"type": "string"},
                                 "type_": {"type": "string", "description": "rg --type (py, ts, rust...)"},
                                 "files_with_matches": {"type": "boolean", "default": False},
                                 "multiline": {"type": "boolean", "default": False},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                                 "prefer": {"type": "string", "enum": ["auto", "ripgrep", "python"]}},
                  "required": ["pattern"], "additionalProperties": False},
)

_spec(
    id="fs.list", title="List a directory",
    description="Bounded directory listing with sizes and mtimes; ignore rules applied.",
    capability="fs.list", risk="read", parallel_safe=True, typical_latency_ms=20,
    tags=["ls", "dir", "list", "tree", "browse", "folder"],
    input_schema={"type": "object",
                  "properties": {"path": {"type": "string", "default": "."},
                                 "depth": {"type": "integer", "minimum": 1, "maximum": 6, "default": 1},
                                 "sort": {"type": "string", "enum": ["name", "size", "mtime"], "default": "name"},
                                 "include_hidden": {"type": "boolean"},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 400}},
                  "additionalProperties": False},
)

_spec(
    id="fs.glob", title="Match filenames",
    description="Glob filenames under a root (** spans directories). Returns paths, sizes, mtimes.",
    capability="fs.glob", risk="read", parallel_safe=True, typical_latency_ms=60,
    tags=["glob", "find", "files", "match", "name"],
    input_schema={"type": "object",
                  "properties": {"pattern": {"type": "string", "minLength": 1,
                                             "examples": ["**/*.py", "src/**/*test*.ts"]},
                                 "root": {"type": "string", "default": "."},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 500},
                                 "sort": {"type": "string", "enum": ["mtime", "name"], "default": "mtime"}},
                  "required": ["pattern"], "additionalProperties": False},
)

_spec(
    id="fs.sniff", title="Identify a file's format",
    description=("Sample a file and report encoding, line endings, whether it is binary, and size - "
                 "cheap to run, and the thing to check before reading anything unusual into context."),
    capability="fs.read", risk="read", parallel_safe=True, typical_latency_ms=3,
    tags=["encoding", "binary", "detect", "crlf", "utf16", "sniff"],
    input_schema={"type": "object", "properties": {"path": _PATH,
                                                    "sample_bytes": {"type": "integer", "minimum": 256,
                                                                      "maximum": 262144, "default": 8192}},
                  "required": ["path"], "additionalProperties": False},
)

_spec(
    id="fs.stat", title="Inspect a path",
    description="Existence, type, size, mtime, permissions, writability, and whether a symlink was followed.",
    capability="fs.stat", risk="read", parallel_safe=True, typical_latency_ms=2,
    tags=["stat", "info", "exists", "size", "metadata"],
    input_schema={"type": "object", "properties": {"path": _PATH}, "required": ["path"],
                  "additionalProperties": False},
)

_spec(
    id="fs.delete", title="Delete a path",
    description="Delete a file or directory. Journaled first, so the result carries an undo token.",
    capability="fs.delete", risk="destructive", destructive=True, reversible=True, idempotent=False,
    parallel_safe=False, typical_latency_ms=15, approval="policy",
    tags=["delete", "remove", "rm", "unlink", "clean"],
    anti_patterns=["never delete a directory you have not listed first"],
    input_schema={"type": "object", "properties": {"path": _PATH,
                                                    "recursive": {"type": "boolean", "default": False},
                                                    "dry_run": {"type": "boolean", "default": False}},
                  "required": ["path"], "additionalProperties": False},
)

_spec(
    id="fs.move", title="Move or rename a path",
    description="Rename/move within the sandbox, journaled so it can be undone.",
    capability="fs.move", risk="write", destructive=True, reversible=True, idempotent=False,
    typical_latency_ms=15, tags=["mv", "rename", "move", "relocate"],
    input_schema={"type": "object", "properties": {"src": _PATH, "dst": _PATH,
                                                    "overwrite": {"type": "boolean", "default": False},
                                                    "dry_run": {"type": "boolean", "default": False}},
                  "required": ["src", "dst"], "additionalProperties": False},
)

_spec(
    id="fs.mkdir", title="Create a directory",
    description="mkdir -p inside the sandbox.",
    capability="fs.mkdir", risk="write", idempotent=True, reversible=True, typical_latency_ms=5,
    tags=["mkdir", "directory", "create", "folder"],
    input_schema={"type": "object", "properties": {"path": _PATH,
                                                    "parents": {"type": "boolean", "default": True},
                                                    "dry_run": {"type": "boolean", "default": False}},
                  "required": ["path"], "additionalProperties": False},
)

_spec(
    id="fs.undo", title="Undo one journaled change",
    description="Restore the before-image of one fs.write/fs.patch/fs.delete/fs.move using its undo token. "
                "Pass the `undo_token` value the mutating call returned (either argument name works).",
    capability="fs.undo", risk="write", idempotent=True, reversible=False, typical_latency_ms=25,
    tags=["undo", "revert", "rollback", "restore"],
    input_schema={"type": "object", "properties": {
        "token": {"type": "string", "description": "Undo token, e.g. 'und_1a2b3c4d5e6f'."},
        "undo_token": {"type": "string", "description": "Alias for `token`."},
        "dry_run": {"type": "boolean", "default": False}},
        "anyOf": [{"required": ["token"]}, {"required": ["undo_token"]}],
        "additionalProperties": False},
)

_spec(
    id="fs.undo_task", title="Undo every change in a task",
    description="Reverse all journaled mutations for one task_id, newest first. This is 'revert the turn'.",
    capability="fs.undo", risk="write", idempotent=False, reversible=False, typical_latency_ms=80,
    tags=["undo", "revert", "rollback", "restore", "task"],
    input_schema={"type": "object", "properties": {"task_id": {"type": "string"},
                                                    "dry_run": {"type": "boolean", "default": False}},
                  "required": ["task_id"], "additionalProperties": False},
)

_spec(
    id="fs.journal_list", title="List journaled changes",
    description="Recent journal entries (what changed, when, and the undo token) - the audit trail.",
    capability="fs.journal", risk="read", typical_latency_ms=5, stateful="session",
    tags=["journal", "history", "undo", "audit"],
    input_schema={"type": "object", "properties": {"task_id": {"type": "string"},
                                                    "path": {"type": "string"},
                                                    "limit": {"type": "integer", "minimum": 1,
                                                              "maximum": 500, "default": 50}},
                  "additionalProperties": False},
)

# ============================================================ profile/registry
_spec(
    id="profile.probe", title="Probe host capabilities",
    description="Re-probe and return the CapabilityProfile: OS/arch, usable shells with versions and "
                "caveats, available binaries, filesystem traits (case sensitivity, symlinks), console "
                "encoding, and the warnings that explain degraded behaviour.",
    capability="profile.capabilities", risk="none", typical_latency_ms=250, idempotent=True,
    tags=["profile", "capability", "host", "detect", "probe", "environment"],
    anti_patterns=["don't call this on every turn - the profile is cached and fingerprinted"],
    input_schema={"type": "object", "properties": {"force": {"type": "boolean", "default": False},
                                                    "include_receipts": {"type": "boolean", "default": False}},
                  "additionalProperties": False},
)

_spec(
    id="registry.list", title="List available tools",
    description="The currently advertised tool set after capability gating and provider de-duplication, "
                "with risk class and token cost. Use this to see what you may call right now.",
    capability="registry.tools", risk="none", typical_latency_ms=5,
    tags=["tools", "registry", "list", "discover", "capability"],
    input_schema={"type": "object",
                  "properties": {"group": {"type": "string"},
                                 "include_gated": {"type": "boolean", "default": False},
                                 "include_schema": {"type": "boolean", "default": False},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 400, "default": 100}},
                  "additionalProperties": False},
)

_spec(
    id="registry.search", title="Find a tool for a need",
    description="Search tools by natural-language capability ('rename a bunch of files', 'run a script "
                "in powershell'). Deterministic lexical ranking over id/tags/description/capability.",
    capability="registry.search", risk="none", typical_latency_ms=8,
    tags=["search", "tools", "discover", "find", "capability", "need"],
    input_schema={"type": "object",
                  "properties": {"query": {"type": "string", "minLength": 1},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
                                 "include_gated": {"type": "boolean", "default": False},
                                 "group": {"type": "string"},
                                 "max_risk": {"type": "string", "enum": ["none", "read", "write",
                                                                         "destructive", "network",
                                                                         "privileged"]}},
                  "required": ["query"], "additionalProperties": False},
)

_spec(
    id="registry.describe", title="Inspect one tool's contract",
    description="Full manifest for a tool: schema, requirements, availability gates with reasons, "
                "risk, reversibility, examples, anti-patterns, and live stats.",
    capability="registry.describe", risk="none", typical_latency_ms=4,
    tags=["tool", "schema", "describe", "inspect", "manifest"],
    input_schema={"type": "object", "properties": {"tool": {"type": "string"}},
                  "required": ["tool"], "additionalProperties": False},
)

_spec(
    id="registry.stats", title="Tool usage and reliability",
    description="Per-tool call counts, failure counts, and mean latency. Providers are ranked partly by "
                "this, so check it when something seems to be failing a lot.",
    capability="registry.stats", risk="none", typical_latency_ms=3, stateful="session",
    tags=["stats", "metrics", "reliability", "failures"],
    input_schema={"type": "object", "properties": {"tool": {"type": "string"}}, "additionalProperties": False},
)


# ============================================================== handler wiring
def register(reg: Any, *, engine: Any, shells: Any, fs: Any, journal: Any, skills: Any = None,
             load_skills: bool = True) -> dict[str, Any]:
    """Bind handlers to manifests on a registry. Returns a load report."""
    report = {"registered": 0, "skipped": []}

    def add(tool_id: str, handler: Any) -> None:
        try:
            man = next(m for m in TOOL_SPECS if m.id == tool_id)
        except StopIteration:
            report["skipped"].append(f"{tool_id}: no manifest")
            return
        handler.__name__ = tool_id.replace(".", "_")
        reg.register(man, handler, replace=True)
        report["registered"] += 1

    # ---- shell
    def shell_run(script: str, dialect: str | None = None, cwd: str | None = None, timeout_s: float = 120.0,
                  env: dict[str, str] | None = None, env_mode: str = "inherit", strict: bool = True,
                  expects: str | None = None, session: str | None = None, background: bool = False,
                  stdin_text: str | None = None, capture_env: bool = False,
                  keep_script: bool = False, max_output_bytes: int | None = None,
                  ctx: Any = None) -> Any:
        from ..shells.base import ShellRequest

        resolved_cwd = cwd or (ctx.cwd if ctx else None) or engine.config.workspace
        # cwd is sandbox-checked too: an agent must not `cd /` and keep going
        if resolved_cwd:
            try:
                resolved_cwd = engine.fs.sb.resolve(resolved_cwd, intent="list").real
            except SkeletonKeyError as exc:
                if exc.code == "SANDBOX_VIOLATION":
                    exc.details["note"] = "shell cwd is sandboxed like every other path"
                raise
        req = ShellRequest(script=script, dialect=dialect, cwd=resolved_cwd, env=env, env_mode=env_mode,
                           timeout_s=float(timeout_s), strict=strict, expects=expects, session=session,
                           background=background, stdin_text=stdin_text, capture_env=capture_env,
                           cleanup_script=not keep_script,
                           max_output_bytes=int(max_output_bytes or engine.config.shell.max_output_bytes))
        out = shells.run(req)
        data = _shell_payload(out, engine.config.shell.max_output_bytes)
        if background:
            jid = out.session_state.get("job_id")
            return ToolResult.success(data=data, next_actions=[{"tool": "shell.job_wait",
                                                                "args": {"job_id": jid, "timeout_s": 30}}],
                                      hints=["job is running detached; poll with shell.job_wait"],
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
            result.error = _shell_error(out, timeout_s)
            result.data = data  # keep the evidence attached to the failure
        return result

    def _shell_payload(out: Any, cap: int) -> dict[str, Any]:
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

    def _shell_error(out: Any, timeout_s: float) -> Any:
        from ..core.errors import ToolError

        if out.timed_out:
            return ToolError.from_code(E.TIMEOUT, f"timed out after {timeout_s}s",
                                       details={"exit_code": out.exit_code, "killed": out.killed,
                                                "stderr_tail": clip(out.stderr, 1200)})
        return ToolError.from_code(
            E.NONZERO_EXIT, f"{'script aborted early' if not out.completed else 'command failed'} "
                            f"(exit {out.exit_code})",
            details={"exit_code": out.exit_code, "completed": out.completed, "dialect": out.dialect,
                     "stderr_tail": clip(out.stderr, 1500), "stdout_tail": clip(out.stdout, 1500)})

    def shell_available(refresh: bool = False) -> dict[str, Any]:
        prof = engine.profile
        if refresh:
            prof = engine.refresh_profile()
        pol = engine.config.shell
        denied = set(pol.deny_dialects or ())
        allowed = [d for d in (pol.allow_dialects or []) if d not in denied]
        present = prof.available_dialects() if prof else []
        # "available" is the set the agent may actually use: a dialect that policy
        # denies is not available, and telling it otherwise invites a failed call.
        usable = [d for d in present if not allowed or d in allowed]
        blocked = [d for d in present if d not in usable]
        preferred = prof.preferred_dialect() if prof else None
        if preferred not in usable:
            preferred = usable[0] if usable else None
        return {
            "preferred": preferred,
            "available": usable,
            "shells": {k: v.to_dict() for k, v in sorted((prof.shells if prof else {}).items())
                       if not allowed or k in allowed},
            "allowed_by_policy": allowed,
            **({"blocked_by_policy": blocked} if blocked else {}),
            "warnings": prof.warnings if prof else [],
        }

    def shell_jobs() -> dict[str, Any]:
        return {"jobs": shells.jobs()}

    def shell_job_wait(job_id: str, timeout_s: float = 30.0, tail_bytes: int = 8000) -> dict[str, Any]:
        return shells.job_wait(job_id, timeout=float(timeout_s), tail_bytes=int(tail_bytes))

    def shell_job_kill(job_id: str, tree: bool = True) -> dict[str, Any]:
        return shells.job_kill(job_id, tree=tree)

    def shell_sessions() -> dict[str, Any]:
        return {"sessions": shells.sessions()}

    def shell_session_reset(session: str | None = None, all: bool = False) -> dict[str, Any]:
        return shells.session_reset(None if all else session)

    # ---- fs
    def fs_sniff(path: str, sample_bytes: int = 8192) -> dict[str, Any]:
        return fs.sniff(path, sample_bytes=int(sample_bytes))

    def fs_read(path: str, offset: int = 0, limit_lines: int | None = None, start_line: int | None = None,
                end_line: int | None = None, max_bytes: int | None = None) -> dict[str, Any]:
        r = fs.read(path, offset=int(offset), limit_lines=limit_lines, start_line=start_line,
                    end_line=end_line)
        d = r.to_dict()
        if r.truncated and r.next_offset is not None:
            d["next_call"] = {"tool": "fs.read", "args": {"path": path, "offset": r.next_offset,
                                                           "limit_lines": limit_lines or 400}}
        return d

    def fs_write(path: str, content: str, overwrite: bool = True, create_dirs: bool = True,
                 newline: str = "preserve", encoding: str | None = None, expect_sha: str | None = None,
                 dry_run: bool = False, ctx: Any = None) -> dict[str, Any]:
        w = fs.write(path, content, overwrite=overwrite, create_dirs=create_dirs, newline=newline,
                     encoding=encoding, expect_sha=expect_sha, dry_run=dry_run,
                     task_id=ctx.task_id if ctx else "")
        out = w.to_dict()
        if dry_run:
            # say so in the payload, not only in a hint: a host that reads `created: true`
            # must be able to tell that nothing was actually created.
            out["dry_run"] = True
            out["hint"] = "no bytes written; rerun without dry_run to apply"
        return out

    def fs_patch(path: str, edits: list[dict[str, Any]], expect_sha: str | None = None,
                 dry_run: bool = False, ctx: Any = None) -> dict[str, Any]:
        d = fs.patch(path, edits, dry_run=dry_run, expect_sha=expect_sha,
                     task_id=ctx.task_id if ctx else "")
        # The token lives in the nested write result, but hosts copy `data.undo_token`
        # straight into fs.undo - keep it at the top level like every other mutation.
        token = (d.get("write") or {}).get("undo_token")
        if token:
            d["undo_token"] = token
            d["undo"] = {"tool": "fs.undo", "args": {"token": token}}
        return d

    def fs_search(pattern: str, path: str = ".", regex: bool = False, ignore_case: bool = False,
                  word: bool = False, fixed: bool = False, context: int = 0, glob: str | None = None,
                  type_: str | None = None, files_with_matches: bool = False, multiline: bool = False,
                  limit: int = 200, prefer: str = "auto") -> dict[str, Any]:
        backend = engine.search_prefer(None if prefer in (None, "auto") else prefer)
        out = backend.search(pattern, path=path, regex=regex, ignore_case=ignore_case, fixed=fixed,
                            word=word, context=int(context), glob=glob, type_=type_, limit=int(limit),
                            files_with_matches=files_with_matches, multiline=multiline)
        d = out.to_dict(max_hits=int(limit))
        if out.truncated:
            d["hint"] = "results truncated: narrow `path`/`glob`, or raise limit"
        if out.files_matched == 0:
            d["zero_match_advice"] = ("pattern is literal by default - set regex=true for regex syntax; "
                                      "also try ignore_case=true and a wider path")
        return d

    def fs_list(path: str = ".", depth: int = 1, sort: str = "name", include_hidden: bool | None = None,
                limit: int = 400) -> dict[str, Any]:
        return fs.list(path, depth=int(depth), sort=sort, include_hidden=include_hidden, limit=int(limit))

    def fs_glob(pattern: str, root: str = ".", limit: int = 500, sort: str = "mtime") -> dict[str, Any]:
        return fs.glob(pattern, root=root, limit=int(limit), sort=sort)

    def fs_stat(path: str) -> dict[str, Any]:
        return fs.stat(path)

    def fs_delete(path: str, recursive: bool = False, dry_run: bool = False, ctx: Any = None) -> dict[str, Any]:
        d = fs.delete(path, recursive=recursive, dry_run=dry_run, task_id=ctx.task_id if ctx else "")
        if d.get("undo_token"):
            d["undo"] = {"tool": "fs.undo", "args": {"token": d["undo_token"]}}
        return d

    def fs_move(src: str, dst: str, overwrite: bool = False, dry_run: bool = False,
                ctx: Any = None) -> dict[str, Any]:
        return fs.move(src, dst, overwrite=overwrite, dry_run=dry_run, task_id=ctx.task_id if ctx else "")

    def fs_mkdir(path: str, parents: bool = True, dry_run: bool = False,
                 ctx: Any = None) -> dict[str, Any]:
        return fs.mkdir(path, parents=parents, dry_run=dry_run,
                        task_id=ctx.task_id if ctx else "")

    def fs_undo(token: str | None = None, undo_token: str | None = None,
                 dry_run: bool = False) -> dict[str, Any]:
        picked = token or undo_token
        if not picked:
            raise SkeletonKeyError(
                E.BAD_ARGS, "fs.undo needs the token a mutating call returned",
                details={"hint": "fs.write/fs.patch/fs.delete/fs.move/fs.mkdir return undo_token",
                         "recent": [e.get("token") for e in journal.list(limit=5)]},
            )
        return journal.undo(picked, dry_run=dry_run)

    def fs_undo_task(task_id: str, dry_run: bool = False) -> dict[str, Any]:
        return journal.undo_task(task_id, dry_run=dry_run)

    def fs_journal_list(task_id: str | None = None, path: str | None = None, limit: int = 50) -> dict[str, Any]:
        return {"entries": journal.list(task_id=task_id, limit=int(limit), paths=path),
                "summary": journal.summary()}

    # ---- profile / registry
    def profile_probe(force: bool = False, include_receipts: bool = False) -> dict[str, Any]:
        prof = engine.refresh_profile() if force else engine.profile
        d = prof.to_dict(include_receipts=include_receipts)
        d["tool_availability"] = {
            "advertised": len(engine.registry.advertise().tools), "total": len(engine.registry.all())}
        if prof.warnings:
            d["warnings"] = prof.warnings
        return d

    def registry_list(group: str | None = None, include_gated: bool = False, include_schema: bool = False,
                      limit: int = 100) -> dict[str, Any]:
        snap = engine.advertise(dedupe_capability=not include_gated,
                               token_budget=engine.config.mcp.advertise_max_tools * 200)
        items = []
        for man in snap.tools:
            if group and man.group != group:
                continue
            d = man.to_dict(include_schema=include_schema)
            items.append(d)
            if len(items) >= int(limit):
                break
        if include_gated:
            for tid, gate in snap.gates.items():
                if not gate.available and (not group or tid.split(".")[0] == group):
                    items.append({"id": tid, "available": False, "gate": gate.to_dict()})
        return {"tools": items, "count": len(items), "digest": snap.digest,
                "selected_providers": snap.selected,
                "estimated_tokens": snap.tokens,
                "budget": engine.config.mcp.advertise_max_tools}

    def registry_search(query: str, limit: int = 8, include_gated: bool = False, group: str | None = None,
                        max_risk: str | None = None) -> dict[str, Any]:
        hits = engine.registry.search(query, limit=int(limit), include_gated=include_gated, group=group,
                                     max_risk=max_risk)
        return {"query": query, "results": hits, "count": len(hits),
                **({"advice": "no tool matched; try broader words or include_gated=true to see gated ones"}
                   if not hits else {})}

    def registry_describe(tool: str) -> dict[str, Any]:
        return engine.registry.describe(tool)

    def registry_stats(tool: str | None = None) -> dict[str, Any]:
        return {"stats": engine.registry.stats(tool), "overview": engine.registry.overview()}

    add("shell.run", shell_run)
    add("shell.available", shell_available)
    add("shell.jobs", shell_jobs)
    add("shell.job_wait", shell_job_wait)
    add("shell.job_kill", shell_job_kill)
    add("shell.sessions", shell_sessions)
    add("shell.session_reset", shell_session_reset)
    for name, fn in [("fs.read", fs_read), ("fs.write", fs_write), ("fs.patch", fs_patch),
                     ("fs.search", fs_search), ("fs.list", fs_list), ("fs.glob", fs_glob),
                     ("fs.stat", fs_stat), ("fs.sniff", fs_sniff),
                     ("fs.delete", fs_delete), ("fs.move", fs_move),
                     ("fs.mkdir", fs_mkdir), ("fs.undo", fs_undo), ("fs.undo_task", fs_undo_task),
                     ("fs.journal_list", fs_journal_list), ("profile.probe", profile_probe),
                     ("registry.list", registry_list), ("registry.search", registry_search),
                     ("registry.describe", registry_describe), ("registry.stats", registry_stats)]:
        add(name, fn)
    return report
