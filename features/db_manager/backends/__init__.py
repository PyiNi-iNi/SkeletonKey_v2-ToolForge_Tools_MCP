"""Vector store backends. ``make_store`` resolves the configured backend.

Selection order (env ``SK_DB_BACKEND``): ``auto`` probes faiss → chroma →
weaviate and falls back to ``sqlite`` (always available).
"""

from __future__ import annotations

import importlib.util

from features.common.config import FeatureConfig
from features.common.errors import BackendUnavailableError

from .base import SearchHit, VectorStore
from .memory import MemoryStore, SqliteStore

__all__ = [
    "SearchHit",
    "VectorStore",
    "MemoryStore",
    "SqliteStore",
    "make_store",
    "available_backends",
]

# backend name -> python package that must import
_PROBES: dict[str, str] = {
    "faiss": "faiss",
    "chroma": "chromadb",
    "weaviate": "weaviate",
    "sqlite": "sqlite3",
    "memory": "sqlite3",
}

_AUTO_ORDER = ["faiss", "chroma", "weaviate", "sqlite"]


def available_backends() -> list[str]:
    """Backends whose python package can be imported on this host."""
    out = []
    for name, package in _PROBES.items():
        if importlib.util.find_spec(package) is not None:
            out.append(name)
    return out


def make_store(
    backend: str | None = None,
    namespace: str = "default",
    path: str | None = None,
    dim: int = 384,
) -> VectorStore:
    cfg = FeatureConfig(prefix="SK_DB")
    name = (backend or cfg.get_str("backend") or "auto").lower()
    if name == "auto":
        for candidate in _AUTO_ORDER:
            if importlib.util.find_spec(_PROBES[candidate]) is not None:
                name = candidate
                break
    if name not in _PROBES:
        raise BackendUnavailableError(
            f"unknown vector backend {name!r}", hint=f"one of {sorted(set(_PROBES))} or auto"
        )
    if name == "memory":
        return MemoryStore(namespace=namespace, dim=dim)
    if name == "sqlite":
        return SqliteStore(namespace=namespace, path=path, dim=dim)
    if importlib.util.find_spec(_PROBES[name]) is None:
        raise BackendUnavailableError(
            f"{name} is not installed",
            hint=_install_hint(name),
        )
    if name == "faiss":
        from .faiss import FaissStore

        return FaissStore(namespace=namespace, path=path, dim=dim)
    if name == "chroma":
        from .chroma import ChromaStore

        return ChromaStore(namespace=namespace, path=path, dim=dim)
    from .weaviate import WeaviateStore

    return WeaviateStore(namespace=namespace, dim=dim)


def _install_hint(name: str) -> str:
    return {
        "faiss": 'pip install "faiss-cpu>=1.8" (or faiss-gpu)',
        "chroma": 'pip install "chromadb>=0.5"',
        "weaviate": 'pip install "weaviate-client>=4.0"',
    }.get(name, "see features/db_manager/requirements.txt")
