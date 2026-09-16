"""db_manager — private, persistent vector database + embedding engine.

Backends (heaviest-first, auto-detected): FAISS, Chroma, Weaviate, SQLite
(stdlib, always available). The embedding engine prefers a real model
(Instructor / MiniLM via Hugging Face) and falls back to a deterministic
zero-dependency hashing embedder so the private DB is always usable.

See README.md for env vars and the REST surface.
"""

from __future__ import annotations

from .backends.base import SearchHit, VectorStore
from .backends.memory import MemoryStore, SqliteStore
from .embeddings import EmbeddingEngine, HashingEmbedder, HuggingFaceEmbedder
from .store import Collection, DBManager

__all__ = [
    "Collection",
    "DBManager",
    "EmbeddingEngine",
    "HashingEmbedder",
    "HuggingFaceEmbedder",
    "MemoryStore",
    "SearchHit",
    "SqliteStore",
    "VectorStore",
]
