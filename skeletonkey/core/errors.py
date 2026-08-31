"""Error taxonomy for autonomous recovery.

A model reading `exit code 1` will guess. A model reading
`{"code":"MISSING_BINARY","missing":"rg","fallback":"grep","retryable":false}`
will act. Every failure surface in SkeletonKey is normalized into one of these
codes with an explicit recovery path, because that is what makes an unattended
autopilot loop cheap to run instead of expensively confused.

See docs/TOOL-CONTRACT.md for the normative description.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorClass(StrEnum):
    """Top-level split: whose fault is it, and does retrying help?"""

    USAGE = "usage"              # caller passed bad input -> fix args, retry OK
    ENVIRONMENT = "environment"  # box is missing/incorrect something -> may be fixable
    POLICY = "policy"            # we refused on purpose (sandbox, approval, budget)
    EXECUTION = "execution"      # the operation itself failed
    INTERNAL = "internal"        # our bug -> never retry blindly


@dataclass(frozen=True)
class ErrorCode:
    code: str
    cls: ErrorClass
    retryable: bool
    summary: str
    # Advice rendered into the result so the agent gets a next step without a
    # second round trip. Placeholders are filled from `details`.
    hint: str = ""


class _SafeDict(dict):
    """format_map backend: unknown placeholders render as a hint, never a crash."""

    def __missing__(self, key: str) -> str:
        return f"<{key} not reported>"

    def __getattr__(self, key: str) -> Any:  # allow {a.b} on nested dicts
        try:
            value = self[key]
        except KeyError:
            return f"<{key} not reported>"
        return value if not isinstance(value, dict) else _SafeDict(value)


class E:
    """The registry of error codes. Keep stable: agents memoize on these."""

    # ---- usage
    BAD_ARGS = ErrorCode("BAD_ARGS", ErrorClass.USAGE, True, "Arguments failed schema validation.",
                         "Fix the field paths listed in error.details.errors, then retry.")
    MISSING_ARG = ErrorCode("MISSING_ARG", ErrorClass.USAGE, True, "A required argument was not provided.",
                            "Supply {missing!r}; full schema is in error.details.schema.")
    UNKNOWN_TOOL = ErrorCode("UNKNOWN_TOOL", ErrorClass.USAGE, False, "No tool with that name is registered.",
                              "Call `registry.search` with the capability you need, then use the returned name.")
    TOOL_NOT_ADVERTISED = ErrorCode("TOOL_NOT_ADVERTISED", ErrorClass.USAGE, False,
                                    "Tool exists but is not currently available on this host.",
                                    "Reason is in `details.gate`; `registry.search(include_gated=true)` shows all gates.")

    # ---- environment
    MISSING_BINARY = ErrorCode("MISSING_BINARY", ErrorClass.ENVIRONMENT, False, "A required executable is absent.",
                               "Install it, or use the provider named in error.details.fallback.")
    MISSING_SHELL = ErrorCode("MISSING_SHELL", ErrorClass.ENVIRONMENT, False, "Requested shell is not usable here.",
                              "Use one of the dialects in error.details.available.")
    UNSUPPORTED_PLATFORM = ErrorCode("UNSUPPORTED_PLATFORM", ErrorClass.ENVIRONMENT, False,
                                     "Tool does not run on this OS/architecture.", "")
    DEPENDENCY_MISSING = ErrorCode("DEPENDENCY_MISSING", ErrorClass.ENVIRONMENT, False,
                                   "A python package the tool wants is not importable.",
                                   "Re-run with the optional extra, or accept the degraded path.")
    PATH_UNREADABLE = ErrorCode("PATH_UNREADABLE", ErrorClass.ENVIRONMENT, True,
                                "Path exists but could not be read (permissions/locking).",
                                "On Windows a running process may hold the file; check `details.holder`.")

    # ---- policy
    SANDBOX_VIOLATION = ErrorCode("SANDBOX_VIOLATION", ErrorClass.POLICY, False,
                                  "Resolved path escapes the allowed roots or matches a deny rule.",
                                  "Use a path inside `details.allowed_roots`, or declare the extra root in config.")
    APPROVAL_REQUIRED = ErrorCode("APPROVAL_REQUIRED", ErrorClass.POLICY, False,
                                  "Action needs a human/approval and none was granted.",
                                  "Re-issue with an approval token, or set policy.auto_approve for this risk class.")
    BUDGET_EXCEEDED = ErrorCode("BUDGET_EXCEEDED", ErrorClass.POLICY, False,
                                "Task budget (tokens/wall time/calls) exhausted.",
                                "Summarize what you have; do not retry until budget is raised.")
    DENY_RULE = ErrorCode("DENY_RULE", ErrorClass.POLICY, False, "A configured deny rule matched.", "")
    READ_ONLY_MODE = ErrorCode("READ_ONLY_MODE", ErrorClass.POLICY, False, "Mutation attempted in read-only mode.",
                               "Turn read_only off for this phase, or use dry_run to record intent only.")

    # ---- execution
    NONZERO_EXIT = ErrorCode("NONZERO_EXIT", ErrorClass.EXECUTION, False, "Command ran and returned non-zero.",
                             "Read `data.stderr_tail` before retrying; a different flag/subcommand usually applies.")
    TIMEOUT = ErrorCode("TIMEOUT", ErrorClass.EXECUTION, True, "Command exceeded its timeout and was killed.",
                        "Raise timeout_s ({timeout_s}s here), split the work, or run with background=true.")
    PARSE = ErrorCode("PARSE", ErrorClass.EXECUTION, False, "Output could not be parsed as requested.",
                      "Drop `expects='json'` or inspect the raw stream.")
    CONFLICT = ErrorCode("CONFLICT", ErrorClass.EXECUTION, True, "Precondition failed or content changed.",
                         "Re-read the current state, then recompute the mutation against it.")
    PATCH_CONFLICT = ErrorCode("PATCH_CONFLICT", ErrorClass.EXECUTION, True,
                               "Patch context did not match the file (it changed under you).",
                               "Re-read the file with `fs.read`, then re-derive the edit.")
    AMBIGUOUS_MATCH = ErrorCode("AMBIGUOUS_MATCH", ErrorClass.USAGE, True,
                                "A find/replace target matched multiple locations and replace_all was false.",
                                "Add surrounding context to disambiguate, or set replace_all=true.")
    IO = ErrorCode("IO", ErrorClass.EXECUTION, True, "Low-level I/O failure.", "")
    ENOENT = ErrorCode("ENOENT", ErrorClass.EXECUTION, False, "Path does not exist.",
                       "Check `details.suggested` for close matches.")
    EEXIST = ErrorCode("EEXIST", ErrorClass.EXECUTION, False, "Path already exists where a new one was expected.",
                       "Set overwrite=true if that is intended.")
    TOO_LARGE = ErrorCode("TOO_LARGE", ErrorClass.EXECUTION, False, "Input/output exceeds a configured size limit.",
                          "Page with offset/limit_lines, or raise the budget.")

    # ---- internal
    INTERNAL = ErrorCode("INTERNAL", ErrorClass.INTERNAL, False, "Unexpected error inside SkeletonKey.",
                         "Report with `details.trace_id`; do not retry.")
    CANCELLED = ErrorCode("CANCELLED", ErrorClass.INTERNAL, False, "Cancelled by client.", "")
    NOT_IMPLEMENTED = ErrorCode("NOT_IMPLEMENTED", ErrorClass.INTERNAL, False, "Phase-gated feature not built yet.",
                                "See PLAN.md for the phase that delivers it.")

    @classmethod
    def all(cls) -> list[ErrorCode]:
        return [v for v in vars(cls).values() if isinstance(v, ErrorCode)]

    @classmethod
    def by_code(cls) -> dict[str, ErrorCode]:
        return {c.code: c for c in cls.all()}


@dataclass
class ToolError:
    """Serializable error payload carried inside a ToolResult."""

    code: str
    error_class: str
    message: str
    retryable: bool = False
    hint: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_code(
        cls,
        code: ErrorCode,
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
        hint: str = "",
    ) -> ToolError:
        det = details or {}
        try:
            rendered_hint = code.hint.format_map(_SafeDict(det)) if code.hint else ""
        except Exception:
            rendered_hint = code.hint
        return cls(
            code=code.code,
            error_class=code.cls.value,
            message=message or code.summary,
            retryable=code.retryable,
            hint=hint or rendered_hint,
            details=det,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "class": self.error_class,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


class SkeletonKeyError(Exception):
    """Raised internally; the engine converts it into a ToolError payload."""

    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        *,
        details: dict[str, Any] | None = None,
        hint: str = "",
        next_actions: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message or code.summary)
        self.err = ToolError.from_code(code, message, details=details, hint=hint)
        self.next_actions = next_actions or []

    @property
    def code(self) -> str:
        return self.err.code

    @property
    def details(self) -> dict[str, Any]:
        return self.err.details


def classify_exception(exc: BaseException) -> ToolError:
    """Map arbitrary Python exceptions onto the taxonomy."""
    if isinstance(exc, SkeletonKeyError):
        return exc.err
    if isinstance(exc, TimeoutError):
        return ToolError.from_code(E.TIMEOUT, str(exc) or "operation timed out")
    if isinstance(exc, FileNotFoundError):
        return ToolError.from_code(E.PATH_UNREADABLE if isinstance(exc, PermissionError) else E.ENOENT, str(exc))
    if isinstance(exc, PermissionError):
        return ToolError.from_code(E.PATH_UNREADABLE, str(exc))
    if isinstance(exc, FileExistsError):
        return ToolError.from_code(E.EEXIST, str(exc))
    if isinstance(exc, NotImplementedError):
        return ToolError.from_code(E.NOT_IMPLEMENTED, str(exc))
    if isinstance(exc, (ValueError, TypeError)):
        return ToolError.from_code(E.BAD_ARGS, f"{type(exc).__name__}: {exc}")
    return ToolError.from_code(E.INTERNAL, f"{type(exc).__name__}: {exc}",
                               details={"exception": type(exc).__name__})
