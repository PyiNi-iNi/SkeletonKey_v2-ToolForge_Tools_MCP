"""DBManager — the private persistent database facade.

One DBManager == one physical store (file/directory). Namespaces are
logical collections inside it. Texts are embedded by the configured
``EmbeddingEngine``; queries accept raw texts or precomputed vectors.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from features.common.config import FeatureConfig
from features.common.errors import ValidationError

from .backends import make_store as _make_store
from .backends.base import SearchHit, VectorStore
from .backends.memory import SqliteStore
from .embeddings import EmbeddingEngine, HashingEmbedder, HuggingFaceEmbedder


def _embedder_for_dim(dim: int):
    """Real model when installed and dim-compatible, else hashing fallback."""
    try:
        hf = HuggingFaceEmbedder()
        if hf.dim == dim:
            return hf
    except Exception:  # noqa: BLE001 - BackendUnavailableError et al.
        pass
    return HashingEmbedder(dim=dim)


class Collection:
    """One namespace of a DBManager: add/query over texts or vectors."""

    def __init__(self, db: "DBManager", name: str):
        self._db = db
        self.name = name
        self._store: VectorStore | None = None

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = self._db._open_store(self.name)
        return self._store

    # -- sync ---------------------------------------------------------------

    def add(self, ids: list[str], vectors: list[list[float]],
            metadatas: list[dict[str, Any]] | None = None) -> int:
        self._db._check_dim(vectors)
        return self.store.add(ids, vectors, metadatas)

    def add_texts(self, texts: list[str], metadatas: list[dict[str, Any]] | None = None,
                  ids: list[str] | None = None) -> list[str]:
        if not texts:
            return []
        ids = list(ids) if ids else [uuid.uuid4().hex for _ in texts]
        if len(ids) != len(texts):
            raise ValidationError("ids and texts must align")
        vectors = self._db.encode(list(texts))
        self.store.add(ids, vectors, metadatas)
        return ids

    def query(self, embedding: list[float], n: int = 5,
              where: dict[str, Any] | None = None) -> list[SearchHit]:
        self._db._check_dim([embedding])
        return self.store.query([embedding], n=n, where=where)

    def query_texts(self, query: str, n: int = 5,
                    where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        hits = self.query(self._db.encode_one(query), n=n, where=where)
        out = []
        for hit in hits:
            item = hit.to_dict()
            item["metadata"] = {**hit.metadata}
            out.append(item)
        return out

    def delete(self, ids: list[str]) -> int:
        return self.store.delete(list(ids))

    def count(self) -> int:
        return self.store.count()

    def get(self, ids: list[str]) -> list[dict[str, Any]]:
        return self.store.get(list(ids))

    # -- async --------------------------------------------------------------

    async def aadd_texts(self, texts: list[str],
                         metadatas: list[dict[str, Any]] | None = None,
                         ids: list[str] | None = None) -> list[str]:
        return await asyncio.to_thread(self.add_texts, texts, metadatas, ids)

    async def aquery_texts(self, query: str, n: int = 5,
                           where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.query_texts, query, n, where)


class DBManager:
    """Private persistent DB: backend auto-selection + embedding engine."""

    def __init__(
        self,
        backend: str | None = None,
        path: str | None = None,
        dim: int | None = None,
        embedder: Any | None = None,
        engine: EmbeddingEngine | None = None,
    ):
        cfg = FeatureConfig(prefix="SK_DB")
        self._cfg_dim = dim or cfg.get_int("dim", 384)
        self._backend_name = (backend or cfg.get_str("backend") or "auto").lower()
        self.path = path or cfg.get_str("path")
        if engine is not None:
            self.engine = engine
        elif embedder is not None:
            self.engine = EmbeddingEngine(embedder)
        elif dim is None:
            self.engine = EmbeddingEngine()
        else:
            # explicit dim: prefer the real model, fall back to a matching
            # hashing embedder so the store is usable without weights.
            self.engine = EmbeddingEngine(_embedder_for_dim(dim))
        self._dim = self.engine.dim if dim is None else dim
        self._stores: dict[str, VectorStore] = {}
        self._closed = False

    def _open_store(self, namespace: str) -> VectorStore:
        if self._closed:
            raise ValidationError("DBManager is closed")
        store = self._stores.get(namespace)
        if store is None:
            store = _make_store(
                backend=self._backend_name,
                namespace=namespace,
                path=self.path,
                dim=self._dim,
            )
            self._stores[namespace] = store
        return store

    def _check_dim(self, vectors: list[list[float]]) -> None:
        for v in vectors[:1]:
            if len(v) != self._dim:
                raise ValidationError(
                    f"vector dim {len(v)} != configured dim {self._dim}",
                    hint="restart with a matching SK_DB_DIM or re-embed",
                )

    # -- public API ---------------------------------------------------------

    @property
    def backend(self) -> str:
        return self._backend_name

    @property
    def dim(self) -> int:
        return self._dim

    def collection(self, name: str = "default") -> Collection:
        return Collection(self, name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return self.engine.encode(texts)

    def encode_one(self, text: str) -> list[float]:
        return self.engine.encode_one(text)

    async def aencode(self, texts: list[str]) -> list[list[float]]:
        return await self.engine.aencode(texts)

    def namespaced(self, name: str) -> Collection:  # alias
        return self.collection(name)

    def namespaces(self) -> list[str]:
        store = next(iter(self._stores.values()), None)
        if store is None:
            self._open_store("__probe__")
            store = self._stores["__probe__"]
        if isinstance(store, SqliteStore):
            return store.namespaces()
        return list(self._stores.keys())

    def close(self) -> None:
        for store in self._stores.values():
            store.close()
        self._stores.clear()
        self._closed = True

    def __enter__(self) -> "DBManager":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def create_db(backend: str | None = None, path: str | None = None,
              dim: int | None = None) -> DBManager:
    """Convenience constructor honoring env (SK_DB_BACKEND, SK_DB_PATH...)."""
    return DBManager(backend=backend, path=path, dim=dim)
