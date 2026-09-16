# features/db_manager — private persistent vector DB + embedding engine

Private (local-only) vector database for the platform: chunk store, RAG
memory, HYDE targets, voice-clone speaker embeddings, eval corpus. No
data ever leaves the machine — the optional REST server binds to
localhost and accepts a bearer token.

## Architecture

```
DBManager (store.py)                 facade: namespaces + embedding engine
├── EmbeddingEngine (embeddings.py)
│   ├── HuggingFaceEmbedder          sentence-transformers / instructor (lazy)
│   └── HashingEmbedder              deterministic n-gram hashing, zero-dep fallback
└── VectorStore (backends/)
    ├── FaissStore        faiss-cpu/gpu (lazy)      exact flat index + JSON sidecar
    ├── ChromaStore       chromadb (lazy)           persistent local collections
    ├── WeaviateStore     weaviate-client (lazy)    local Weaviate service only
    ├── SqliteStore       stdlib sqlite3            DEFAULT persistent backend
    └── MemoryStore       in-process                tests / ephemeral
```

Backend auto-selection (`SK_DB_BACKEND=auto`): faiss → chroma → weaviate →
sqlite (first importable wins; sqlite always wins on a bare install).

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_DB_BACKEND` | `auto` | `auto` \| `faiss` \| `chroma` \| `weaviate` \| `sqlite` \| `memory` |
| `SK_DB_PATH` | `~/.skeletonkey/db` | sqlite/faiss/chroma persistence root |
| `SK_DB_DIM` | `384` | embedding dimension (must match the embedder) |
| `SK_DB_EMBED_BACKEND` | `auto` | `auto` \| `hf` \| `hash` |
| `SK_DB_EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | any sentence-transformers / instructor model |
| `SK_DB_DEVICE` | *(framework default)* | e.g. `cuda`, `cpu` |
| `SK_DB_HOST` / `SK_DB_PORT` | `127.0.0.1` / `8770` | REST server bind |
| `SK_DB_TOKEN` | *(unset → open on loopback)* | required bearer token when set |

## Usage

```python
from features.db_manager import DBManager

db = DBManager()                                   # auto backend, MiniLM or hash fallback
col = db.collection("notes")
col.add_texts(["Chroma is a vector DB", "FAISS is a library"],
              metadatas=[{"src": "docs"}, {"src": "docs"}])
hits = col.query_texts("vector database", n=2)     # [{id, score, metadata, distance}]

await db.collection("notes").aadd_texts([...])     # async ingestion (thread-offloaded)
```

## REST surface (optional)

```
POST /v1/ingest     {"namespace", "texts", "metadatas"?, "ids"?}
POST /v1/query      {"namespace", "query", "n"?, "where"?}
POST /v1/query_vec  {"namespace", "embedding", "n"?, "where"?}
GET  /v1/namespaces
GET  /health
```

## Streaming / async

Ingestion and query are async (`aadd_texts`, `aquery_texts`) — embedding
runs off-loop via `asyncio.to_thread`, so a Gradio/UI event loop keeps
responsiveness while documents stream in. The REST layer serves through
the shared `features.common.http` server (chunked-capable).

## Dependencies

Core: none. Optional (see `requirements.txt`): `faiss-cpu` (GPU hosts:
`faiss-gpu`), `chromadb`, `weaviate-client`, `sentence-transformers`
(GPU: install `torch` with CUDA; CPU: `--index-url https://download.pytorch.org/whl/cpu`).
Linux, macOS, Windows, Docker all supported; paths use `os.path`/`~` only.

## Tests

`tests/` — embedder determinism + ranking sanity, memory/sqlite round
trips, persistence across reopen, `where` filtering, backend selection,
async paths, and a live HTTP ingest→query round trip with token auth.
