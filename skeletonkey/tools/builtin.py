"""Built-in Phase-1 tools: filesystem, multi-shell, profile, registry, journal.

Every handler is `(args...) -> dict | ToolResult`. Raise `SkeletonKeyError` for
anything the agent can act on; the engine turns it into a coded envelope. The
manifest metadata below is the contract the autopilot plans against, so risk /
reversibility / requirements are filled in for real, not decorative.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.engine import ApprovalRequired
from ..core.envelope import ToolResult
from ..core.errors import E, SkeletonKeyError
from ..core.manifest import Requirement, ToolManifest
from ..shells.execute import run_script

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
                   "don't chain unrelated commands in one call; a failure mid-script loses the rest",
                   "don't interpolate user/model text into `script` - pass it in `argv` and read $1 / $args / sys.argv"],
    see_also=["fs.patch", "shell.job_wait", "fs.search", "shell.quote"],
    examples=[{"args": {"script": "git status --short", "dialect": "bash"}}],
    input_schema={
        "type": "object",
        "properties": {
            "script": {"type": "string", "minLength": 1, "description": "Script body, verbatim."},
            "argv": {"type": "array", "items": {"type": "string"}, "maxItems": 128,
                     "description": "Arguments for the script ($1..$n, $args, or sys.argv[1:]). Passed "
                                     "straight to the process, so they never meet a shell parser: prefer "
                                     "this over interpolating values into `script`. Numbers are accepted "
                                     "and stringified; pass structured data as one json.dumps() string."},
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
    id="shell.job_watch", title="Watch a background job for a line",
    description="Poll a background job's stdout until a line matches `until` (regular "
                "expression) or the timeout cap is reached. On a match returns "
                "`matched_line`; on the cap returns `timed_out: true` and leaves the job "
                "running - watching never kills.",
    capability="exec.jobs", risk="none", typical_latency_ms=2000, stateful="session",
    tags=["shell", "jobs", "watch", "background"],
    input_schema={"type": "object",
                  "properties": {"job_id": {"type": "string"},
                                 "until": {"type": "string"},
                                 "timeout_s": {"type": "number", "minimum": 0, "maximum": 1800,
                                               "default": 30},
                                 "poll_s": {"type": "number", "minimum": 0.05, "maximum": 30,
                                            "default": 0.5},
                                 "tail_bytes": {"type": "integer", "minimum": 256, "maximum": 200_000,
                                                "default": 8000}},
                  "required": ["job_id", "until"], "additionalProperties": False},
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
    description="Delete a file or directory. Default (fs.trash = \"journal\"): journaled first, so the "
                "result carries an undo token. fs.trash = \"os-trash\" moves the path to the OS recycle bin "
                "(the journal keeps a second copy; a host without a trash API refuses with UNSUPPORTED_PLATFORM "
                "and deletes nothing). \"delete\" is a hard, unjournaled delete.",
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
    id="fs.chmod", title="Set the mode bits on a path",
    description="Set permissions inside the sandbox from an octal mode (`644`, `0o755`) or "
        "symbolic clauses (`u+x`, `go-w`, `a=r`, `a=rwx,o+t`). Symbolic modes are applied to the "
        "current bits, so `u+x` cannot wipe what you did not name. Journaled: `fs.undo` puts the "
        "previous mode back. A mode that already matches changes and records nothing. On Windows "
        "the mode bits collapse to the read-only attribute - the result says so when the bits you "
        "asked for did not stick, because '0600' there does not mean 'nobody else can read this'.",
    capability="fs.chmod", risk="write", idempotent=True, reversible=True,
    parallel_safe=False, typical_latency_ms=8,
    tags=["permissions", "chmod", "mode", "executable", "read-only", "icacls", "windows", "unix"],
    anti_patterns=["not a way to change ownership, and not an ACL editor on Windows",
                   "don't chmod +x and expect a runnable file if the shebang or the interpreter "
                   "is missing - check with fs.sniff/fs.stat first"],
    see_also=["fs.stat", "fs.undo", "shell.run"],
    examples=[{"args": {"path": "scripts/deploy.sh", "mode": "u+x"}},
              {"args": {"path": "tools", "mode": "755", "recursive": True}}],
    input_schema={"type": "object", "properties": {
        "path": _PATH,
        "mode": {"anyOf": [{"type": "string", "minLength": 1},
                           {"type": "integer", "minimum": 0, "maximum": 4095}],
                 "description": "Octal (`644`, `0o755`, or the integer 0o644) or symbolic "
                                "(`u+x`, `go-w`, `a=r`, `u=rw,go=r`). An unparseable mode is an "
                                "error, never a fallback to 0o644."},
        "recursive": {"type": "boolean", "default": False,
                      "description": "Walk a directory. Symlinks are not followed and every path "
                                     "is re-checked against roots and deny rules before anything "
                                     "is written, so one denied entry refuses the whole call."},
        "dry_run": {"type": "boolean", "default": False}},
        "required": ["path", "mode"], "additionalProperties": False},
)

_spec(
    id="fs.undo", title="Undo one journaled change",
    description="Restore the before-image of one fs.write/fs.patch/fs.delete/fs.move using its undo token. "
                "Pass the `undo_token` value the mutating call returned (either argument name works). "
                "Set `expect_sha` (the sha256 from the last fs.read, full or 16-char prefix) to refuse with "
                "CONFLICT if the file no longer holds that content - the guard against rolling over work that "
                "happened after the change.",
    capability="fs.undo", risk="write", idempotent=True, reversible=False, typical_latency_ms=25,
    tags=["undo", "revert", "rollback", "restore"],
    input_schema={"type": "object", "properties": {
        "token": {"type": "string", "description": "Undo token, e.g. 'und_1a2b3c4d5e6f'."},
        "undo_token": {"type": "string", "description": "Alias for `token`."},
        "dry_run": {"type": "boolean", "default": False},
        "expect_sha": {"type": "string", "description": "If set, refuse with CONFLICT unless the file still "
                                                       "holds this sha256 (or its 16-char prefix)."}},
        "anyOf": [{"required": ["token"]}, {"required": ["undo_token"]}],
        "additionalProperties": False},
)

_spec(
    id="fs.redo", title="Re-apply the most recent undone change",
    description="The mirror of fs.undo: re-apply the most recently *undone* journaled change, optionally "
                "limited to one path. The redo itself is journaled - the result carries a fresh `undo_token` "
                "so it can be undone again. Anything that no longer holds (the file changed after the undo, "
                "the after-image was pruned, the path was re-created) is CONFLICT, never a silent overwrite.",
    capability="fs.redo", risk="write", idempotent=False, reversible=True, typical_latency_ms=25,
    tags=["redo", "reapply", "undo", "rollback"],
    see_also=["fs.undo", "fs.journal_list"],
    input_schema={"type": "object", "properties": {
        "path": {"type": "string", "description": "Limit to the most recent undone change on this path."},
        "dry_run": {"type": "boolean", "default": False}},
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
    id="policy.grant", title="Record an approval grant",
    description="Grant approval for a tool for this task or the whole session, with a "
                "receipt in the result and a row in the ledger, so an audit shows who "
                "approved what. A grant for a tool that itself requires approval is "
                "itself approval-gated: the approver is shown the target in the prompt "
                "(args_preview carries tool + scope), because an unattended self-grant "
                "for a destructive tool would be the hole this whole layer closes. A "
                "grant for a tool the caller could already run is pure record-keeping. "
                "Grants live in the calling task's context: a task grant does not outlive "
                "the task.",
    capability="policy.grant", risk="write", approval="policy", idempotent=True,
    typical_latency_ms=1, stateful="session",
    tags=["approval", "grant", "policy", "approve", "permission", "receipt", "audit"],
    anti_patterns=["not a way to grant yourself a destructive tool unattended - "
                   "the grant is approval-gated whenever the target is"],
    see_also=["registry.describe"],
    examples=[{"args": {"tool": "fs.delete", "scope": "task"}}],
    input_schema={
        "type": "object",
        "properties": {
            "tool": {"type": "string", "minLength": 1, "description": "Tool id the grant covers."},
            "scope": {"type": "string", "enum": ["once", "task", "session"], "default": "task",
                      "description": "once = no standing grant (the approval token of a "
                                     "single call is the grant); task = until this CallContext "
                                     "ends; session = for the lifetime of the shared context."},
        },
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


_spec(
    id="shell.quote", title="Quote values for a dialect",
    description="""Render values as literal tokens for one shell dialect, for embedding in a
`shell.run {script}` body. If the value is an *argument* rather than part of the program text,
do not quote it - pass `shell.run {argv: [...]}` and it never reaches a shell parser.
Posix uses shlex single-quoting, PowerShell doubles embedded single quotes (its literal
form: no $ or backtick expansion), python returns a source literal.""",
    capability="shell.quote", risk="none", typical_latency_ms=1, typical_output_bytes=300,
    idempotent=True, parallel_safe=True, stateful="none",
    tags=["shell", "quote", "escaping", "argv", "injection", "bash", "pwsh"],
    anti_patterns=["not a general escaping: do not embed the result inside a double-quoted "
                   "PowerShell string or a heredoc body",
                   "don't quote your way out of interpolation when argv would avoid the problem"],
    see_also=["shell.run", "shell.available"],
    examples=[{"args": {"args": ["a file.txt", "it's here", "$HOME"], "dialect": "bash"}}],
    input_schema={
        "type": "object",
        "properties": {
            "args": {"type": "array", "items": {"type": ["string", "integer", "number", "boolean"]},
                     "minItems": 1, "maxItems": 256,
                     "description": "Values to render as literal tokens."},
            "dialect": {"type": "string", "enum": ["bash", "pwsh", "powershell", "python", "sh", "zsh", "fish"],
                        "description": "Omit to use the host's preferred shell."},
            "shape": {"type": "string", "enum": ["tokens", "command", "both"], "default": "both",
                      "description": "tokens = the list only; command = one joined line; both = the two. "
                                     "(`as` would be the nicer name but it is a Python keyword, and handler "
                                     "parameters are injected by name.)"},
        },
        "required": ["args"],
        "additionalProperties": False,
    },
)


# ================================================================= publishing
_PUB_ID = {"type": "string",
           "pattern": "^[a-z0-9][a-z0-9._-]{0,63}$",
           "description": "Lowercase id, e.g. pypi.token, google_play.token"}
_PUB_KINDS = ["token", "api_key", "client_id", "client_secret", "oauth_token",
              "password", "email", "phone", "two_factor", "social_account",
              "signing_key", "certificate", "webhook", "other"]

_spec(
    id="pub.store_put", title="Store a credential in the publish store",
    description="Write a credential (token, key, password, account id, ...) into the write-only "
                "publish store. The value is persisted to a user-level file OUTSIDE the workspace "
                "and is NEVER returned by any tool - the only way out is pub.inject into files. "
                "Use it for everything a publish needs: platform tokens, API keys, OAuth tokens, "
                "2FA codes, social accounts, e-mail, phone, signing keys.",
    capability="publish.store.write", group="publishing",
    risk="write", idempotent=False, parallel_safe=False, stateful="host",
    typical_latency_ms=15, secret_args=["value"],
    tags=["publish", "store", "credential", "secret", "token", "key", "oauth", "2fa"],
    anti_patterns=["don't paste credentials into workspace files instead - the store is the wall",
                   "don't store the same secret in two ids; rotate (put again) and delete the old id"],
    see_also=["pub.store_list", "pub.inject"],
    input_schema={"type": "object",
                  "properties": {
                      "id": _PUB_ID,
                      "kind": {"type": "string", "enum": _PUB_KINDS},
                      "value": {"type": "string", "minLength": 1,
                                "description": "The secret itself. Never echoed back."},
                      "note": {"type": "string", "description": "Human note: what it is, where it came from, expiry."}},
                  "required": ["id", "kind", "value"],
                  "additionalProperties": False},
)

_spec(
    id="pub.store_list", title="List publish store entries (metadata only)",
    description="List what is in the publish store: id, kind, note, timestamps and a short value "
                "mask. Raw values are not listed and can never be read back through a tool.",
    capability="publish.store.list", group="publishing",
    risk="read", stateful="host", typical_latency_ms=10,
    tags=["publish", "store", "list", "credentials"],
    see_also=["pub.store_put", "pub.store_delete"],
    input_schema={"type": "object",
                  "properties": {"kind": {"type": "string", "enum": _PUB_KINDS,
                                          "description": "Filter by kind."}},
                  "additionalProperties": False},
)

_spec(
    id="pub.store_delete", title="Delete a publish store entry",
    description="Remove one entry from the publish store. Destructive and IRREVERSIBLE - the store "
                "lives outside the workspace journal, so there is no undo. Rotate instead of delete "
                "when the old credential may still be in use somewhere.",
    capability="publish.store.delete", group="publishing",
    risk="write", idempotent=False, destructive=True, reversible=False, stateful="host",
    typical_latency_ms=15, approval="policy",
    tags=["publish", "store", "delete", "credential"],
    see_also=["pub.store_put", "pub.store_list"],
    input_schema={"type": "object", "properties": {"id": _PUB_ID},
                  "required": ["id"], "additionalProperties": False},
)

_spec(
    id="pub.placeholders", title="Find {{PUB.<id>}} placeholders with exact locations",
    description="Scan a file or directory for {{PUB.<id>}} markers and report each occurrence with "
                "the exact file, line and column, the store id it binds, and whether that id is "
                "bound (present in the store) or missing. This is the pre-publish check: publish "
                "only when every marker is bound.",
    capability="publish.scan", group="publishing",
    risk="read", stateful="host", typical_latency_ms=30,
    tags=["publish", "placeholders", "scan", "markers", "template"],
    anti_patterns=["don't hand-edit values into the markers - let pub.inject do it so it is journaled"],
    see_also=["pub.inject", "pub.store_list"],
    input_schema={"type": "object",
                  "properties": {"path": {"type": "string", "default": ".",
                                          "description": "A file or directory (workspace-relative). Default: the whole workspace."}},
                  "additionalProperties": False},
)

_spec(
    id="pub.inject", title="Inject stored values into {{PUB.<id>}} placeholders",
    description="Replace {{PUB.<id>}} markers in files with values from the publish store, writing "
                "through the journaled fs layer - every changed file is undoable. Refuses to touch "
                "ANY file if a marker's store id is missing (no partial publishes). dry_run=true "
                "reports the plan without writing. Values never appear in this tool's result.",
    capability="publish.inject", group="publishing",
    risk="write", idempotent=False, parallel_safe=False, reversible=True, stateful="host",
    typical_latency_ms=80, approval="policy",
    tags=["publish", "inject", "placeholders", "secrets", "template"],
    anti_patterns=["never re-type a secret to 'fix' an unbound marker - store_put it, then re-inject"],
    see_also=["pub.placeholders", "pub.store_put", "fs.undo"],
    examples=[{"args": {"path": "deploy", "dry_run": True}},
              {"args": {"path": "deploy", "bindings": {"pypi.token": "pypi.token.old"}}}],
    input_schema={"type": "object",
                  "properties": {
                      "path": {"type": "string", "default": ".",
                               "description": "File or directory to inject into. Default: the whole workspace."},
                      "bindings": {"type": "object",
                                   "additionalProperties": {"type": "string"},
                                   "description": "Map a marker id to a different store id for the lookup."},
                      "dry_run": {"type": "boolean", "default": False,
                                  "description": "Report the plan (files, markers, missing ids) without writing."}},
                  "additionalProperties": False},
)

_spec(
    id="pub.platforms", title="Publishing platforms: consoles, docs, setup steps",
    description="Knowledge base of publishing platforms - Google Play, Apple App Store, GitHub, "
                "PyPI, npm, custom/self-hosted: console and docs URLs, account setup cost, the "
                "steps to go live, which credentials to store (with suggested store ids) and a "
                "placeholder example. Omit name to list all platforms briefly.",
    capability="publish.knowledge.platforms", group="publishing",
    risk="read", typical_latency_ms=5,
    tags=["publish", "platforms", "google play", "app store", "github", "pypi", "npm", "links"],
    see_also=["pub.payments", "pub.packaging"],
    input_schema={"type": "object",
                  "properties": {"name": {"type": "string",
                                          "description": "A platform key (see the list result) for full detail."}},
                  "additionalProperties": False},
)

_spec(
    id="pub.payments", title="Payment providers: setup steps and links",
    description="Knowledge base for setting up payments: Stripe, Paddle (merchant of record), "
                "Google Play Billing, Apple In-App Purchase (with the honest note that Apple Pay "
                "checkout is a PSP feature, not a direct API). Console/docs URLs, numbered setup "
                "steps, and the credentials to store with suggested ids. Omit provider for the list.",
    capability="publish.knowledge.payments", group="publishing",
    risk="read", typical_latency_ms=5,
    tags=["publish", "payments", "stripe", "paddle", "billing", "iap", "app store", "links"],
    see_also=["pub.platforms"],
    input_schema={"type": "object",
                  "properties": {"provider": {"type": "string",
                                               "description": "A provider key for full detail."}},
                  "additionalProperties": False},
)

_spec(
    id="pub.packaging", title="Packaging options: steps, commands, verification",
    description="Knowledge base of packaging targets - PyPI, GitHub Release, Windows installer "
                "(Inno Setup), MSI (WiX), Scoop, Chocolatey, Winget, Homebrew, self-hosted: "
                "the tooling, numbered steps with the actual commands, how to verify on a clean "
                "machine, and the gotchas. Omit target for the list.",
    capability="publish.knowledge.packaging", group="publishing",
    risk="read", typical_latency_ms=5,
    tags=["publish", "packaging", "pypi", "msi", "scoop", "chocolatey", "winget", "homebrew", "installer"],
    see_also=["pub.platforms", "pub.testers"],
    input_schema={"type": "object",
                  "properties": {"target": {"type": "string",
                                             "description": "A packaging key for full detail."}},
                  "additionalProperties": False},
)

_spec(
    id="pub.testers", title="Generate an AI-executable release test plan",
    description="Create a machine-executable release test plan: ordered steps (preflight, build, "
                "publish, verify) where each step is a tool call or a command with acceptance "
                "lines and on-fail behavior. Plans reference {{PUB.<id>}} placeholders, never raw "
                "secrets - run pub.inject before any step that needs one.",
    capability="publish.testplan", group="publishing",
    risk="read", stateful="host", typical_latency_ms=5,
    tags=["publish", "testers", "test plan", "release", "ai", "verify"],
    see_also=["pub.inject", "pub.packaging", "pub.platforms"],
    input_schema={"type": "object",
                  "properties": {
                      "platform": {"type": "string", "description": "Target platform key (from pub.platforms)."},
                      "packaging": {"type": "string", "description": "Packaging key (from pub.packaging)."},
                      "version": {"type": "string", "description": "Version being released, for the plan labels."}},
                  "additionalProperties": False},
)


# ============================================================== handler wiring
def register(reg: Any, *, engine: Any, shells: Any, fs: Any, journal: Any, skills: Any = None,
             load_skills: bool = True, publish: Any = None) -> dict[str, Any]:
    """Bind handlers to manifests on a registry. Returns a load report.

    ``publish`` is a ``core.publish.PublishStore`` (or None to disable the
    ``pub.*`` tools entirely - the manifests stay listed, gated off).
    """
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
    def shell_quote(args: list[Any], dialect: str | None = None,
                    shape: str = "both") -> dict[str, Any]:
        from ..shells.dialect import (
            DIALECT_FAMILY,
            UnsupportedDialect,
            command_line,
            dialect_family,
            quote_args,
        )

        dl = dialect
        if not dl:
            prof = getattr(engine, "profile", None)
            dl = (prof.preferred_dialect() if prof else None) or "bash"
        try:
            fam = dialect_family(dl)
        except UnsupportedDialect as exc:
            raise SkeletonKeyError(
                E.BAD_ARGS, str(exc),
                details={"dialect": dl, "supported": sorted(DIALECT_FAMILY)},
                hint="choose a dialect the toolkit renders for",
            ) from None
        quoted = quote_args(args, dl)
        data: dict[str, Any] = {"dialect": dl, "family": fam, "count": len(quoted)}
        if shape in ("tokens", "both"):
            data["tokens"] = quoted
        if shape in ("command", "both"):
            data["command"] = command_line(args, dl)
        data["argv"] = [a if isinstance(a, str) else str(a) for a in args]
        data["note"] = ("only needed to embed a value in a script body; for arguments, pass them "
                        "in shell.run {argv: [...]} instead - those never reach a shell parser")
        hints = ["the tokens are for a script body; shell.run {argv} needs no quoting"]
        if fam == "powershell":
            hints.append("PowerShell single quotes are literal: no $ expansion, no backtick escape")
        return ToolResult.success(data=data, hints=hints)

    def shell_run(script: str, dialect: str | None = None, cwd: str | None = None, timeout_s: float = 120.0,
                  env: dict[str, str] | None = None, env_mode: str = "inherit", strict: bool = True,
                  expects: str | None = None, session: str | None = None, background: bool = False,
                  stdin_text: str | None = None, capture_env: bool = False,
                  keep_script: bool = False, argv: list[Any] | None = None,
                  max_output_bytes: int | None = None,
                  ctx: Any = None) -> Any:
        # The real work lives in shells.execute.run_script because skill-synthesized tools
        # call the same function: one payload, one error mapping, no drift between the two
        # ways a script gets run.
        return run_script(engine, shells, script=script, dialect=dialect, cwd=cwd,
                          timeout_s=timeout_s, env=env, env_mode=env_mode, strict=strict,
                          expects=expects, session=session, background=background,
                          stdin_text=stdin_text, capture_env=capture_env, keep_script=keep_script,
                          argv=argv, max_output_bytes=max_output_bytes, ctx=ctx)

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

    def shell_job_watch(job_id: str, until: str, timeout_s: float = 30.0, poll_s: float = 0.5,
                        tail_bytes: int = 8000) -> dict[str, Any]:
        return shells.job_watch(job_id, until=until, timeout=float(timeout_s),
                                poll_s=float(poll_s), tail_bytes=int(tail_bytes))

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

    def fs_chmod(path: str, mode: str | int, recursive: bool = False, dry_run: bool = False,
                 ctx: Any = None) -> dict[str, Any]:
        return fs.chmod(path, mode, recursive=recursive, dry_run=dry_run,
                        task_id=ctx.task_id if ctx else "")

    def fs_undo(token: str | None = None, undo_token: str | None = None,
                 dry_run: bool = False, expect_sha: str | None = None) -> dict[str, Any]:
        picked = token or undo_token
        if not picked:
            raise SkeletonKeyError(
                E.BAD_ARGS, "fs.undo needs the token a mutating call returned",
                details={"hint": "fs.write/fs.patch/fs.delete/fs.move/fs.mkdir/fs.chmod return undo_token",
                         "recent": [e.get("token") for e in journal.list(limit=5)]},
            )
        return journal.undo(picked, dry_run=dry_run, expect_sha=expect_sha)

    def fs_redo(path: str | None = None, dry_run: bool = False) -> dict[str, Any]:
        return journal.redo(path, dry_run=dry_run)

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

    def policy_grant(tool: str, scope: str = "task", ctx: Any = None,
                     engine: Any = None) -> dict[str, Any]:
        """Record an approval grant with a receipt (PLAN P3: a receipt for every
        grant, so the ledger shows who approved what).

        A grant for a tool that itself requires approval is itself approval-gated:
        the approver sees the target in the prompt. A grant for a tool the caller
        could already run is record-keeping and needs no ceremony - granting
        permission for something you already have is how self-approval holes
        start, so only the dangerous grants go through the approver.
        """
        if ctx is None:
            raise SkeletonKeyError(
                E.BAD_ARGS, "policy.grant needs a task context to record the grant against",
                details={"advice": "a grant with no owner is a grant to nothing; the host "
                                   "must pass its CallContext"})
        target = engine.registry.get(tool)   # UNKNOWN_TOOL with suggestions, like every tool id
        pol = engine.config.policy
        risk = target.risk
        if target.id in pol.escalate or (target.group in pol.escalate):
            risk = "privileged"
        needs = engine._needs_approval(target, risk)
        granted_by = "no approval required for this target"
        if needs and scope != "once":
            req = ApprovalRequired("policy.grant", f"grant {scope} approval for {tool}",
                                   args={"tool": tool, "scope": scope}, manifest=target,
                                   token="grant:policy.grant", risk=risk, engine=engine)
            if engine.approver is None:
                raise SkeletonKeyError(
                    E.APPROVAL_REQUIRED,
                    f"granting {scope} approval for {tool} requires an approver and none is configured",
                    details={"prompt": req.prompt_payload(), "target_tool": tool,
                             "target_risk": risk, "scope": scope,
                             "advice": "run with an approver: --auto-approve on the CLI, "
                                       "SKELETONKEY_AUTO_APPROVE=1 for the MCP server, or an "
                                       "in-process approver in the autopilot"},
                    next_actions=[{"tool": "registry.describe", "args": {"tool": tool}}],
                )
            try:
                granted = bool(engine.approver(req))
            except Exception as exc:
                raise SkeletonKeyError(
                    E.INTERNAL, f"approver raised {type(exc).__name__}: {exc}",
                    details={"note": "an approver that throws is treated as a denial, never as consent"},
                ) from exc
            if not granted:
                raise SkeletonKeyError(
                    E.APPROVAL_REQUIRED, f"grant of {scope} approval for {tool} was declined",
                    details={"target_tool": tool, "target_risk": risk, "scope": scope,
                             "next": "ask the operator to approve the grant, or widen policy.auto_approve"},
                )
            granted_by = "approver callback"
        out = dict(engine.grant(ctx, scope=scope, tool=tool))
        out["target_risk"] = risk
        out["receipt"] = {"granted_by": granted_by, "tool": tool, "scope": scope,
                          "task_id": ctx.task_id, "session_id": ctx.session_id,
                          "ts": round(time.time(), 3)}
        return out

    # ---- publishing -------------------------------------------------------
    from .. import publish_data as _pubdata
    from ..core import publish as _pubmod

    def _pub_store():
        if publish is None:
            raise SkeletonKeyError(E.DEPENDENCY_MISSING, "publish store not configured",
                                   details={"hint": "toolkit.build(publish=PublishStore(...))"})
        return publish

    def _pub_files(path: str) -> list[str]:
        """Workspace-relative file list for scan/inject: one file or a whole dir."""
        st = fs.stat(path)
        if st.get("is_file"):
            return [st.get("display") or path]
        if not st.get("is_dir"):
            raise SkeletonKeyError(E.ENOENT, f"{path} does not exist",
                                   details={"path": path})
        base = (st.get("display") or path)
        g = fs.glob("**/*", root=base, limit=10_000, sort="path")
        prefix = "" if base in (".", "") else base.rstrip("/") + "/"
        return [prefix + m["path"] for m in g["matches"]]

    # Read errors that mean "this file is protected/absent - skip it honestly"
    # rather than "the whole publish is broken". A deny-ruled file may still
    # contain markers, but policy says we may not touch it, so we skip and say so.
    # (compare code STRINGS: SkeletonKeyError.code is a str, not an ErrorCode)
    _PUB_SKIP_CODES = {E.ENOENT.code, E.SANDBOX_VIOLATION.code, E.DENY_RULE.code,
                       E.READ_ONLY_MODE.code, E.PATH_UNREADABLE.code}

    def _pub_scan_file(fpath: str, store: Any) -> tuple[list[Any], list[str], str]:
        """Read one file, return (markers, skipped_notes, display_path)."""
        try:
            rd = fs.read(fpath)
        except SkeletonKeyError as exc:
            if exc.code in _PUB_SKIP_CODES:
                return [], [f"{fpath}: {exc.code.lower()}, skipped"], fpath
            raise
        if getattr(rd, "is_binary", False):
            return [], [f"{fpath}: binary, skipped"], fpath
        if getattr(rd, "truncated", False):
            return [], [f"{fpath}: larger than the read cap, skipped"], fpath
        return _pubmod.find_markers_in_text(rd.content, rd.path or fpath, store), [], fpath

    def pub_store_put(id: str, kind: str, value: str, note: str = "",
                      ctx: Any = None) -> dict[str, Any]:
        meta = _pub_store().put(id, kind, value, note)
        return {"stored": meta,
                "value": "<never returned; the store is write-only - pub.inject is the only way out>"}

    def pub_store_list(kind: str = "", ctx: Any = None) -> dict[str, Any]:
        store = _pub_store()
        if kind and kind not in _pubmod.KINDS:
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown kind {kind!r}",
                                   details={"kinds": list(_pubmod.KINDS)})
        metas = store.metas(kind)
        return {"count": len(metas), "entries": metas,
                "note": "metadata only - raw values are not returned by any tool"}

    def pub_store_delete(id: str, ctx: Any = None) -> dict[str, Any]:
        return _pub_store().delete(id)

    def pub_placeholders(path: str = ".", ctx: Any = None) -> dict[str, Any]:
        store = _pub_store()
        files = _pub_files(path)
        markers: list[Any] = []
        skipped: list[str] = []
        for fpath in files:
            m, notes, _ = _pub_scan_file(fpath, store)
            markers.extend(m)
            skipped.extend(notes)
        missing = sorted({m.id for m in markers if not m.bound})
        bound = [m for m in markers if m.bound]
        return {
            "scanned_files": len(files),
            "files_with_markers": len({m.file for m in markers}),
            "marker_count": len(markers),
            "bound": len(bound),
            "missing": len(markers) - len(bound),
            "markers": [m.to_dict() for m in markers],
            "missing_ids": missing,
            "skipped": skipped,
            "ready_to_publish": not missing,
            "note": "publish only when ready_to_publish is true; pub.inject refuses otherwise",
        }

    def pub_inject(path: str = ".", bindings: dict[str, str] | None = None,
                   dry_run: bool = False, ctx: Any = None) -> dict[str, Any]:
        store = _pub_store()
        files = _pub_files(path)
        # Pass 1: read every file, plan every replacement. NO writes yet.
        plan: list[dict[str, Any]] = []
        skipped: list[str] = []
        all_missing: set[str] = set()
        for fpath in files:
            try:
                rd = fs.read(fpath)
            except SkeletonKeyError as exc:
                if exc.code in _PUB_SKIP_CODES:
                    skipped.append(f"{fpath}: {exc.code.lower()}, skipped")
                    continue
                raise
            if getattr(rd, "is_binary", False):
                skipped.append(f"{fpath}: binary, skipped")
                continue
            if getattr(rd, "truncated", False):
                skipped.append(f"{fpath}: larger than the read cap, skipped")
                continue
            new_text, mk, missing = _pubmod.replace_markers(rd.content, store, bindings, file=fpath)
            if missing:
                all_missing.update(missing)
            plan.append({"path": fpath, "sha": rd.sha256, "newline": rd.newline,
                         "encoding": rd.encoding, "old": rd.content, "new": new_text,
                         "markers": [m.to_dict() for m in mk],
                         "changed": new_text != rd.content})
        if all_missing and not dry_run:
            raise SkeletonKeyError(
                E.ENOENT, "refusing to publish with unbound placeholders",
                details={"missing": sorted(all_missing),
                         "hint": "pub.store_put each id (or map it via `bindings`), then re-run"})
        if dry_run:
            return {"dry_run": True, "files": [
                        {"path": p["path"], "changed": p["changed"],
                         "markers": p["markers"]} for p in plan],
                    "skipped": skipped, "missing_ids": sorted(all_missing),
                    "hint": "no bytes written; rerun without dry_run to apply"}
        # Pass 2: write only the changed files, journaled, expect_sha-protected.
        written: list[dict[str, Any]] = []
        undo_tokens: list[str] = []
        task_id = ctx.task_id if ctx else ""
        for p in plan:
            if not p["changed"]:
                continue
            w = fs.write(p["path"], p["new"], expect_sha=p["sha"], task_id=task_id)
            d = w.to_dict()
            token = d.get("undo_token")
            if token:
                undo_tokens.append(token)
            written.append({"path": p["path"], "bytes_before": w.bytes_before,
                            "bytes_after": w.bytes_after, "sha_after": w.sha_after,
                            "undo_token": token})
        out = {"files_written": len(written), "files_scanned": len(plan),
               "written": written, "skipped": skipped, "missing_ids": sorted(all_missing)}
        if undo_tokens:
            out["undo_tokens"] = undo_tokens
            if task_id:
                # every engine call carries its own task id, so undo_task reverts
                # exactly this call's writes - the right scope for "undo this publish"
                out["undo"] = {"tool": "fs.undo_task", "args": {"task_id": task_id},
                               "note": "reverts every file this injection wrote"}
            elif len(undo_tokens) == 1:
                out["undo"] = {"tool": "fs.undo", "args": {"token": undo_tokens[0]}}
            else:
                out["undo"] = {"tool": "fs.undo", "args": {"token": undo_tokens[0]},
                               "note": "multiple files: call once per token in `undo_tokens`"}
        return out

    def pub_platforms(name: str = "", ctx: Any = None) -> dict[str, Any]:
        if not name:
            return {"count": len(_pubdata.PLATFORMS),
                    "platforms": [{"key": k, "name": v["name"], "console": v["console"]}
                                  for k, v in sorted(_pubdata.PLATFORMS.items())],
                    "hint": "call again with name=<key> for the full entry"}
        entry = _pubdata.PLATFORMS.get(name)
        if entry is None:
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown platform {name!r}",
                                   details={"known": sorted(_pubdata.PLATFORMS)})
        return entry

    def pub_payments(provider: str = "", ctx: Any = None) -> dict[str, Any]:
        if not provider:
            return {"count": len(_pubdata.PAYMENTS),
                    "providers": [{"key": k, "name": v["name"], "console": v["console"]}
                                  for k, v in sorted(_pubdata.PAYMENTS.items())],
                    "hint": "call again with provider=<key> for the full entry"}
        entry = _pubdata.PAYMENTS.get(provider)
        if entry is None:
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown payment provider {provider!r}",
                                   details={"known": sorted(_pubdata.PAYMENTS)})
        return entry

    def pub_packaging(target: str = "", ctx: Any = None) -> dict[str, Any]:
        if not target:
            return {"count": len(_pubdata.PACKAGING),
                    "targets": [{"key": k, "name": v["name"], "tooling": v["tooling"]}
                                for k, v in sorted(_pubdata.PACKAGING.items())],
                    "hint": "call again with target=<key> for the full entry"}
        entry = _pubdata.PACKAGING.get(target)
        if entry is None:
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown packaging target {target!r}",
                                   details={"known": sorted(_pubdata.PACKAGING)})
        return entry

    def pub_testers(platform: str = "", packaging: str = "", version: str = "",
                    ctx: Any = None) -> dict[str, Any]:
        if platform and platform not in _pubdata.PLATFORMS:
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown platform {platform!r}",
                                   details={"known": sorted(_pubdata.PLATFORMS)})
        if packaging and packaging not in _pubdata.PACKAGING:
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown packaging target {packaging!r}",
                                   details={"known": sorted(_pubdata.PACKAGING)})
        return _pubdata.test_plan(platform, packaging, version)

    add("shell.run", shell_run)
    add("shell.quote", shell_quote)
    add("shell.available", shell_available)
    add("shell.jobs", shell_jobs)
    add("shell.job_wait", shell_job_wait)
    add("shell.job_watch", shell_job_watch)
    add("shell.job_kill", shell_job_kill)
    add("shell.sessions", shell_sessions)
    add("shell.session_reset", shell_session_reset)
    for name, fn in [("fs.read", fs_read), ("fs.write", fs_write), ("fs.patch", fs_patch),
                     ("fs.search", fs_search), ("fs.list", fs_list), ("fs.glob", fs_glob),
                     ("fs.stat", fs_stat), ("fs.sniff", fs_sniff),
                     ("fs.delete", fs_delete), ("fs.move", fs_move),
                     ("fs.mkdir", fs_mkdir), ("fs.chmod", fs_chmod), ("fs.undo", fs_undo),
                     ("fs.redo", fs_redo), ("fs.undo_task", fs_undo_task),
                     ("fs.journal_list", fs_journal_list), ("profile.probe", profile_probe),
                     ("registry.list", registry_list), ("registry.search", registry_search),
                     ("registry.describe", registry_describe), ("registry.stats", registry_stats),
                     ("policy.grant", policy_grant)]:
        add(name, fn)
    for name, fn in [("pub.store_put", pub_store_put), ("pub.store_list", pub_store_list),
                     ("pub.store_delete", pub_store_delete), ("pub.placeholders", pub_placeholders),
                     ("pub.inject", pub_inject), ("pub.platforms", pub_platforms),
                     ("pub.payments", pub_payments), ("pub.packaging", pub_packaging),
                     ("pub.testers", pub_testers)]:
        add(name, fn)
    return report
