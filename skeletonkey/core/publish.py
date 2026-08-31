"""Publishing subsystem: a write-only credential store and a placeholder engine.

Design (ADR-0010):

* The store is a JSON file *outside the workspace roots* (user-level config dir
  by default). The fs sandbox is a hard wall, so ``fs.*`` tools cannot read or
  write it; only this module does.
* The store is **write-only to the agent**: no method of this class is
  advertised as a tool that returns raw values. ``meta()`` masks values. The
  only path a stored value takes out of the process is
  :meth:`PublishEngine.inject`, which writes it straight into a workspace file
  through the journaled fs layer.
* Values are plaintext on disk, protected by file permissions (``0600``,
  best-effort) and location. There is deliberately no keyring dependency
  (zero-mandatory-deps rule); the trade-off is stated in SECURITY-MODEL.

Pure standard library so the core-constraint job keeps passing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import E, SkeletonKeyError

STORE_FORMAT_VERSION = 1

#: ids: lowercase, start alphanumeric, then alnum plus ``. _ -``; max 64 chars
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: kinds the store accepts. ``other`` is the escape hatch so the set stays
#: open without being free-form.
KINDS = (
    "token", "api_key", "client_id", "client_secret", "oauth_token", "password",
    "email", "phone", "two_factor", "social_account", "signing_key",
    "certificate", "webhook", "other",
)

#: ``{{PUB.<id>}}`` — the marker grammar. The id grammar is the store's.
MARKER_RE = re.compile(r"\{\{PUB\.([a-z0-9][a-z0-9._-]{0,63})\}\}")


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mask(value: str) -> str:
    """A short, stable, non-inverting fingerprint for metadata listings."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}…{value[-2:]}({len(value)})"


def validate_id(id_: str) -> str:
    if not isinstance(id_, str) or not ID_RE.match(id_):
        raise SkeletonKeyError(E.BAD_ARGS, f"invalid store id {id_!r}",
                               details={"pattern": ID_RE.pattern})
    if ".." in id_ or "{{" in id_ or "}}" in id_:
        raise SkeletonKeyError(E.BAD_ARGS, f"store id {id_!r} is not allowed to "
                                           "contain '..' or brace sequences")
    return id_


def validate_kind(kind: str) -> str:
    if kind not in KINDS:
        raise SkeletonKeyError(E.BAD_ARGS, f"unknown kind {kind!r}",
                               details={"kinds": list(KINDS)})
    return kind


class PublishStore:
    """JSON-backed credential store. Write-only in spirit: see module docstring."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = {"version": STORE_FORMAT_VERSION, "entries": {}}
        self.load()

    # -- persistence -------------------------------------------------------

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SkeletonKeyError(E.IO,
                                   f"publish store unreadable: {self.path}",
                                   details={"reason": str(exc)}) from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("entries"), dict):
            raise SkeletonKeyError(E.IO,
                                   f"publish store has wrong shape: {self.path}")
        self._data = raw

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600, best effort
        except OSError:
            pass
        os.replace(tmp, self.path)

    # -- entry API ----------------------------------------------------------

    @property
    def _entries(self) -> dict[str, Any]:
        return self._data["entries"]

    def put(self, id_: str, kind: str, value: str, note: str = "") -> dict[str, Any]:
        """Insert or update an entry. Returns metadata only — never the value."""
        validate_id(id_)
        validate_kind(kind)
        if not isinstance(value, str) or not value:
            raise SkeletonKeyError(E.BAD_ARGS, "store value must be a non-empty string")
        now = _utcnow()
        existing = self._entries.get(id_)
        entry = {
            "kind": kind,
            "note": note or "",
            "value": value,
            "created": (existing or {}).get("created", now),
            "updated": now,
        }
        self._entries[id_] = entry
        self._save()
        return self.meta(id_)

    def meta(self, id_: str) -> dict[str, Any]:
        """Metadata for one entry. The value appears only as a short mask."""
        validate_id(id_)
        entry = self._entries.get(id_)
        if entry is None:
            raise SkeletonKeyError(E.ENOENT, f"no store entry {id_!r}",
                                   details={"ids": self.ids()})
        return {
            "id": id_,
            "kind": entry.get("kind", "other"),
            "note": entry.get("note", ""),
            "created": entry.get("created", ""),
            "updated": entry.get("updated", ""),
            "value_masked": _mask(entry.get("value", "")),
        }

    def metas(self, kind: str = "") -> list[dict[str, Any]]:
        out = []
        for id_ in sorted(self._entries):
            if kind and self._entries[id_].get("kind") != kind:
                continue
            out.append(self.meta(id_))
        return out

    def ids(self) -> list[str]:
        return sorted(self._entries)

    def has(self, id_: str) -> bool:
        return id_ in self._entries

    def value(self, id_: str) -> str:
        """Raw value. Internal use only — never exposed by a tool."""
        validate_id(id_)
        entry = self._entries.get(id_)
        if entry is None:
            raise SkeletonKeyError(E.ENOENT, f"no store entry {id_!r}",
                                   details={"ids": self.ids()})
        return str(entry.get("value", ""))

    def delete(self, id_: str) -> dict[str, Any]:
        """Delete an entry. Destructive and *irreversible* (store is outside
        the workspace journal)."""
        validate_id(id_)
        entry = self._entries.pop(id_, None)
        if entry is None:
            raise SkeletonKeyError(E.ENOENT, f"no store entry {id_!r}",
                                   details={"ids": self.ids()})
        self._save()
        return {"id": id_, "deleted": True,
                "note": "irreversible: the store is outside the workspace journal"}

    def fingerprint(self) -> str:
        """Content hash over the *metadata* (no values) — for cache keys/tests."""
        rows = [{"id": i, "kind": self._entries[i].get("kind"),
                 "updated": self._entries[i].get("updated")}
                for i in sorted(self._entries)]
        return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Placeholder engine
# ---------------------------------------------------------------------------

@dataclass
class Marker:
    """One ``{{PUB.<id>}}`` occurrence, located exactly."""
    file: str        # display path (workspace-relative)
    line: int        # 1-based
    column: int      # 1-based
    id: str          # the store id the marker refers to
    bound: bool      # store has an entry for the id
    marker: str = field(repr=False, default="")  # the literal text

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "column": self.column,
                "id": self.id, "bound": self.bound,
                "status": "bound" if self.bound else "missing"}


def find_markers_in_text(text: str, file: str, store: PublishStore | None) -> list[Marker]:
    markers: list[Marker] = []
    for line_no, line in enumerate(text.split("\n"), start=1):
        for m in MARKER_RE.finditer(line):
            id_ = m.group(1)
            markers.append(Marker(
                file=file, line=line_no, column=m.start() + 1, id=id_,
                bound=(store.has(id_) if store is not None else False),
                marker=m.group(0)))
    return markers


def replace_markers(text: str, store: PublishStore,
                    bindings: dict[str, str] | None = None, file: str = "") -> tuple[str, list[Marker], list[str]]:
    """Replace every marker in ``text``.

    Returns ``(new_text, markers, missing_ids)``. ``missing_ids`` lists (sorted,
    unique) store ids that a marker refers to but the store does not have —
    callers must refuse to write when it is non-empty. ``bindings`` maps a
    marker id to a *different* store id for the lookup ("maps to stored
    credentials"). ``file`` is echoed into the marker locations for reporting.
    """
    missing: set[str] = set()

    def _sub(m: re.Match[str]) -> str:
        id_ = m.group(1)
        source = (bindings or {}).get(id_, id_)
        if not store.has(source):
            missing.add(id_)
            return m.group(0)  # leave untouched; caller decides to abort
        return store.value(source)

    new_text = MARKER_RE.sub(_sub, text)
    markers = find_markers_in_text(text, file, store)
    return new_text, markers, sorted(missing)
