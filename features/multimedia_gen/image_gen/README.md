# features/multimedia_gen/image_gen — Stable Diffusion / Flux / Playground

Streaming image generation with live progress events.

## Architecture

```
ImageGenPipeline (pipeline.py)      astream_image -> live events + final PNG
└── ImageGenBackend (backends.py)
    ├── StableDiffusionBackend      diffusers SD 1.5+ (lazy)
    ├── FluxBackend                 diffusers Flux.1 (lazy)
    ├── PlaygroundBackend           PlaygroundAI REST (key-only, stdlib)
    └── MockImageBackend            deterministic gradient PNG (pure stdlib)
png.py                              pure-stdlib PNG writer + header parser
```

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_IMAGEGEN_BACKEND` | `auto` | `auto` \| `mock` \| `stablediffusion` \| `flux` \| `playground` |
| `SK_IMAGEGEN_SD_MODEL` / `_DEVICE` | `runwayml/stable-diffusion-v1-5` | SD pipeline |
| `SK_IMAGEGEN_FLUX_MODEL` / `_DEVICE` | `black-forest-labs/FLUX.1-schnell` | Flux pipeline |
| `SK_IMAGEGEN_PLAYGROUND_KEY` / `_URL` / `_MODEL` | *(api.playground.ai)* / `playground-v2.5` | PlaygroundAI |

## Streaming protocol

```python
async for ev in pipeline.astream_image("a lighthouse at dusk",
                                       ImageParams(width=512, height=512, steps=20)):
    print(ev)
# {"stage": "queue", "step": 0}
# {"stage": "model_load", "step": 0}
# {"stage": "denoise", "step": 1, "steps": 20, "p": 0.05}
# ...
# {"stage": "done", "image": <png bytes>, "mime": "image/png", "meta": {...}}
```

## Usage

```python
from features.multimedia_gen.image_gen import ImageGenPipeline, ImageParams

pipe = ImageGenPipeline()                      # auto: SD -> Flux -> Playground -> mock
result = await pipe.generate("a red fox", ImageParams(seed=42))
open("fox.png", "wb").write(result.image)
```

## Dependencies

GPU: `diffusers` + `torch` (CUDA) + `accelerate`. CPU: same, cpu wheels.
PlaygroundAI: key only. Mock: none. See `requirements.txt`.

## Tests

`tests/` — PNG codec round trip + CRC validation, mock determinism +
prompt sensitivity, streaming event ordering + progress math + final
image validity, Playground wire format against a local mock server,
upstream error mapping, auto-selection fallback, backend hints.
