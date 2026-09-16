"""Vector store contract shared by every backend."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

__all__ = ["SearchHit", "VectorStore", "cosine"]


@dataclass
class SearchHit:
    id: str
    score: float  # cosine similarity in [-1, 1]
    metadata: dict[str, Any] = field(default_factory=dict)
    distance: float | None = None  # raw backend distance when relevant

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "score": round(self.score, 6),
            "metadata": self.metadata,
            "distance": self.distance,
        }


def cosine(a: list[float] | tuple[float, ...], b: list[float] | tuple[float, ...]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class VectorStore(ABC):
    """One store instance == one namespace (collection).

    Score semantics are cosine similarity (higher = closer) across all
    backends, so DBManager consumers are backend-agnostic.
    """

    name: str = "abstract"

    def __init__(self, namespace: str = "default", dim: int = 384):
        self.namespace = namespace
        self.dim = dim

    @abstractmethod
    def add(self, ids: list[str], embeddings: list[list[float]],
            metadatas: list[dict[str, Any]] | None = None) -> int: ...

    @abstractmethod
    def query(self, embeddings: list[list[float]], n: int = 5,
              where: dict[str, Any] | None = None) -> list[SearchHit]: ...

    @abstractmethod
    def delete(self, ids: list[str]) -> int: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def get(self, ids: list[str]) -> list[dict[str, Any]]: ...

    def close(self) -> None:  # optional for in-memory backends
        return None

    def __enter__(self) -> "VectorStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
