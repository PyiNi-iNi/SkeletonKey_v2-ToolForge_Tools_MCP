"""Weaviate backend (requires a running Weaviate service).

Private-by-design: point ``SK_WEAVIATE_URL`` at a local instance
(``http://127.0.0.1:8080``) — the DB never phones home.
"""

from __future__ import annotations

import os
from typing import Any

from features.common.errors import UpstreamError

from .base import SearchHit, VectorStore


class WeaviateStore(VectorStore):
    name = "weaviate"

    def __init__(self, namespace: str = "default", dim: int = 384):
        super().__init__(namespace, dim)
        import weaviate  # type: ignore  (lazy by design)

        url = os.environ.get("SK_WEAVIATE_URL", "http://127.0.0.1:8080")
        token = os.environ.get("SK_WEAVIATE_TOKEN")
        self._client = weaviate.Client(url=url, auth_client_secret=token)
        self._name = namespace.replace("-", "_").replace("/", "_").capitalize() or "Default"
        if not self._client.collections.exists(self._name):
            import weaviate.classes as wvc  # type: ignore

            self._client.collections.create(
                name=self._name,
                vectorizer_config=None,  # we supply vectors
                object_vector_schema=wvc.Init.NestedVector(  # type: ignore[attr-defined]
                    "content", dim=dim, metric="cosine"
                ),
            )
        self._col = self._client.collections.get(self._name)

    def add(self, ids, embeddings, metadatas=None) -> int:
        metadatas = metadatas or [{} for _ in ids]
        for i, (vid, emb) in enumerate(zip(ids, embeddings)):
            meta = metadatas[i] if i < len(metadatas) else {}
            self._col.insert(uuid=vid, vector={"content": list(map(float, emb))},
                             properties={k: _plain(v) for k, v in meta.items()})
        return len(ids)

    def query(self, embeddings, n=5, where=None) -> list[SearchHit]:
        if not embeddings:
            return []
        kwargs: dict[str, Any] = {
            "query": list(map(float, embeddings[0])),
            "limit": max(1, n),
            "include_vector": False,
        }
        if where:
            from weaviate.util import filter_conversion  # type: ignore

            kwargs["filters"] = filter_conversion.convert_filters_to_weaviate(
                [(_wv_where(k, v) for k, v in where.items())])
        res = self._col.query.near_vector(**kwargs)
        hits = []
        for obj in res.objects:
            dist = obj.metadata.distance.to_cosine()
            hits.append(SearchHit(id=str(obj.uuid), score=float(dist),
                                  metadata=dict(obj.properties or {}),
                                  distance=float(obj.metadata.distance)))
        return hits

    def delete(self, ids) -> int:
        n = 0
        for vid in ids:
            res = self._col.delete_by_id(vid)
            n += 1 if res is None or res.status_code in (200, 204) else 0
        return n

    def count(self) -> int:
        return int(self._col.aggregate.over_all().count or 0)

    def get(self, ids) -> list[dict[str, Any]]:
        out = []
        for vid in ids:
            obj = self._col.fetch_object_by_id(vid, include_vector=False)
            if obj is not None:
                out.append({"id": str(obj.uuid), "metadata": dict(obj.properties or {}),
                            "embedding": None})
        return out

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass


def _wv_where(key: str, value: Any) -> dict[str, Any]:
    return {"path": [key], "operator": "Equal", "value": value}


def _plain(v: Any) -> Any:
    return v if isinstance(v, (str, int, float, bool)) else str(v)
