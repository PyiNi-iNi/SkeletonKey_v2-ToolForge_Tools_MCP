# ADR-0014 — Features platform: isolation, lazy backends with mandatory fallbacks

- **Status:** accepted
- **Date:** 2026-09
- **Deciders:** platform work on branch `arena/01a0a8fa` (Arena Agent Mode)

## Context

The implementation mandate: a modular, scalable AI orchestration platform
with nine features — db_manager, document_processor, inference_engine,
ui_manager, vision_engine, multimedia_gen, agent_framework, api_proxy,
evaluator — each in its own folder with README, requirements, source, and
unit tests; OpenAI-compatible APIs; async-first with 80 tok/s streaming;
GPU (HF/vLLM) and CPU (GPT4ALL) dependency paths; Linux/macOS/Windows/
Docker; an evaluator with a 1000+ automated test suite.

The repo already lives by ADR-0001 (zero mandatory dependencies) and
ADR-0004 (schema-subset validation). A platform that `pip install`s
torch+diffusers+whisper+gradio as mandatory dependencies would violate the
spirit of the toolkit it is embedded in, and would make the test suite
impossible to run in CI without GPU runners.

## Decision

1. **Layout.** Features live at repo root as one importable package per
   feature: `features/<name>/` with `__init__.py`, `README.md`,
   `requirements.txt` (heavy optional deps only), source modules, and a
   `tests/` directory of integration tests. `features/__init__.py` keeps a
   machine-readable registry (`FEATURES`, `load_feature`, `feature_status`).
   A shared `features/common/` package (OpenAI schemas, SSE, streaming
   timing, env config, error taxonomy, asyncio HTTP server) is used by all
   features; it is support, not a feature.

2. **Zero-dependency cores, lazy heavy backends, mandatory fallbacks.**
   Every backend class lazy-imports its heavy dependency in `__init__`
   (never at module import time) and raises `BackendUnavailableError` with
   a `pip install` hint when missing. Every backend *category* ships a
   deterministic zero-dep fallback (hashing embedder / SQLite store,
   MockBackend, MockVision, mock image/stt/tts codecs, HeuristicReward,
   headless UI). `make_*_backend("auto")` probes heavy → fallback.
   Consequence: `python -m pytest features/` (1,400+ tests) passes on bare
   CPython. Real deployments install runtimes incrementally; the API does
   not change.

3. **OpenAI compatibility at the proxy.** `api_proxy` is the compliance
   surface: `/v1/chat/completions` (SSE + non-stream), `/v1/completions`,
   `/v1/embeddings`, `/v1/models`, standard objects and the
   `{"error": {...}}` envelope (with OpenAI `type` field via the shared
   error taxonomy). Other features reuse the same schema classes from
   `common.openai_compat` rather than re-inventing shapes. Function
   calling is a local execute→feedback loop over registered callables;
   JSON mode / strict `json_schema` enforcement uses the ADR-0004-style
   subset validator with corrective retry, then 422.

4. **Streaming contracts, async-first.** Every user-facing pipeline is an
   `AsyncIterator`: chat token streams (80 t/s target *measured* by
   `InferenceEngine.benchmark`, not assumed), image progress events per
   denoise step, STT partial transcripts growing monotonically, TTS
   header-then-PCM chunks. Heavy work runs on worker threads
   (`asyncio.to_thread` / dedicated threads + queues) so the event loop
   stays responsive — and the same code path serves Gradio, the HTTP
   layer, and tests.

5. **Private-by-default data plane.** The DB is local (SQLite file,
   `~/.skeletonkey/db`); REST surfaces bind to localhost and accept a
   bearer token (`SK_DB_TOKEN`, `SK_PROXY_TOKEN`); Weaviate/Chroma only
   point at local instances; voice samples and UI state live under
   `~/.skeletonkey`. Nothing phones home; provider base URLs are
   overridable for self-hosted inference.

6. **Sandbox semantics are documented, not hidden.**
   `agent_framework.PythonExecAgent` runs `sys.executable -I` with a
   minimal allowlisted env, temp cwd, and timeout-kill, and its
   README/ADR note say plainly: defense against accidental bad code, not a
   security boundary (hostile code needs a container — `skeletonkey/fsx`).

7. **The 1000+ suite is the evaluator's artifact, and also a pytest run.**
   `features/evaluator.generate_suite(n=1000, seed)` is deterministic and
   JSONL-serializable; the evaluator's own test file runs every generated
   case as a parameterized pytest test (1,037 tests in that file), so the
   "automated test suite" is both an evaluation corpus and the platform's
   regression proof.

## Rejected alternatives

- **Mandatory heavy deps per feature.** The platform becomes uninstallable
  in the environments ADR-0001 exists for; CI needs GPUs; the test suite
  stops being a fast gate.
- **One monolith package with submodules.** Loses the isolation the
  mandate asks for (per-feature requirements/README/tests) and makes
  partial installs impossible.
- **FastAPI/Gradio-only HTTP.** Third-party server dependencies in the
  hot path; a 200-line asyncio server gives us keep-alive, chunked SSE,
  and zero installs.
- **vLLM as the only inference path.** vLLM is great but is a serving
  runtime, not a library; the proxy can front one, but per-process
  transformers/llama.cpp/GPT4All backends keep CPU-only and small-model
  deployments first-class.

## Consequences

- (+) `pip install .` stays dependency-free; `features/` runs on bare
  CPython on all four target OSes.
- (+) The full 1,400+ test suite runs in ~7 seconds in CI without GPU.
- (+) Incremental capability: install `faiss-cpu` and the DB upgrades;
  install `transformers` and real models appear; nothing else changes.
- (+) OpenAI clients point at `api_proxy` and just work (wire-tested).
- (−) We own more small codecs (PNG, WAV, minimal xlsx/PDF readers) —
  each is tiny, stdlib-only, and doubles as a test oracle.
- (−) Fallback models are deterministic mocks, not intelligence: scores
  against `mock-1b` measure the *machinery*, and feature READMEs say so.
- (−) The code-execution sandbox is not a security boundary; documented.

## Test evidence

`python -m pytest features/ -q` → 1,403 passed (common 25, db_manager 36,
document_processor 37, inference_engine 28, ui_manager 31, vision_engine
23, multimedia_gen 53, agent_framework 34, api_proxy 99, evaluator 1037
including the 1000-case parameterized run), on CPython 3.11, no third-
party runtime installed beyond pytest/pytest-asyncio.
