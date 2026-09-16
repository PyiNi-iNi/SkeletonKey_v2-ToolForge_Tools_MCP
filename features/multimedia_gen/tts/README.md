# features/multimedia_gen/tts — Microsoft Speech T5 + voice cloning

Text-to-speech with a **WAV streaming contract** and voice cloning.

## Architecture

```
TTSBackend (tts.py)
├── SpeechT5Backend   microsoft/speecht5_hifigan (lazy transformers)
└── MockTTS           per-word tone-speech (pure stdlib, 8 kHz 16-bit)
VoiceCloner           stable voice ids from reference samples (JSON profiles)
wav.py                pure-stdlib RIFF/WAV writer + parser (test oracle)
```

## Streaming contract

```python
async for piece in tts.astream("hello world"):
    # first piece is the 44-byte RIFF header, then bounded PCM chunks
    decoder.write(piece)          # can start decoding before TTS finishes
```

`astream` defaults to synthesize-then-split (correct everywhere); native
chunking backends may override.

## Voice cloning

```python
tts = make_tts_backend()
voice_id = tts.clone_voice(open("ref.wav", "rb").read())
wav = await tts.asynthesize("hi, this is the cloned voice", voice_id=voice_id)
```

`VoiceCloner.register` derives a stable id + pitch/brightness profile
from the reference sample (mock applies the pitch offset; Speech T5
conditions on the stored sample — best-effort speaker conditioning,
documented as experimental).

## Environment variables

| Var | Default | Meaning |
| --- | --- | --- |
| `SK_TTS_BACKEND` | `auto` | `auto` \| `speecht5` \| `mock` |
| `SK_TTS_MODEL` | `microsoft/speecht5_hifigan` | HF model id |
| `SK_TTS_DEVICE` | *(auto)* | `cuda` \| `cpu` |
| `SK_TTS_VOICES_DIR` | `~/.skeletonkey/voices` | voice profile + reference storage |

## Dependencies

`transformers>=4.40` + `torch` + `numpy`. Mock: none. See
`requirements.txt`.

## Tests

`tests/` — WAV codec round trip + validation, mock determinism + text
length scaling + voice sensitivity, streaming header-then-chunks
contract + reassembly == full file, voice cloning id stability +
persistence + unknown-voice error, Speech T5 missing-runtime hint,
auto-selection fallback.
