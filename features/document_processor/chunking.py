"""Chunking: sentence splitting, semantic (similarity-based) and recursive.

``SemanticChunker`` embeds sentences and cuts where the similarity between
consecutive sentences drops — the standard "semantic chunking" idea
(LlamaIndex-style). With a learned embedder the default threshold works
well; with the zero-dep hashing embedder use ``threshold="auto"`` which
derives a data-driven breakpoint (mean - 0.5*std of the similarity trace).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from features.common.errors import ValidationError

__all__ = ["Chunk", "split_sentences", "SemanticChunker", "RecursiveChunker"]

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n+")


@dataclass
class Chunk:
    text: str
    index: int
    start: int  # char offset in source
    end: int
    doc_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def length(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "doc_id": self.doc_id,
            "metadata": self.metadata,
        }


def split_sentences(text: str) -> list[tuple[str, int, int]]:
    """Split into (sentence, start, end) keeping offsets into ``text``."""
    out: list[tuple[str, int, int]] = []
    pos = 0
    for match in _SENTENCE_RE.finditer(text):
        if match.start() > pos:
            piece = text[pos : match.start()]
            if piece.strip():
                out.append((piece, pos, match.start()))
        pos = match.end()
    tail = text[pos:]
    if tail.strip():
        out.append((tail, pos, len(text)))
    return out


class SemanticChunker:
    """Embedding-similarity chunker with size guardrails."""

    def __init__(
        self,
        embedder: Any,
        target_chunk_chars: int = 800,
        min_chunk_chars: int = 80,
        threshold: float | str = "auto",
        max_chunks: int = 256,
    ):
        if target_chunk_chars < min_chunk_chars:
            raise ValidationError("target_chunk_chars must be >= min_chunk_chars")
        self.embedder = embedder
        self.target_chunk_chars = target_chunk_chars
        self.min_chunk_chars = min_chunk_chars
        self.threshold = threshold
        self.max_chunks = max_chunks

    def _decide_threshold(self, sims: list[float]) -> float:
        if isinstance(self.threshold, (int, float)):
            return float(self.threshold)
        if len(sims) < 2:
            return 0.0
        mean = sum(sims) / len(sims)
        var = sum((s - mean) ** 2 for s in sims) / len(sims)
        std = var ** 0.5
        return mean - 0.5 * std

    def chunk(self, text: str, doc_id: str = "") -> list[Chunk]:
        sents = split_sentences(text)
        if not sents:
            return []
        if len(sents) == 1:
            (s, a, b) = sents[0]
            if len(s) <= self.target_chunk_chars:
                return [Chunk(text=s.strip(), index=0, start=a, end=b, doc_id=doc_id)]
            # one overlong "sentence": hard split by size
            out: list[Chunk] = []
            step = max(1, self.target_chunk_chars - 20)
            for i in range(0, len(s), step):
                piece = s[i : i + self.target_chunk_chars]
                if not piece.strip():
                    continue
                out.append(Chunk(text=piece, index=len(out), start=a + i,
                                 end=a + i + len(piece), doc_id=doc_id))
            return out
        vectors = self.embedder.embed([s for s, _, _ in sents])
        sims: list[float] = []
        for a, b in zip(vectors, vectors[1:]):
            dot = sum(x * y for x, y in zip(a, b))
            sims.append(dot)  # vectors are normalized -> cosine
        cutoff = self._decide_threshold(sims)

        # 1) candidate boundaries: low-similarity cuts
        boundaries = [0]
        for i, s in enumerate(sims):
            if s < cutoff:
                boundaries.append(i + 1)
        boundaries.append(len(sents))

        # 2) merge tiny chunks into neighbours; split overlong ones
        groups: list[list[int]] = []
        for start, end in zip(boundaries, boundaries[1:]):
            if groups and sum(len(sents[i][0]) for i in range(groups[-1][0], start)) < self.min_chunk_chars:
                groups[-1][1] = end
            else:
                groups.append([start, end])

        chunks: list[Chunk] = []
        for start, end in groups[: self.max_chunks]:
            parts = sents[start:end]
            # hard size cap: pack by target size
            buf: list[tuple[str, int, int]] = []
            buf_len = 0
            for s, a, b in parts:
                if buf and buf_len + len(s) > self.target_chunk_chars:
                    chunks.append(self._make(buf, doc_id, len(chunks)))
                    buf, buf_len = [], 0
                buf.append((s, a, b))
                buf_len += len(s)
            if buf:
                chunks.append(self._make(buf, doc_id, len(chunks)))
        # drop pure-whitespace chunks, reindex
        chunks = [c for c in chunks if c.text.strip()]
        for i, c in enumerate(chunks):
            c.index = i
        return chunks

    @staticmethod
    def _make(parts: list[tuple[str, int, int]], doc_id: str, index: int) -> Chunk:
        text = " ".join(s.strip() for s, _, _ in parts).strip()
        return Chunk(text=text, index=index, start=parts[0][1], end=parts[-1][2],
                     doc_id=doc_id)


class RecursiveChunker:
    """Classic recursive separator chunker (no embeddings needed)."""

    DEFAULT_SEPARATORS = ("\n\n", "\n", ". ", " ", "")

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 100,
                 separators: Sequence[str] | None = None):
        if chunk_overlap >= chunk_size:
            raise ValidationError("chunk_overlap must be < chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = list(separators) if separators else list(self.DEFAULT_SEPARATORS)

    def chunk(self, text: str, doc_id: str = "") -> list[Chunk]:
        chunks = self._split(text, 0)
        out: list[Chunk] = []
        offset = 0
        for piece in chunks:
            start = text.find(piece, offset)
            if start < 0:
                start = offset
            offset = start + 1
            out.append(Chunk(text=piece, index=len(out), start=start,
                             end=start + len(piece), doc_id=doc_id))
        return out

    def _split(self, text: str, sep_idx: int) -> list[str]:
        if len(text) <= self.chunk_size or sep_idx >= len(self.separators):
            return [text] if text.strip() else []
        sep = self.separators[sep_idx]
        if not sep:
            hard = self.chunk_size - self.chunk_overlap
            pieces = [text[i : i + hard] for i in range(0, len(text), hard)]
            return [p for p in pieces if p.strip()]
        parts = text.split(sep)
        out: list[str] = []
        buf = ""
        for part in parts:
            candidate = part if not buf else buf + sep + part
            if len(candidate) <= self.chunk_size:
                buf = candidate
            else:
                if buf.strip():
                    out.append(buf)
                if len(part) > self.chunk_size:
                    out.extend(self._split(part, sep_idx + 1))
                    buf = ""
                else:
                    buf = part
        if buf.strip():
            out.append(buf)
        return out
