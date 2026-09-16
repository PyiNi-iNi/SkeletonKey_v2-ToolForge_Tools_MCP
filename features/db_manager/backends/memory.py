"""Zero-dependency stores: in-memory and SQLite-persisted.

``MemoryStore`` is for tests and ephemeral use. ``SqliteStore`` is the
default persistent backend for the *private* DB: single local file,
stdlib only, cosine computed in Python (fine for private-DB scale;
swap in FAISS/Chroma via ``SK_DB_BACKEND`` when you need index speed).
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import threading
from typing import Any

from .base import SearchHit, VectorStore, cosine

__all__ = ["MemoryStore", "SqliteStore"]


class MemoryStore(VectorStore):
    name = "memory"

    def __init__(self, namespace: str = "default", dim: int = 384):
        super().__init__(namespace, dim)
        self._vectors: dict[str, list[float]] = {}
        self._metas: dict[str, dict[str, Any]] = {}

    def add(self, ids, embeddings, metadatas=None) -> int:
        if len(ids) != len(embeddings):
            raise ValueError("ids and embeddings must align")
        metadatas = metadatas or [{} for _ in ids]
        n = 0
        for i, (vid, emb) in enumerate(zip(ids, embeddings)):
            self._vectors[vid] = list(map(float, emb))
            self._metas[vid] = dict(metadatas[i] if i < len(metadatas) else {})
            n += 1
        return n

    def query(self, embeddings, n=5, where=None) -> list[SearchHit]:
        if not embeddings:
            return []
        emb = embeddings[0]
        scored = []
        for vid, vec in self._vectors.items():
            if where and not _matches(self._metas.get(vid, {}), where):
                continue
            scored.append((cosine(emb, vec), vid))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [SearchHit(id=vid, score=score, metadata=dict(self._metas.get(vid, {})))
                for score, vid in scored[: max(0, n)]]

    def delete(self, ids) -> int:
        n = 0
        for vid in ids:
            if vid in self._vectors:
                del self._vectors[vid]
                self._metas.pop(vid, None)
                n += 1
        return n

    def count(self) -> int:
        return len(self._vectors)

    def get(self, ids) -> list[dict[str, Any]]:
        out = []
        for vid in ids:
            if vid in self._vectors:
                out.append({"id": vid, "embedding": self._vectors[vid],
                            "metadata": dict(self._metas.get(vid, {}))})
        return out


class SqliteStore(VectorStore):
    """Persistent store; one table per namespace in one local file."""

    name = "sqlite"

    def __init__(self, namespace: str = "default", path: str | None = None, dim: int = 384):
        super().__init__(namespace, dim)
        cfg_path = path
        if cfg_path is None:
            cfg_path = os.environ.get("SK_DB_PATH") or os.path.join(
                os.path.expanduser("~"), ".skeletonkey", "db", "private.db"
            )
        os.makedirs(os.path.dirname(os.path.abspath(cfg_path)), exist_ok=True)
        self.path = cfg_path
        # check_same_thread=False + a lock: async callers fan out over the
        # thread-pool, and sqlite connections are per-thread objects.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._table = "ns_" + _safe_name(namespace)
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} ("
                "id TEXT PRIMARY KEY, emb BLOB NOT NULL, meta TEXT NOT NULL DEFAULT '{}')"
            )
            self._conn.commit()

    def _pack(self, vec: list[float]) -> bytes:
        return struct.pack(f"<{len(vec)}f", *vec)

    def _unpack(self, blob: bytes) -> list[float]:
        return list(struct.unpack(f"<{len(blob) // 4}f", blob))

    def add(self, ids, embeddings, metadatas=None) -> int:
        if len(ids) != len(embeddings):
            raise ValueError("ids and embeddings must align")
        metadatas = metadatas or [{} for _ in ids]
        rows = []
        for i, (vid, emb) in enumerate(zip(ids, embeddings)):
            meta = metadatas[i] if i < len(metadatas) else {}
            rows.append((vid, self._pack([float(x) for x in emb]), json.dumps(meta)))
        with self._lock:
            self._conn.executemany(
                f"INSERT OR REPLACE INTO {self._table} (id, emb, meta) VALUES (?, ?, ?)", rows
            )
            self._conn.commit()
        return len(rows)

    def query(self, embeddings, n=5, where=None) -> list[SearchHit]:
        if not embeddings:
            return []
        emb = embeddings[0]
        with self._lock:
            cur = self._conn.execute(f"SELECT id, emb, meta FROM {self._table}")
            rows = cur.fetchall()
        scored = []
        for vid, blob, meta_raw in rows:
            meta = json.loads(meta_raw or "{}")
            if where and not _matches(meta, where):
                continue
            scored.append((cosine(emb, self._unpack(blob)), vid, meta))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [SearchHit(id=vid, score=score, metadata=meta) for score, vid, meta in scored[: max(0, n)]]

    def delete(self, ids) -> int:
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        with self._lock:
            cur = self._conn.execute(f"DELETE FROM {self._table} WHERE id IN ({marks})", list(ids))
            self._conn.commit()
            return cur.rowcount

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute(f"SELECT COUNT(*) FROM {self._table}").fetchone()[0])

    def get(self, ids) -> list[dict[str, Any]]:
        out = []
        for vid in ids:
            with self._lock:
                row = self._conn.execute(
                    f"SELECT emb, meta FROM {self._table} WHERE id = ?", (vid,)
                ).fetchone()
            if row is not None:
                out.append({"id": vid, "embedding": self._unpack(row[0]),
                            "metadata": json.loads(row[1] or "{}")})
        return out

    def namespaces(self, conn: sqlite3.Connection | None = None) -> list[str]:
        with self._lock:
            rows = (conn or self._conn).execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ns_%'"
            ).fetchall()
        return [r[0][3:] for r in rows]

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.commit()
                self._conn.close()
        except sqlite3.Error:
            pass


def _safe_name(namespace: str) -> str:
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in namespace)
    return out or "default"


def _matches(meta: dict[str, Any], where: dict[str, Any]) -> bool:
    """Chroma-style ``where``: all keys must equal exactly."""
    for key, value in where.items():
        if meta.get(key) != value:
            return False
    return True
