"""stt — streaming speech-to-text (Whisper + deterministic mock).

``STTBackend.atranscribe`` handles a complete clip; ``astream`` accepts an
``AsyncIterator[bytes]`` of audio chunks and yields *partial*
transcriptions as they arrive (is_final=False), ending with the final
result — the streaming contract the UI needs for live captions.
"""

from __future__ import annotations

from .stt import (
    MockSTT,
    Segment,
    STTBackend,
    TranscriptionResult,
    WhisperBackend,
    make_stt_backend,
)

__all__ = [
    "MockSTT",
    "Segment",
    "STTBackend",
    "TranscriptionResult",
    "WhisperBackend",
    "make_stt_backend",
]
