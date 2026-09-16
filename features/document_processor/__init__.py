"""document_processor — chunking, OCR, and ingestion for every doc format.

Pipeline: load (per-format extractor) → semantic chunk → (OCR for images)
→ embed → store into db_manager. All extraction has a zero-dependency
fallback (raw PDF text scan, stdlib zipfile/XML xlsx reader, regex code
chunker, passthrough OCR) so ingestion works on a bare install; heavy
parsers (pypdf, openpyxl/pandas, DocTR) plug in lazily when installed.
"""

from __future__ import annotations

from .chunking import (
    Chunk,
    RecursiveChunker,
    SemanticChunker,
    split_sentences,
)
from .ocr import DocTROCR, OCREngine, PassthroughOCR
from .pipeline import IngestResult, IngestionPipeline

__all__ = [
    "Chunk",
    "DocTROCR",
    "IngestResult",
    "IngestionPipeline",
    "OCREngine",
    "PassthroughOCR",
    "RecursiveChunker",
    "SemanticChunker",
    "split_sentences",
]
