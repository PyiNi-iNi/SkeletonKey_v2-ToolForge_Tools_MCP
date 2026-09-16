"""Embedding engines.

``HuggingFaceEmbedder`` wraps sentence-transformers (MiniLM by default,
any Instructor model via ``SK_DB_EMBED_MODEL``) and is lazy-imported so
the core stays zero-dependency. ``HashingEmbedder`` is a deterministic,
collision-bucketed n-gram hashing encoder: not state-of-the-art, but
stable across runs/platforms and good enough to make the private DB
functional and testable without weights.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Any, Protocol

from features.common.config import FeatureConfig
from features.common.errors import BackendUnavailableError

__all__ = ["Embedder", "HashingEmbedder", "HuggingFaceEmbedder", "EmbeddingEngine"]

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Deterministic feature-hashing embedder (pure stdlib).

    Token + character n-grams are hashed into ``dim`` signed buckets with
    BLAKE2b; the result is L2-normalized. Deterministic for a given
    (text, dim, seed) on every platform — which is what private-DB
    persistence and tests rely on.
    """

    def __init__(self, dim: int = 384, seed: int = 0, char_ngram: int = 3, word_ngram: int = 1):
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim
        self.seed = seed
        self.char_ngram = char_ngram
        self.word_ngram = word_ngram
        self.name = f"hashing-{dim}"

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return _TOKEN_RE.findall(text.lower())

    def _features(self, text: str) -> list[str]:
        tokens = self._tokens(text)
        feats: list[str] = []
        for tok in tokens:
            feats.append(tok)
            for i in range(len(tok) - self.char_ngram + 1):
                feats.append("c:" + tok[i : i + self.char_ngram])
        for n in range(self.word_ngram, self.word_ngram + 1):
            for i in range(len(tokens) - n + 1):
                feats.append("w:" + " ".join(tokens[i : i + n]))
        return feats

    def _bucket(self, feat: str) -> tuple[int, int]:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8,
                                 key=str(self.seed).encode("utf-8")).digest()
        value = int.from_bytes(digest, "big")
        idx = value % self.dim
        sign = 1 if (value >> 63) & 1 == 0 else -1
        return idx, sign

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for feat in self._features(text):
                idx, sign = self._bucket(feat)
                vec[idx] += sign
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            out.append(vec)
        return out


class HuggingFaceEmbedder:
    """sentence-transformers wrapper (lazy import).

    Model default: ``sentence-transformers/all-MiniLM-L6-v2`` (384d).
    Set ``SK_DB_EMBED_MODEL`` to any sentence-transformer or instructor
    model id; the dim is read from the model if possible.
    """

    def __init__(self, model: str | None = None, device: str | None = None):
        cfg = FeatureConfig(prefix="SK_DB")
        self.model_name = model or cfg.get_str("embed_model") or "sentence-transformers/all-MiniLM-L6-v2"
        self.device = device or cfg.get_str("device")  # None -> framework default (GPU if present)
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "sentence-transformers is not installed",
                hint='pip install "sentence-transformers>=2.6"',
            ) from exc
        kwargs: dict[str, Any] = {}
        if self.device:
            kwargs["device"] = self.device
        self._model = SentenceTransformer(self.model_name, **kwargs)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.name = self.model_name

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [list(map(float, v)) for v in vectors]


class EmbeddingEngine:
    """Facade: sync ``encode`` + async ``aencode`` (offloaded to a thread
    so callers can stream/await without blocking the loop)."""

    def __init__(self, embedder: Embedder | None = None):
        if embedder is None:
            embedder = self._auto()
        self.embedder = embedder
        self.dim = embedder.dim
        self.name = embedder.name

    @staticmethod
    def _auto() -> Embedder:
        cfg = FeatureConfig(prefix="SK_DB")
        backend = (cfg.get_str("embed_backend") or "auto").lower()
        if backend in {"auto", "hf", "huggingface", "minilm", "instructor"}:
            try:
                return HuggingFaceEmbedder()
            except BackendUnavailableError:
                if backend == "auto":
                    return HashingEmbedder(dim=cfg.get_int("dim", 384))
                raise
        if backend in {"hash", "hashing", "none"}:
            return HashingEmbedder(dim=cfg.get_int("dim", 384))
        raise BackendUnavailableError(f"unknown embed backend {backend!r}",
                                      hint="use auto|hf|hash")

    def encode(self, texts: list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return []
        return self.embedder.embed(list(texts))

    async def aencode(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.encode, texts)

    def encode_one(self, text: str) -> list[float]:
        return self.encode([text])[0]
