# features/multimedia_gen — image generation, STT, TTS

Three self-contained sub-features, each with its own README,
`requirements.txt`, and tests. All pipelines **stream** via async
generators and degrade to deterministic zero-dep mocks when the heavy
runtime is absent.

| Sub-feature | Heavy runtimes | Zero-dep fallback |
| --- | --- | --- |
| `image_gen/` | Stable Diffusion, Flux (diffusers), PlaygroundAI (key) | hash-driven PNG mock |
| `stt/` | Whisper (faster-whisper / openai-whisper) | deterministic token mock |
| `tts/` | Microsoft Speech T5 (transformers) + voice cloning | per-word tone-speech WAV |

## Shared conventions

- **Streaming**: every sub-feature exposes an async event/chunk stream
  (image progress events, STT partial transcripts, TTS WAV chunks) so UIs
  can render/decode live.
- **Env**: `SK_IMAGEGEN_*`, `SK_STT_*`, `SK_TTS_*` (see sub-READMEs).
- **Codecs**: pure-stdlib PNG (`image_gen/png.py`) and WAV
  (`tts/wav.py`) writers/parsers double as test oracles.

## Quickstart

```python
from features.multimedia_gen.image_gen import ImageGenPipeline
from features.multimedia_gen.stt import make_stt_backend
from features.multimedia_gen.tts import make_tts_backend

# images: live progress events, final PNG
async for ev in ImageGenPipeline().astream_image("a lighthouse at dusk"):
    ...

# stt: partial transcripts while audio streams in
async for partial in make_stt_backend().astream(audio_chunks()):
    print(partial.text, end="")

# tts: WAV header first, then PCM chunks (start decoding early)
tts = make_tts_backend()
voice_id = tts.clone_voice(open("ref.wav", "rb").read())  # optional
async for piece in tts.astream("hello world", voice_id=voice_id):
    decode_or_play(piece)
```

See each sub-folder's README for provider details and env vars.
