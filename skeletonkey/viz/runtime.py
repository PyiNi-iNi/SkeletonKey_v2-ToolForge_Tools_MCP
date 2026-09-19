"""V5: the runtime overlay - what the tools actually did, not what they claim.

Everything so far renders *declarations*: the manifest's `typical_latency_ms`,
its `typical_output_bytes`, its risk class. This module reads the ledger and
puts measurements next to them, which turns the visualizer from a diagram into
a diagnostic:

  * per-tool call count, success rate, p50/p95 latency, bytes returned;
  * **contract drift** - where the measured p95 outruns the declared typical
    latency by more than a factor, or a tool declared idempotent keeps failing.
    That list is a manifest-accuracy linter, and it is the reason to build this.

Three rules this module does not bend:

1. **Absent by default.** No ledger, or an unreadable one, means an overlay of
   nothing and a page that works exactly as it did before. A diagnostic that
   breaks the thing it diagnoses is worse than no diagnostic.
2. **Redacted.** Ledger rows carry argument digests and previews. Everything
   that reaches the model goes through `core.redact`, and the tests assert no
   credential-shaped string survives.
3. **Read-only.** The ledger is opened for reading. Nothing here appends,
   truncates, or repairs it.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any

from skeletonkey.core.redact import redact_obj, redact_text

# A tool whose measured p95 exceeds `declared * DRIFT_FACTOR` is mis-declaring
# its cost to the planner. 3x is deliberately forgiving: cold caches, a loaded
# box and a first-call import all cost real time without being a wrong manifest.
DRIFT_FACTOR = 3.0

# Below this many calls, a percentile is an anecdote. Drift is only reported
# once there is enough evidence to be worth acting on.
MIN_CALLS_FOR_DRIFT = 5


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. No numpy, no interpolation, no surprises."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


@dataclass
class ToolRuntime:
    """Measured behaviour of one tool, from the ledger."""

    id: str
    calls: int = 0
    ok: int = 0
    failed: int = 0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    max_ms: float = 0.0
    mean_ms: float = 0.0
    last_run_id: str = ""
    last_ts: float = 0.0
    error_codes: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return (self.ok / self.calls) if self.calls else 0.0

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "calls": self.calls, "ok": self.ok,
                "failed": self.failed,
                "success_rate": round(self.success_rate, 3),
                "p50_ms": round(self.p50_ms, 1), "p95_ms": round(self.p95_ms, 1),
                "max_ms": round(self.max_ms, 1), "mean_ms": round(self.mean_ms, 1),
                "last_run_id": self.last_run_id, "last_ts": self.last_ts,
                "error_codes": dict(sorted(self.error_codes.items()))}


@dataclass
class Drift:
    """One way a manifest disagrees with reality."""

    id: str
    kind: str            # latency | reliability
    declared: Any
    measured: Any
    ratio: float = 0.0
    calls: int = 0
    detail: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "declared": self.declared,
                "measured": self.measured, "ratio": round(self.ratio, 2),
                "calls": self.calls, "detail": self.detail}


def read_runtime(ledger: Any, *, limit: int | None = 20_000) -> dict[str, ToolRuntime]:
    """Fold the ledger into per-tool measurements. Never raises.

    `ledger` may be a `core.ledger.Ledger`, a path, or None. Anything that
    cannot be read yields an empty overlay, because the page must survive a
    missing, truncated or half-written audit trail.
    """
    entries = _entries(ledger, limit=limit)
    durations: dict[str, list[float]] = {}
    out: dict[str, ToolRuntime] = {}

    for entry in entries:
        tool = getattr(entry, "tool", "") or ""
        if not tool:
            continue
        row = out.setdefault(tool, ToolRuntime(id=tool))
        row.calls += 1
        ms = float(getattr(entry, "duration_ms", 0) or 0)
        durations.setdefault(tool, []).append(ms)
        if getattr(entry, "ok", True):
            row.ok += 1
        else:
            row.failed += 1
            code = getattr(entry, "error_code", None) or "unknown"
            row.error_codes[code] = row.error_codes.get(code, 0) + 1
        ts = float(getattr(entry, "ts", 0) or 0)
        if ts >= row.last_ts:
            row.last_ts = ts
            row.last_run_id = getattr(entry, "run_id", "") or row.last_run_id

    for tool, row in out.items():
        seen = durations.get(tool, [])
        row.p50_ms = percentile(seen, 50)
        row.p95_ms = percentile(seen, 95)
        row.max_ms = max(seen) if seen else 0.0
        row.mean_ms = sum(seen) / len(seen) if seen else 0.0
    return out


def _entries(ledger: Any, *, limit: int | None) -> list[Any]:
    """Best-effort read. A broken ledger is an empty overlay, never an exception."""
    if ledger is None:
        return []
    try:
        if isinstance(ledger, str):
            if not os.path.isfile(ledger):
                return []
            from skeletonkey.core.ledger import Ledger

            ledger = Ledger(ledger, enabled=False)
        return list(ledger.read(limit=limit, tail=True))
    except Exception:
        # A torn line, a permissions error, a ledger from a future build: the
        # overlay is optional, so degrade to nothing rather than take the page
        # down with it.
        return []


def find_drift(model: Any, runtime: dict[str, ToolRuntime], *,
               factor: float = DRIFT_FACTOR,
               min_calls: int = MIN_CALLS_FOR_DRIFT) -> list[Drift]:
    """Where the manifests disagree with what actually happened.

    This is the manifest-accuracy linter. It only speaks when it has evidence:
    fewer than `min_calls` observations and a tool is left alone.
    """
    out: list[Drift] = []
    for node in sorted(model.nodes, key=lambda n: n.id):
        row = runtime.get(node.id)
        if row is None or row.calls < min_calls:
            continue

        declared = float(node.typical_latency_ms or 0)
        if declared > 0 and row.p95_ms > declared * factor:
            out.append(Drift(
                id=node.id, kind="latency", declared=int(declared),
                measured=round(row.p95_ms, 1),
                ratio=row.p95_ms / declared, calls=row.calls,
                detail=(f"declares {int(declared)} ms but p95 is "
                        f"{row.p95_ms:.0f} ms over {row.calls} calls"),
            ))

        if row.failed and row.success_rate < 0.5:
            out.append(Drift(
                id=node.id, kind="reliability", declared="expected to succeed",
                measured=f"{row.success_rate:.0%} success",
                ratio=row.success_rate, calls=row.calls,
                detail=(f"{row.failed} of {row.calls} calls failed "
                        f"({', '.join(sorted(row.error_codes))})"),
            ))
    return out


def recent_calls(ledger: Any, *, limit: int = 40) -> list[dict[str, Any]]:
    """The tail of the activity feed, redacted.

    Argument previews are the risky field: they are the only place a secret can
    reach the page, so they go through `redact_obj`/`redact_text` on the way.
    """
    rows: list[dict[str, Any]] = []
    for entry in _entries(ledger, limit=limit)[-limit:]:
        preview = getattr(entry, "result_preview", "") or ""
        if preview:
            preview = redact_text(preview[:200])[0]
        rows.append({
            "run_id": getattr(entry, "run_id", ""),
            "tool": getattr(entry, "tool", ""),
            "ok": bool(getattr(entry, "ok", True)),
            "duration_ms": getattr(entry, "duration_ms", 0),
            "ts": getattr(entry, "ts", 0),
            "error_code": getattr(entry, "error_code", None),
            "task_id": getattr(entry, "task_id", ""),
            "args": redact_obj(getattr(entry, "args", {}) or {}),
            "preview": preview,
        })
    return rows


def attach_runtime(model: Any, ledger: Any, *, limit: int | None = 20_000,
                   feed: int = 40) -> dict[str, Any]:
    """Attach the overlay to a model, in place, and return its summary.

    Every node gains a `runtime` dict (empty when the tool was never called),
    and the model gains `model.runtime` with the drift list and the feed. The
    return value is the summary, so a caller can report without re-deriving.
    """
    runtime = read_runtime(ledger, limit=limit)
    drift = find_drift(model, runtime)

    for node in model.nodes:
        row = runtime.get(node.id)
        node.runtime = row.to_json() if row else {}

    # Tools the ledger remembers calling that the registry no longer has: a
    # renamed tool, an uninstalled skill, or a ledger from another workspace.
    # Worth surfacing rather than silently dropping - it is usually a clue.
    known = {n.id for n in model.nodes}
    orphans = sorted(set(runtime) - known)

    summary = {
        "available": bool(runtime),
        "tools_called": len(runtime),
        "orphans": orphans,
        "calls": sum(r.calls for r in runtime.values()),
        "failures": sum(r.failed for r in runtime.values()),
        "drift": [d.to_json() for d in drift],
        "drift_count": len(drift),
        "busiest": [r.to_json() for r in
                    sorted(runtime.values(), key=lambda r: -r.calls)[:10]],
        "slowest": [r.to_json() for r in
                    sorted(runtime.values(), key=lambda r: -r.p95_ms)[:10]],
        "recent": recent_calls(ledger, limit=feed),
    }
    model.runtime = summary
    return summary
