# features/ — the modular AI orchestration platform

Nine isolated features on a shared zero-dependency core. Every feature:
its own `README.md`, `requirements.txt` (heavy, optional deps only),
source, and `tests/`. Import path: `features.<name>`.

**The invariant (ADR-0001 extended, ADR-0014):** every feature core is
pure stdlib. Heavy backends (torch, diffusers, whisper, transformers,
gradio, faiss, chromadb, llama.cpp, ...) are lazy-imported, and each one
ships a **zero-dependency fallback** — so the whole platform installs,
runs, and passes its full test suite on a bare CPython on Linux, macOS,
Windows, and Docker.

## Feature map

| Feature | Purpose | Heavy backends (lazy) | Zero-dep fallback |
| --- | --- | --- | --- |
| `common/` | OpenAI wire schemas, SSE, streaming timing (80 t/s), env config, error taxonomy, asyncio HTTP server | — | — (this is the core) |
| `db_manager/` | Private persistent vector DB + embedding engine | FAISS, Chroma, Weaviate, sentence-transformers/Instructor/MiniLM | SQLite + deterministic hashing embedder |
| `document_processor/` | Semantic chunking, OCR, PDF/Excel/Code ingestion pipelines | pypdf, openpyxl, DocTR (docling) | raw PDF text scan, stdlib zipfile/XML xlsx, regex code splitter, passthrough OCR |
| `inference_engine/` | LLM backends (LLaMa-2/Mistral/Falcon), Attention Sinks, HYDE, 80 t/s benchmark | transformers, AutoGPTQ, llama.cpp, GPT4All | deterministic MockBackend |
| `ui_manager/` | Gradio UI, bake-off mode, state preservation, native + OAuth device auth | Gradio | headless console UI + HTTP auth server |
| `vision_engine/` | LLaVa / Claude-3 / Gemini-Pro / GPT-4 vision | transformers (LLaVA) | MockVision (providers are key-only stdlib HTTP) |
| `multimedia_gen/` | `image_gen/` (SD/Flux/Playground), `stt/` (Whisper streaming), `tts/` (Speech T5 + voice cloning) | diffusers, faster-whisper, transformers | pure-Python PNG mock, token mock, per-word tone-speech WAV |
| `agent_framework/` | AutoGen-style conversations, sandboxed code execution, Python/CSV agents, web search | pandas, autogen (optional) | isolated `python -I` subprocess, stdlib csv, keyless DDG HTML |
| `api_proxy/` | OpenAI-compatible server: chat/completions/embeddings/models, function calling, JSON mode, strict schema enforcement | vLLM/Outlines (GPU-side) | built-in JSON-Schema subset validator |
| `evaluator/` | Reward-model evaluation + the **1000-case automated suite** | transformers reward models | HeuristicReward (6 dimensions) |

## Layout

```
features/
├── README.md                  <- you are here
├── __init__.py                <- feature registry (FEATURES, load_feature)
├── conftest.py                <- puts repo root on sys.path for tests
├── common/                    <- shared support package (not a feature)
├── db_manager/                ├── README.md
│   ├── requirements.txt       ├── <source modules>
│   └── tests/                 └── feature-local integration tests
├── ... (8 more feature dirs, same shape)
```

## How features compose

```
api_proxy ──> inference_engine (models) ──> db_manager (embeddings)
    │                                              ▲
    └──> db_manager (RAG store) <── document_processor (ingestion)
                                         │
ui_manager ──> inference_engine (bake-off, chat)   │
             └──> db_manager (state lives in ~/.skeletonkey)
multimedia_gen ──> independent (streams out)
agent_framework ──> inference_engine (models) + stdlib sandbox
evaluator ──> inference_engine (candidate) + api_proxy (schema checks)
```

## Running everything

```bash
# zero-dependency: the ENTIRE platform test suite (1400+ tests)
python -m pytest features/ -q

# one feature
python -m pytest features/db_manager -q

# the evaluator CLI (1000-case suite against any registered model)
python -m features.evaluator --suite 1000 --model mock-1b --report report.json
```

### Going real (per feature)

Install the extras from a feature's `requirements.txt`:

```bash
pip install -r features/db_manager/requirements.txt        # e.g. faiss-cpu
pip install -r features/inference_engine/requirements.txt  # transformers+torch
```

Then point the env vars at your resources — every feature reads
`SK_<FEATURE>_*` (documented in each feature README). Backend selection is
`auto` everywhere: heavy backend if importable, fallback otherwise, so
you can install one heavy runtime at a time and the platform composes.

## Streaming targets

- **Chat:** 80 tokens/sec target — measured per model by
  `InferenceEngine.benchmark()` (reports `meets_target`); the mock backend
  runs orders of magnitude faster.
- **Audio (STT/TTS):** chunked partial transcripts; WAV header-then-PCM
  chunk streams.
- **Image:** progress events per denoising step + final image event.

## OpenAI compatibility

`api_proxy` is the compliance surface: `/v1/chat/completions`
(stream + non-stream), `/v1/completions`, `/v1/embeddings`, `/v1/models`,
standard `chat.completion(.chunk)` / `text_completion` objects and
`{"error": {...}}` envelope — verified by wire tests. `db_manager` and
`ui_manager` also expose OpenAI-shaped JSON where they serve HTTP.

## Decisions

See `docs/adr/0014-features-architecture.md` (feature isolation, lazy
backends + mandatory fallbacks, streaming contracts, schema-subset
enforcement, private-by-default data plane).

## Test inventory

1,400+ tests across the platform:

| Area | Tests |
| --- | ---: |
| common | 25 |
| db_manager | 36 |
| document_processor | 37 |
| inference_engine | 28 |
| ui_manager | 31 |
| vision_engine | 23 |
| multimedia_gen (image_gen + stt + tts) | 53 |
| agent_framework | 34 |
| api_proxy | 99 |
| evaluator (incl. the 1000-case parameterized run) | 1037 |
