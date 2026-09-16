# features/document_processor — chunking, OCR, ingestion

Turns PDF / Excel / CSV / code / text (and OCR'd images) into
embedding-ready chunks stored in `db_manager`.

## Architecture

```
IngestionPipeline (pipeline.py)      async, streaming progress events
├── extractors/                      bytes → ExtractedDoc (text + blocks)
│   ├── PDFExtractor                 pypdf (lazy) | raw Tj/TJ stream scanner
│   ├── ExcelExtractor               openpyxl (lazy) | stdlib zipfile+XML | csv
│   ├── CodeExtractor                tree-sitter (lazy) | regex symbol splitter
│   └── TextExtractor                text/markdown/json/notebooks
├── chunking.py
│   ├── SemanticChunker              similarity-breakpoint chunking
│   └── RecursiveChunker             separator fallback, no embeddings
└── ocr.py
    ├── DocTROCR                     docling (lazy)
    └── PassthroughOCR               zero-dep fallback (flags no-OCR)
```

Every heavy dependency is optional; the stdlib fallbacks keep ingestion
functional on a bare install (Linux/macOS/Windows/Docker).

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_DOC_NAMESPACE` | `documents` | db_manager namespace for ingested chunks |
| `SK_DOC_OCR` | `auto` | `auto` \| `doctr` \| `none` |

## Usage

```python
from features.db_manager import DBManager
from features.document_processor import IngestionPipeline

db = DBManager()
pipe = IngestionPipeline(db=db)

result = await pipe.aingest("paper.pdf")      # -> IngestResult
# live progress:
async for event in pipe.aprocess("paper.pdf"):
    print(event)   # {"stage": "extract"|"chunk"|"embed"|"store"|"done", "progress": ...}

hits = await pipe.aquery("quantum entanglement", n=5)
```

## Streaming / async

`aprocess` is an async generator of progress events (extract → chunk →
embed → store → done) so Gradio/web clients can render live ingestion
status. Embedding is thread-offloaded; the store write is awaitable.

## Tests

`tests/` — sentence splitting, semantic chunk boundaries on multi-topic
text, chunk size guards, recursive fallback, PDF (uncompressed +
Flate-compressed fixture), xlsx/csv/tsv (generated fixtures), code symbol
splitting, notebook extraction, pipeline event ordering, end-to-end
ingest→query round trip, OCR fallback behavior.
