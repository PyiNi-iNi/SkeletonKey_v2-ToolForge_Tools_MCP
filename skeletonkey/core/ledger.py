"""Append-only call ledger with a hash chain.

Autonomous runs need answers to three questions after the fact: what did the
agent do, in what order, and can I trust this record wasn't edited? One NDJSON
line per call with `prev`/`entry_sha` chaining gives us all three, plus replay.

Kept deliberately simple: fsync-optional batching, no DB, survives SIGKILL
(torn last line is detected and truncated on open).
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .util import compact_json, content_hash


@dataclass
class LedgerEntry:
    """One line of the audit trail.

    Every field has a default matching the value `to_dict` omits, so a record
    written compactly still reads back exactly - a hash chain that cannot be
    replayed is not an audit trail.
    """

    seq: int = 0
    ts: float = 0.0
    run_id: str = ""
    tool: str = ""
    args_digest: str = ""
    ok: bool = True
    duration_ms: int = 0
    error_code: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    result_digest: str = ""
    result_preview: str = ""
    risk: str = ""
    task_id: str = ""
    session_id: str = ""
    redacted: list[str] = field(default_factory=list)
    # what this context exposed / withheld to the agent, and why the call stopped -
    # the per-call mirror of the per-tool discovery receipt (why you never saw a tool)
    context_receipt: dict[str, Any] | None = None
    prev: str = ""
    entry_sha: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {}
        for k, v in self.__dict__.items():
            default = LedgerEntry.__dataclass_fields__[k].default
            if (v is not default and (v not in (None, "", [], 0) or k in ("seq", "ok")
                                     or (default is dataclasses.MISSING))) or (k in ("seq", "ts", "ok", "entry_sha", "prev", "tool") and v not in (None,)):
                d[k] = v
        return d

    def compute_sha(self) -> str:
        body = {k: v for k, v in self.__dict__.items() if k != "entry_sha"}
        return content_hash(body)


class Ledger:
    def __init__(self, path: str, *, enabled: bool = True, fsync: bool = False,
                 store_args: bool = True, max_arg_bytes: int = 4000, redact: bool = True) -> None:
        self.path = os.path.abspath(path)
        self.enabled = enabled
        self.fsync = fsync
        self.store_args = store_args
        self.max_arg_bytes = max_arg_bytes
        self.redact = redact
        self._fh: Any = None
        self._seq = 0
        self._prev = ""
        self._buffer: list[str] = []
        if self.enabled:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._prev, self._seq = self._recover_tail()
            self._fh = open(self.path, "a", encoding="utf-8", newline="\n")  # noqa: SIM115

    # ------------------------------------------------------------------ internals
    def _recover_tail(self) -> tuple[str, int]:
        """Read the last valid line; drop a torn tail so appends stay parseable."""
        if not os.path.exists(self.path):
            return "", 0
        try:
            with open(self.path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                if size == 0:
                    return "", 0
                chunk = min(size, 64_000)
                fh.seek(size - chunk)
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            return "", 0
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        if not lines:
            return "", 0
        # a torn final line would fail json.loads - trim it off the file
        try:
            last = json.loads(lines[-1])
            seq = int(last.get("seq", len(lines)))
        except (ValueError, TypeError):
            good = "\n".join(lines[:-1])
            keep = len(good.encode("utf-8")) + (1 if good else 0)
            try:
                with open(self.path, "r+b") as fh:
                    fh.truncate(keep if good else 0)
            except OSError:
                pass
            if not good:
                return "", 0
            try:
                last = json.loads(lines[-2])  # type: ignore[index]
                seq = int(last.get("seq", 0))
                lines = lines[:-1]
            except (ValueError, IndexError, TypeError):
                return "", 0
        return str(last.get("entry_sha", "")), seq

    # --------------------------------------------------------------------- write
    def append(
        self,
        *,
        tool: str,
        args: dict[str, Any] | None,
        ok: bool,
        duration_ms: int,
        run_id: str = "",
        error_code: str | None = None,
        result: Any = None,
        risk: str = "",
        task_id: str = "",
        session_id: str = "",
        context_receipt: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            seq=self._seq + 1, ts=time.time(), run_id=run_id, tool=tool,
            args_digest=content_hash(args or {})[:32], ok=ok, duration_ms=int(duration_ms),
            error_code=error_code, risk=risk, task_id=task_id, session_id=session_id,
            context_receipt=context_receipt,
            prev=self._prev,
        )
        if self.store_args and args:
            blob = compact_json(args)
            if len(blob) > self.max_arg_bytes:
                blob = blob[: self.max_arg_bytes] + "...[truncated]"
                entry.redacted.append("args_truncated")
            if self.redact:
                from .redact import redact_obj

                parsed = _try_json(blob)
                blob = compact_json(redact_obj(parsed)) if parsed is not None else compact_json(
                    redact_obj({"raw": blob})["raw"])
                entry.redacted.append("redacted" if "REDACTED" in blob else "clean")
            entry.args = {"_json": blob[: self.max_arg_bytes]}
        if result is not None:
            text = result if isinstance(result, str) else compact_json(result)
            entry.result_digest = content_hash(text)[:32]
            entry.result_preview = text[:400] + ("...[truncated]" if len(text) > 400 else "")
            if self.redact:
                from .redact import redact_text

                cleaned, hits = redact_text(entry.result_preview)
                if hits:
                    entry.result_preview = cleaned
                    entry.redacted.append("secrets:" + ",".join(hits))
        entry.entry_sha = entry.compute_sha()

        if self.enabled and self._fh is not None:
            self._fh.write(compact_json(entry.to_dict()) + "\n")
            self._fh.flush()
            if self.fsync:
                os.fsync(self._fh.fileno())
        self._seq = entry.seq
        self._prev = entry.entry_sha
        return entry

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except (OSError, ValueError):
                pass
            self._fh = None

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------------------------------------------------------------------- read
    def read(self, *, limit: int | None = None, tail: bool = True,
             tool: str | None = None, only_failures: bool = False) -> Iterator[LedgerEntry]:
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            rows = [ln for ln in fh if ln.strip()]
        if tail and limit:
            rows = rows[-limit:]
        emitted = 0
        for line in (rows if not tail or not limit else list(reversed(rows))[::-1]):
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            if tool and raw.get("tool") != tool:
                continue
            if only_failures and raw.get("ok"):
                continue
            try:
                entry = LedgerEntry(**{k: v for k, v in raw.items()
                                       if k in LedgerEntry.__dataclass_fields__})
            except TypeError:
                continue  # a record written by a newer/older build: skip, never explode
            yield entry
            emitted += 1
            if limit and not tail and emitted >= limit:
                return

    def verify(self) -> dict[str, Any]:
        """Walk the chain: returns {lines, valid, broken_at, orphans}."""
        out = {"lines": 0, "valid": True, "broken_at": None, "orphans": 0, "malformed": 0, "path": self.path}
        if not os.path.exists(self.path):
            out["empty"] = True
            return out
        prev = ""
        expected_seq = 0
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                out["lines"] += 1
                try:
                    raw = json.loads(line)
                except ValueError:
                    out.update(valid=False, broken_at={"line": lineno, "reason": "unparseable"})
                    return out
                expected_seq += 1
                if int(raw.get("seq", -1)) != expected_seq:
                    out["orphans"] += 1
                if raw.get("prev", "") != prev:
                    out.update(valid=False, broken_at={"line": lineno, "reason": "chain break"})
                    return out
                try:
                    entry = LedgerEntry(**{k: v for k, v in raw.items()
                                           if k in LedgerEntry.__dataclass_fields__})
                except TypeError:
                    out["malformed"] += 1
                    out.update(valid=False, broken_at={"line": lineno, "reason": "unreconstructable entry"})
                    return out
                body = {k: getattr(entry, k) for k in LedgerEntry.__dataclass_fields__ if k != "entry_sha"}
                if content_hash(body) != raw.get("entry_sha"):
                    out.update(valid=False, broken_at={"line": lineno, "reason": "digest mismatch"})
                    return out
                prev = raw["entry_sha"]
        return out

    def stats(self) -> dict[str, Any]:
        per_tool: dict[str, dict[str, Any]] = {}
        total_ms = 0
        calls = failures = mutations = 0
        for entry in self.read():
            calls += 1
            total_ms += entry.duration_ms
            if not entry.ok:
                failures += 1
            d = per_tool.setdefault(entry.tool, {"calls": 0, "failures": 0, "ms": 0, "risk": entry.risk})
            d["calls"] += 1
            d["ms"] += entry.duration_ms
            if not entry.ok:
                d["failures"] += 1
            if entry.risk in ("write", "destructive", "privileged"):
                mutations += 1
        return {"calls": calls, "failures": failures, "mutations": mutations,
                "total_ms": total_ms, "per_tool": per_tool,
                "success_rate": round((calls - failures) / calls, 3) if calls else None}


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None
