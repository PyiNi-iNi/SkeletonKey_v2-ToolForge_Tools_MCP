"""FAISS backend (exact flat index; swap in IVF/HNSW for >100k vectors).

Metadata and ids are kept in a sidecar JSON file next to the FAISS index
so the store stays self-contained and private.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .base import SearchHit, VectorStore, cosine


class FaissStore(VectorStore):
    name = "faiss"

    def __init__(self, namespace: str = "default", path: str | None = None, dim: int = 384):
        super().__init__(namespace, dim)
        import faiss  # type: ignore  (lazy by design)

        self._faiss = faiss
        base = path or os.environ.get("SK_DB_PATH") or os.path.join(
            os.path.expanduser("~"), ".skeletonkey", "db"
        )
        os.makedirs(base, exist_ok=True)
        self.index_path = os.path.join(base, f"{_safe(namespace)}.faiss")
        self.meta_path = os.path.join(base, f"{_safe(namespace)}.meta.json")
        self._index = faiss.IndexFlatIP(dim)  # inner product == cosine on normalized vectors
        if os.path.exists(self.index_path):
            faiss.read_index(self.index_path)
            self._index = faiss.read_index(self.index_path)
        self._records: dict[str, dict[str, Any]] = {}
        if os.path.exists(self.meta_path):
            with open(self.meta_path, encoding="utf-8") as fh:
                self._records = json.load(fh)

    def _vectors(self, embeddings: list[list[float]]) -> Any:
        import numpy as np  # noqa: PLC0415 - faiss requires numpy

        return np.asarray([_normalize(list(map(float, v))) for v in embeddings], dtype="float32")

    def add(self, ids, embeddings, metadatas=None) -> int:
        metadatas = metadatas or [{} for _ in ids]
        self._index.add(self._vectors(embeddings))
        start = len(self._records)
        for i, (vid, meta) in enumerate(zip(ids, metadatas)):
            self._records[vid] = meta if i < len(metadatas) else {}
        self._persist()
        return len(ids) - max(0, start - len(ids))

    def query(self, embeddings, n=5, where=None) -> list[SearchHit]:
        if not embeddings or not self._records:
            return []
        k = min(max(1, n * 4), self._index.ntotal)
        scores, idxs = self._index.search(self._vectors(embeddings), k)
        hits: list[SearchHit] = []
        ids = list(self._records.keys())
        for score, j in zip(scores[0], idxs[0]):
            if j < 0:
                continue
            vid = ids[int(j)]
            if where and not _where_match(self._records.get(vid, {}), where):
                continue
            hits.append(SearchHit(id=vid, score=float(score),
                                  metadata=dict(self._records.get(vid, {}))))
            if len(hits) >= n:
                break
        return hits

    def delete(self, ids) -> int:
        # FlatIndex supports single-id removal; loop for small batches.
        n = 0
        target = {vid for vid in ids}
        for vid in list(self._records):
            if vid in target:
                self._index.remove_ids(self._faiss.IDSelector1(
                    list(self._records.keys()).index(vid)))
                del self._records[vid]
                n += 1
        if n:
            self._persist()
        return n

    def count(self) -> int:
        return int(self._index.ntotal)

    def get(self, ids) -> list[dict[str, Any]]:
        return [
            {"id": vid, "metadata": dict(self._records[vid]), "embedding": None}
            for vid in ids
            if vid in self._records
        ]

    def _persist(self) -> None:
        self._faiss.write_index(self._index, self.index_path)
        with open(self.meta_path, "w", encoding="utf-8") as fh:
            json.dump(self._records, fh)

    def close(self) -> None:
        self._persist()


def _safe(namespace: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in namespace) or "default"


def _normalize(v: list[float]) -> list[float]:
    import math  # noqa: PLC0415

    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def _where_match(meta: dict[str, Any], where: dict[str, Any]) -> bool:
    return all(meta.get(k) == v for k, v in where.items())
