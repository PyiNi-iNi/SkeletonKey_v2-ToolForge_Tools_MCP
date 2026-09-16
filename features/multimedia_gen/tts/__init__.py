"""tts — Microsoft Speech T5 + voice cloning, with WAV streaming.

``TTSBackend.asynthesize`` returns a complete WAV; ``astream`` yields the
44-byte RIFF header first, then bounded PCM chunks — so a client can
start decoding (and even play) before the utterance is finished.
Voice cloning: register a reference sample -> stable voice_id; the mock
derives a per-voice pitch profile, Speech T5 conditions on the sample.
"""

from __future__ import annotations

from .tts import (
    MockTTS,
    SpeechT5Backend,
    TTSBackend,
    VoiceCloner,
    make_tts_backend,
    parse_wav,
    write_wav,
)

__all__ = [
    "MockTTS",
    "SpeechT5Backend",
    "TTSBackend",
    "VoiceCloner",
    "make_tts_backend",
    "parse_wav",
    "write_wav",
]
