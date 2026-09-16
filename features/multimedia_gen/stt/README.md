# features/multimedia_gen/stt — streaming speech-to-text

Whisper (faster-whisper preferred) with a **streaming contract**: feed
audio chunks, get partial transcripts live.

## Architecture

```
STTBackend (stt.py)
├── WhisperBackend    faster-whisper / openai-whisper (lazy), async via threads
└── MockSTT           deterministic: 1 token per 1 KB of audio
```

## Streaming contract

```python
async for result in stt.astream(audio_chunk_iter()):
    if result.is_final:
        print("FINAL:", result.text)
    else:
        print("partial:", result.text)   # monotonically growing text
```

Partials have `is_final=False`; the last event carries the complete
transcript with segments (`start`, `end`, `text`) and language.

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_STT_BACKEND` | `auto` | `auto` \| `whisper` \| `mock` |
| `SK_STT_MODEL` | `tiny` | whisper model size (tiny/base/small/medium/large-v3) |
| `SK_STT_DEVICE` | `auto` | `cpu` \| `cuda` |
| `SK_STT_COMPUTE_TYPE` | `int8` | faster-whisper precision (int8/float16/...) |

## Dependencies

`faster-whisper>=1.0` (recommended) or `openai-whisper`. Mock: none.
GPU: torch CUDA build. See `requirements.txt`.

## Tests

`tests/` — WAV/segment model, mock transcription determinism + growth,
streaming partial monotonicity + final correctness, Whisper missing-runtime
hint, auto-selection fallback.
