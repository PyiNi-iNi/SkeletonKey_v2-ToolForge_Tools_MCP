# features/api_proxy — OpenAI-compatible server proxy

Serves the platform as an OpenAI-compatible endpoint: any OpenAI SDK
client (Python/JS/curl) can point `base_url` at it.

## Endpoints

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/v1/chat/completions` | SSE streaming + non-streaming, **function calling**, **JSON mode** |
| POST | `/v1/completions` | SSE streaming + non-streaming (legacy) |
| POST | `/v1/embeddings` | backed by db_manager's EmbeddingEngine |
| GET | `/v1/models` | from InferenceEngine registry (+ embedder) |
| GET | `/health` | liveness + model ids |

All responses use the standard OpenAI schemas (`chat.completion`,
`chat.completion.chunk`, `text_completion`, `list` for embeddings) and
OpenAI's `{"error": {...}}` envelope.

## Function calling

Register local callables; models that emit tool calls (structured or
JSON-in-text — the mock does the latter) drive the loop:

```python
proxy = OpenAIProxy(engine, tool_executors={
    "get_weather": lambda args: {"city": args["city"], "temp_c": 21, "sky": "clear"},
    "add": lambda args: args["a"] + args["b"],
})
```

Loop: model → tool_calls → executor → tool result message → model →
final answer (≤ `max_tool_iterations`). Executor exceptions are fed back
to the model as error results.

## JSON mode / strict schema

- `response_format: {"type": "json_object"}` — output must parse as JSON.
- `response_format: {"type": "json_schema", "json_schema": {...}}` —
  output must **also** satisfy the schema (subset validator: type,
  properties, required, items, enum, min/max (length, items, value),
  pattern, additionalProperties). Invalid output triggers a corrective
  re-generation up to `json_retry` times, then a 422.
- GPU-side equivalent: constrained decoding with vLLM/Outlines (point the
  proxy at a vLLM server via an HF backend, or use Outlines inside one).

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_PROXY_HOST` / `SK_PROXY_PORT` | `0.0.0.0` / `8780` | bind |
| `SK_PROXY_TOKEN` | *(unset → open)* | required Bearer token when set |
| `SK_PROXY_JSON_RETRY` | `2` | JSON-mode corrective retries |
| `SK_PROXY_MAX_TOOL_ITERS` | `5` | tool loop cap |

## Usage

```python
from features.inference_engine import InferenceEngine, make_backend
from features.db_manager import EmbeddingEngine
from features.api_proxy import OpenAIProxy
from features.api_proxy.server import run_proxy_server

engine = InferenceEngine().register(make_backend("mock"))
proxy = OpenAIProxy(engine, embed_engine=EmbeddingEngine(),
                    tool_executors={"add": lambda a: a["a"] + a["b"]})
server = await run_proxy_server(proxy, host="0.0.0.0", port=8780,
                                token=os.environ.get("SK_PROXY_TOKEN"))
```

```bash
curl http://localhost:8780/v1/chat/completions \
  -H 'Authorization: Bearer ...' -H 'Content-Type: application/json' \
  -d '{"model":"mock-1b","stream":true,
       "messages":[{"role":"user","content":"Say hi"}]}'
```

## Streaming

`stream: true` returns `text/event-stream` chunks (OpenAI
`chat.completion.chunk` / `text_completion` objects + `data: [DONE]`).
Token-level streaming comes straight from the backend; when tools or
JSON mode are active the final text is re-streamed in word deltas
(documented tradeoff — correctness first).

## Tests

`tests/` — ~300 parameterized cases: every OpenAI payload shape
round-trips; the schema validator (types/enums/ranges/patterns/nesting/
additionalProperties); tool-call parsing shapes; live HTTP: chat
(stream + non-stream), completions (stream + non-stream), embeddings,
models, health; bearer auth 401s; function-calling loop end-to-end
(scripted model → executor → answer); JSON mode happy path + retry-on-
invalid + 422 after retries; unknown model 404 envelope.
