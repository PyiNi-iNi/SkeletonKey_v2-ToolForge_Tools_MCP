"""The result envelope: every tool returns this, or nothing.

Design rule for autopilot ergonomics (docs/TOOL-CONTRACT.md):
  * `ok` is the only truth the agent needs for control flow.
  * `data` is always JSON-able and always budget-limited.
  * Anything big goes to `artifacts` with a cursor, never inline.
  * `error` carries a recovery `hint`; `next_actions` carries suggested calls.
  * `metrics` lets the caller's governor reason about cost.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .errors import E, ErrorCode, ToolError
from .util import clip, compact_json, estimate_tokens, new_run_id, short_hash

DEFAULT_MAX_BYTES = 24_000


@dataclass
class Artifact:
    """A handle to spilled output. Fetch back with `fs.read` (path/range)."""

    id: str
    kind: str = "text"
    path: str | None = None
    bytes: int = 0
    sha256: str | None = None
    preview: str = ""
    # cursor to read the remainder; `next` is an opaque offset token
    lines: int | None = None
    truncated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in {
            "id": self.id, "kind": self.kind, "path": self.path, "bytes": self.bytes,
            "sha256": self.sha256, "lines": self.lines, "truncated": self.truncated,
        }.items() if v not in (None, False)}
        if self.preview:
            out["preview"] = self.preview
        if self.meta:
            out["meta"] = self.meta
        if self.path and self.truncated:
            # offset must be a legal value (>= 0): the host replays this verbatim.
            out["fetch_rest"] = {"tool": "fs.read",
                                 "args": {"path": self.path, "offset": 0, "limit_lines": 400},
                                 "note": "the spill file holds the complete payload; page through it"}
        return out


@dataclass
class Metrics:
    duration_ms: int = 0
    bytes_out: int = 0
    est_tokens: int = 0
    est_tokens_saved: int = 0
    exit_code: int | None = None
    shell: str | None = None
    provider: str | None = None
    attempts: int = 1
    cached: bool = False
    spill_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = {k: v for k, v in self.__dict__.items()
                if v not in (None, 0, False) and k != "extra" and not (k == "attempts" and v == 1)}
        base.update(self.extra or {})
        return base


@dataclass
class ToolResult:
    """The single return shape for the whole system (and for MCP tool results)."""

    ok: bool
    tool: str = ""
    run_id: str = field(default_factory=new_run_id)
    data: Any = None
    artifacts: list[Artifact] = field(default_factory=list)
    error: ToolError | None = None
    hints: list[str] = field(default_factory=list)
    next_actions: list[dict[str, Any]] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)
    # state the agent may assume for later calls (cwd, selected provider, ...)
    context: dict[str, Any] = field(default_factory=dict)
    # non-fatal warnings worth surfacing to the model
    warnings: list[str] = field(default_factory=list)

    # -------------------------------------------------------------- constructors
    @classmethod
    def success(
        cls,
        data: Any = None,
        *,
        tool: str = "",
        hints: list[str] | None = None,
        next_actions: list[dict[str, Any]] | None = None,
        artifacts: list[Artifact] | None = None,
        context: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        metrics: Metrics | None = None,
    ) -> ToolResult:
        return cls(
            ok=True, tool=tool, data=data, hints=hints or [], next_actions=next_actions or [],
            artifacts=artifacts or [], context=context or {}, warnings=warnings or [],
            metrics=metrics or Metrics(),
        )

    @classmethod
    def failure(
        cls,
        err: ToolError | ErrorCode | str,
        message: str = "",
        *,
        tool: str = "",
        details: dict[str, Any] | None = None,
        data: Any = None,
        hints: list[str] | None = None,
        next_actions: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
        metrics: Metrics | None = None,
    ) -> ToolResult:
        if isinstance(err, ErrorCode):
            error = ToolError.from_code(err, message, details=details)
        elif isinstance(err, ToolError):
            error = err
            if message and not error.message:
                error.message = message
            if details:
                error.details = {**error.details, **details}
        else:  # bare string -> generic internal
            error = ToolError.from_code(E.INTERNAL, message or str(err), details=details)
        return cls(
            ok=False, tool=tool, data=data, error=error, hints=hints or [],
            next_actions=next_actions or [], context=context or {}, metrics=metrics or Metrics(),
        )

    # ------------------------------------------------------------------ rendering
    def to_dict(self, *, max_bytes: int | None = DEFAULT_MAX_BYTES, spill_dir: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok, "tool": self.tool, "run_id": self.run_id}
        if self.data is not None:
            payload["data"] = self.data
        if self.artifacts:
            payload["artifacts"] = [a.to_dict() for a in self.artifacts]
        if self.error:
            payload["error"] = self.error.to_dict()
        if self.hints:
            payload["hints"] = self.hints
        if self.next_actions:
            payload["next_actions"] = self.next_actions
        if self.warnings:
            payload["warnings"] = self.warnings
        m = self.metrics.to_dict()
        if m:
            payload["metrics"] = m
        if self.context:
            payload["context"] = self.context

        if max_bytes and max_bytes > 0:
            payload, spilled = _apply_budget(payload, max_bytes, spill_dir=spill_dir, tool=self.tool)
            if spilled and not any(a.id == spilled[0].id for a in self.artifacts):
                self.artifacts.extend(spilled)
        return payload

    def to_json(self, *, max_bytes: int | None = DEFAULT_MAX_BYTES, indent: bool = False) -> str:
        d = self.to_dict(max_bytes=max_bytes)
        return compact_json(d) if not indent else json.dumps(d, indent=2, ensure_ascii=False)

    def to_text(self, *, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
        """Fallback rendering for hosts that ignore structuredContent."""
        return clip(self.to_json(max_bytes=max_bytes), max_bytes)

    # ------------------------------------------------------------------ helpers
    @property
    def is_error(self) -> bool:
        return not self.ok

    def add_warning(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)

    def estimate(self) -> None:
        """Two passes, so `bytes_out` describes the payload that actually ships
        (metrics themselves are part of that payload)."""
        for _ in range(2):
            text = self.to_json(max_bytes=None)
            tokens = estimate_tokens(text)
            nbytes = len(text.encode("utf-8"))
            if (self.metrics.est_tokens, self.metrics.bytes_out) == (tokens, nbytes):
                break
            self.metrics.est_tokens, self.metrics.bytes_out = tokens, nbytes


def _apply_budget(
    payload: dict[str, Any], max_bytes: int, *, spill_dir: str | None, tool: str
) -> tuple[dict[str, Any], list[Artifact]]:
    """Shrink `data` until the *serialized result* fits the budget.

    Character-count clipping alone is a lie: JSON escaping and the artifact and
    warning fields we append afterwards all change the byte count. So we spill,
    then bisect the inline size against the real serialized length.
    """
    text = json.dumps(payload, ensure_ascii=False)
    if len(text.encode("utf-8")) <= max_bytes:
        return payload, []

    data = payload.get("data")
    if data is None:
        return {"ok": payload["ok"], "tool": tool, "run_id": payload.get("run_id"),
                "data": {"_truncated_json": clip(text, max(0, max_bytes - 300))},
                "warnings": [*payload.get("warnings", []), "result exceeded budget and was clipped"]}, []

    data_text = data if isinstance(data, str) else compact_json(data)
    artifact = Artifact(
        id=f"art_{new_run_id()}", kind="text" if isinstance(data, str) else "json",
        bytes=len(data_text.encode("utf-8")), truncated=True,
        preview=clip(data_text, 900),
    )
    if spill_dir:
        os.makedirs(spill_dir, exist_ok=True)
        path = os.path.join(spill_dir, f"{artifact.id}.txt")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(data_text)
            artifact.path = path
            artifact.sha256 = short_hash(data_text.encode("utf-8"), 64)
        except OSError:
            artifact.meta["spill_error"] = "could not write spill file"

    note = (f"data truncated to fit {max_bytes}B budget"
            + (f"; full copy at {artifact.path}" if artifact.path else "; preview only"))
    # payload["artifacts"] is already serialized here (to_dict built it), so the spill
    # artifact is appended rather than re-derived - calling to_dict() on a dict crashed
    # every oversized result, which is precisely the path that must never fail.
    prior = [a if isinstance(a, dict) else a.to_dict() for a in (payload.get("artifacts") or [])]
    base = {**payload, "warnings": [*payload.get("warnings", []), note],
            "artifacts": [*prior, artifact.to_dict()]}

    def build(inline: str) -> dict[str, Any]:
        return {**base, "data": {"inlined": inline, "spilled": bool(artifact.path),
                                 "total_bytes": artifact.bytes}}

    fits = _bisect_inline(build, data_text, max_bytes)
    payload_out = build(fits)
    return payload_out, [artifact]


def _bisect_inline(build: Any, text: str, max_bytes: int) -> str:
    """Largest clip(text, n) whose enclosing payload serializes under budget."""
    def size(n: int) -> int:
        # Measure with the *roomiest* common serializer (json.dumps defaults put a
        # space after , and :) so the promise holds however the host re-encodes it.
        return len(json.dumps(build(clip(text, n)), ensure_ascii=False).encode("utf-8"))

    lo, hi, best = 0, len(text), ""
    if size(hi) <= max_bytes:
        return text
    while lo <= hi:
        mid = (lo + hi) // 2
        if size(mid) <= max_bytes:
            best = clip(text, mid)
            lo = mid + 1
        else:
            hi = mid - 1
    return best or clip(text, 0)

