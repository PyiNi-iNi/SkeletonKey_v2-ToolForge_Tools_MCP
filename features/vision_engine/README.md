# features/vision_engine — LLaVa / Claude-3 / Gemini-Pro / GPT-4 Vision

Unified image understanding across providers. Each backend speaks its
vendor's exact wire format (verified by tests against a local mock HTTP
server); a deterministic mock keeps the feature testable air-gapped.

## Architecture

```
VisionEngine (engine.py)        provider routing + capability probe
└── VisionBackend (backends.py)
    ├── OpenAIVisionBackend     gpt-4o / gpt-4-vision — chat image_url parts
    ├── AnthropicVisionBackend  claude-3-* — /v1/messages base64 image blocks
    ├── GeminiVisionBackend     gemini-pro-vision — generateContent inline_data
    ├── LLaVaBackend            local llava-1.5 (lazy transformers)
    └── MockVisionBackend       deterministic (tests / air-gapped)
```

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_VISION_PROVIDER` | `auto` | `auto` \| `mock` \| `openai` \| `anthropic` \| `gemini` \| `llava` |
| `SK_VISION_OPENAI_KEY` / `_URL` / `_MODEL` | *(api.openai.com)* / `gpt-4o` | GPT-4 vision |
| `SK_VISION_ANTHROPIC_KEY` / `_URL` / `_MODEL` | *(api.anthropic.com)* / `claude-3-opus-20240229` | Claude 3 |
| `SK_VISION_GEMINI_KEY` / `_URL` / `_MODEL` | *(generativelanguage.googleapis.com)* / `gemini-pro-vision` | Gemini |
| `SK_VISION_LLAVA_MODEL` / `_DEVICE` | `llava-hf/llava-1.5-7b-hf` | local LLaVA |

`auto` = first provider whose key (or local model) is present, else mock.
Base URLs are overridable — point them at any OpenAI-compatible server
(including this platform's own api_proxy).

## Usage

```python
from features.vision_engine import VisionEngine, VisionInput

engine = VisionEngine()                      # auto-routes
desc = await engine.adescribe(VisionInput.from_path("photo.jpg"),
                              "Describe the scene")
print(engine.provider)                       # openai | anthropic | gemini | llava | mock
print(engine.supported())                    # {"openai": False, "mock": True, ...}
```

## Streaming / async

`adescribe` is async (HTTP via `asyncio.to_thread`, LLaVA decoding
off-thread). Image generation *streaming* lives in
`features/multimedia_gen/image_gen` (progress events).

## Dependencies

Cloud providers: keys only (stdlib HTTP). Local LLaVA:
`transformers` + `torch` + `pillow` (see `requirements.txt`). Cross-
platform; no POSIX-only code.

## Tests

`tests/` — data-URI + mime detection, per-provider request body shape
against a live local mock HTTP server (OpenAI/Anthropic/Gemini),
upstream error mapping (401 → UpstreamError), provider auto-selection
matrix, LLaVA missing-runtime hint, mock determinism, path-based input,
capability probe.
