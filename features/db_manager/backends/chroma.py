"""Chroma backend — uses Chroma's own client and default local embedding
when available; we supply our vectors explicitly so the DB's embedding
engine stays in charge (SK_DB_EMBED_*).
"""

from __future__ import annotations

from typing import Any

from .base import SearchHit, VectorStore


class ChromaStore(VectorStore):
    name = "chroma"

    def __init__(self, namespace: str = "default", path: str | None = None, dim: int = 384):
        super().__init__(namespace, dim)
        import chromadb  # type: ignore  (lazy by design)

        self._client = chromadb.PersistentClient(path=path or os_default())
        self._col = self._client.get_or_create_collection(
            name=namespace.replace("/", "_")[: 100] or "default",
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, ids, embeddings, metadatas=None) -> int:
        metadatas = metadatas or [{} for _ in ids]
        self._col.upsert(
            ids=list(ids),
            embeddings=[list(map(float, e)) for e in embeddings],
            metadatas=[_jsonable(m) if i < len(metadatas) else {} for i, m in enumerate(metadatas)],
        )
        return len(ids)

    def query(self, embeddings, n=5, where=None) -> list[SearchHit]:
        if not embeddings:
            return []
        res = self._col.query(
            query_embeddings=[list(map(float, embeddings[0]))],
            n_results=max(1, n),
            where=where or None,
            include=["metadatas", "distances"],
        )
        hits = []
        ids = res.get("ids", [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, vid in enumerate(ids):
            dist = float(dists[i]) if i < len(dists) else 1.0
            hits.append(SearchHit(
                id=vid,
                score=1.0 - dist,  # cosine distance -> similarity
                metadata=dict(metas[i]) if i < len(metas) and metas[i] else {},
                distance=dist,
            ))
        return hits

    def delete(self, ids) -> int:
        if not ids:
            return 0
        before = self.count()
        self._col.delete(ids=list(ids))
        return before - self.count()

    def count(self) -> int:
        return int(self._col.count())

    def get(self, ids) -> list[dict[str, Any]]:
        res = self._col.get(ids=list(ids), include=["metadatas"])
        out = []
        for i, vid in enumerate(res.get("ids", [])):
            metas = res.get("metadatas") or []
            out.append({"id": vid, "metadata": dict(metas[i]) if i < len(metas) and metas[i] else {},
                        "embedding": None})
        return out

    def close(self) -> None:
        return None


def os_default() -> str:
    import os

    return os.environ.get("SK_DB_PATH") or os.path.join(os.path.expanduser("~"), ".skeletonkey", "db")


def _jsonable(meta: dict[str, Any]) -> dict[str, Any]:
    return {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in meta.items()}
