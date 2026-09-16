"""Unified error taxonomy for the features platform.

Mirrors the toolkit's envelope error-codes: every raised error carries a
stable machine-readable ``code`` so API layers can map to HTTP statuses
without string-matching messages.
"""

from __future__ import annotations

from typing import Any

_OPENAI_TYPES = {
    "auth_error": "authentication_error",
    "not_found": "invalid_request_error",
    "validation_error": "invalid_request_error",
    "config_error": "server_error",
    "backend_unavailable": "server_error",
    "upstream_error": "server_error",
    "streaming_error": "server_error",
    "execution_error": "server_error",
    "feature_error": "server_error",
}


class FeatureError(Exception):
    """Base class for all feature errors."""

    code = "feature_error"
    retryable = False
    http_status = 500

    def __init__(self, message: str, *, hint: str | None = None, detail: Any = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "type": _OPENAI_TYPES.get(self.code, "server_error"),
            "retryable": self.retryable,
        }
        if self.hint:
            out["hint"] = self.hint
        if self.detail is not None:
            out["detail"] = self.detail
        return out


class ConfigError(FeatureError):
    code = "config_error"
    http_status = 500


class ValidationError(FeatureError):
    code = "validation_error"
    http_status = 422


class NotFoundError(FeatureError):
    code = "not_found"
    http_status = 404


class AuthError(FeatureError):
    code = "auth_error"
    http_status = 401


class BackendUnavailableError(FeatureError):
    """A heavy optional backend is not installed. ``hint`` is the pip extra."""

    code = "backend_unavailable"
    http_status = 503


class StreamingError(FeatureError):
    code = "streaming_error"
    retryable = True
    http_status = 502


class UpstreamError(FeatureError):
    """A remote provider (OpenAI/Anthropic/...) answered with an error."""

    code = "upstream_error"
    retryable = True
    http_status = 502


class ExecutionError(FeatureError):
    """Sandboxed code execution failed or was killed."""

    code = "execution_error"
    http_status = 502


def from_error(exc: Exception, http_status: int | None = None) -> dict[str, Any]:
    """Map any exception to an error envelope dict (for API responses)."""
    if isinstance(exc, FeatureError):
        d = exc.to_dict()
        if http_status is not None:
            d["http_status"] = http_status
        return d
    return {
        "code": "internal_error",
        "message": f"{type(exc).__name__}: {exc}",
        "retryable": False,
    }
