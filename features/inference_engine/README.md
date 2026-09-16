# features/inference_engine — model backends, Attention Sinks, HYDE

Streaming-first LLM layer. One registry (`InferenceEngine`) serves every
feature (ui, proxy, agents, evaluator) with async token streams.

## Architecture

```
InferenceEngine (engine.py)          registry + astream/generate/benchmark
└── LLMBackend (backends/base.py)    AsyncIterator[str] token contract
    ├── MockBackend                  deterministic, zero-dep (tests, air-gapped)
    ├── HuggingFaceBackend           LLaMa-2 / Mistral / Falcon (lazy transformers)
    ├── GPTQBackend                  AutoGPTQ 4/8-bit quants (lazy)
    ├── LlamaCppBackend              GGUF on CPU (lazy llama-cpp-python)
    └── GPT4AllBackend               CPU-only, easiest install (lazy)
Attention Sinks (attention_sinks.py)  sink tokens + sliding window context
HYDE (hyde.py)                        hypothetical-doc query expansion
```

With no runtime installed, `make_backend("auto")` lands on `MockBackend`,
so the whole platform (and its test suite) runs air-gapped.

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_INF_BACKEND` | `auto` | `auto` \| `mock` \| `hf` \| `gptq` \| `llamacpp` \| `gpt4all` |
| `SK_INF_MODEL` | `mistralai/Mistral-7B-v0.1` | HF/GPTQ model id |
| `SK_INF_LLAMACPP_PATH` | *(empty)* | GGUF file path |
| `SK_INF_CONTEXT` | `4096` | context size (llama.cpp) |
| `SK_INF_DEVICE` | *(auto: cuda if present)* | `cuda` \| `cpu` \| `mps` |

## Usage

```python
from features.inference_engine import InferenceEngine, make_backend
from features.inference_engine.attention_sinks import AttentionSinkManager
from features.inference_engine.hyde import hyde

engine = InferenceEngine()
engine.register(make_backend("hf"))           # or gptq/llamacpp/gpt4all/mock

async for tok in engine.astream("mock-1b", messages):
    print(tok, end="")                        # 80 tok/s target, measured:

report = await engine.benchmark("mock-1b")
print(report.tokens_per_sec, report.meets_target)

# Attention Sinks: bound context on long streams
sinks = AttentionSinkManager(sink_size=4, window=512)
async for tok in engine.astream(...):
    sinks.add_token(tok)
compact_context = "".join(sinks.context())    # sinks + window only

# HYDE: expand a question, retrieve on the hypothetical answer
hypothetical = await hyde("Who proposed transformers?", engine.default())
```

## Streaming / async

Every backend streams tokens as `AsyncIterator[str]` (HF decodes on a
worker thread into an asyncio queue — true token streaming, no batch
waiting). `engine.benchmark()` measures real tokens/sec against the
80 t/s target for any registered model.

## Dependencies

Core: none. GPU: `transformers` + `torch` (CUDA), optionally
`auto-gptq`, `vllm` (front it via api_proxy), `outlines` (strict schema
decoding). CPU: `llama-cpp-python` or `gpt4all`. See `requirements.txt`.

## Tests

`tests/` — mock determinism + max_tokens + stop words, scripted
responses (oracle mode), async streaming + timing, Attention Sink
eviction/retention math, StreamingContext wrapping, HYDE expansion +
retrieval round trip, engine registry + unknown-model errors, benchmark
report shape, and unavailable-backend hints.
