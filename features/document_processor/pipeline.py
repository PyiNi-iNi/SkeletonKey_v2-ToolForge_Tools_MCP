"""Ingestion pipeline: extract → chunk → embed → store, with streaming
progress events so UIs can show live status on long documents."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from features.common.config import FeatureConfig
from features.common.errors import FeatureError, NotFoundError, ValidationError

from .chunking import Chunk, RecursiveChunker, SemanticChunker
from .extractors import extractor_for
from .ocr import PassthroughOCR, make_ocr

__all__ = ["IngestResult", "IngestionPipeline"]


@dataclass
class IngestResult:
    doc_id: str
    filename: str
    format: str
    n_chunks: int
    ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "filename": self.filename,
            "format": self.format,
            "n_chunks": self.n_chunks,
            "ids": self.ids,
            "metadata": self.metadata,
        }


class IngestionPipeline:
    """Wire extractors + chunkers + db_manager together.

    ``db`` is a ``features.db_manager.DBManager`` (optional — without it
    the pipeline yields chunks but does not store).
    """

    def __init__(self, db: Any | None = None, chunker: Any | None = None,
                 embedder: Any | None = None, ocr: Any | None = None,
                 chunk_namespace: str = "documents"):
        cfg = FeatureConfig(prefix="SK_DOC")
        self.db = db
        self.namespace = chunk_namespace or cfg.get_str("namespace", "documents")
        self.chunker = chunker
        self.embedder = embedder
        self.ocr = ocr if ocr is not None else (make_ocr() if cfg.get_str("ocr") != "none" else PassthroughOCR())

    # -- sync ---------------------------------------------------------------

    def load(self, path: str) -> tuple[str, list[Chunk], dict[str, Any]]:
        """Read + extract + chunk one file -> (doc_id, chunks, metadata)."""
        if not os.path.exists(path):
            raise NotFoundError(f"file not found: {path}")
        filename = os.path.basename(path)
        with open(path, "rb") as fh:
            data = fh.read()
        doc_id = hashlib.sha256(data).hexdigest()[:16]
        extracted = extractor_for(filename).extract(data, filename)
        chunks = self._chunk(extracted.text, doc_id)
        for i, ch in enumerate(chunks):
            ch.metadata = {**extracted.metadata, **ch.metadata,
                           "block_index": i, "source": filename}
        return doc_id, chunks, extracted.metadata

    def _chunk(self, text: str, doc_id: str) -> list[Chunk]:
        if self.chunker is not None:
            return self.chunker.chunk(text, doc_id=doc_id)
        if self.db is not None:
            embedder = self.embedder or self.db.engine
            try:
                return SemanticChunker(embedder=embedder).chunk(text, doc_id=doc_id)
            except Exception:  # noqa: BLE001 - fall back to recursive
                return RecursiveChunker().chunk(text, doc_id=doc_id)
        return RecursiveChunker().chunk(text, doc_id=doc_id)

    # -- async / streaming ----------------------------------------------------

    async def aingest(self, path: str, on_progress: Any = None) -> IngestResult:
        async for event in self.aprocess(path, on_progress):
            pass
        return self._last_result  # type: ignore[has-type]

    async def aprocess(self, path: str, on_progress: Any = None) -> AsyncIterator[dict[str, Any]]:
        """Stream progress events: extract -> chunk -> embed -> store."""
        filename = os.path.basename(path)
        if not os.path.exists(path):
            raise NotFoundError(f"file not found: {path}")
        with open(path, "rb") as fh:
            data = fh.read()
        doc_id = hashlib.sha256(data).hexdigest()[:16]

        yield _ev("extract", 0.1, filename=filename)
        extracted = extractor_for(filename).extract(data, filename)
        yield _ev("extract", 0.25, blocks=len(extracted.blocks))

        yield _ev("chunk", 0.4, text_len=len(extracted.text))
        chunks = self._chunk(extracted.text, doc_id)
        for i, ch in enumerate(chunks):
            ch.metadata = {**extracted.metadata, **ch.metadata,
                           "block_index": i, "source": filename}
        yield _ev("chunk", 0.55, n_chunks=len(chunks))

        if self.db is None:
            result = IngestResult(doc_id=doc_id, filename=filename,
                                  format=str(extracted.metadata.get("kind", "text")),
                                  n_chunks=len(chunks),
                                  metadata=extracted.metadata)
            self._last_result = result
            yield _ev("done", 1.0, result=result.to_dict())
            return

        yield _ev("embed", 0.7, n=len(chunks))
        vectors = await self.db.aencode([c.text for c in chunks])
        col = self.db.collection(self.namespace)
        ids = [uuid.uuid4().hex for _ in chunks]
        metas = [{**c.metadata, "doc_id": doc_id} for c in chunks]
        await asyncio.to_thread(col.add, ids, vectors, metas)
        yield _ev("store", 0.95, n_stored=len(ids))

        result = IngestResult(doc_id=doc_id, filename=filename,
                              format=str(extracted.metadata.get("kind", "text")),
                              n_chunks=len(chunks), ids=ids,
                              metadata=extracted.metadata)
        self._last_result = result
        yield _ev("done", 1.0, result=result.to_dict())
        if on_progress is not None:
            await on_progress(result)

    async def aquery(self, query: str, n: int = 5) -> list[dict[str, Any]]:
        if self.db is None:
            raise ValidationError("pipeline has no db attached")
        return await self.db.collection(self.namespace).aquery_texts(query, n=n)


def _ev(stage: str, progress: float, **kw: Any) -> dict[str, Any]:
    return {"stage": stage, "progress": progress, **kw}
