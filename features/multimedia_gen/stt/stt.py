"""STT backends: faster-whisper / openai-whisper (lazy) + deterministic mock.

Streaming contract: ``astream(chunks)`` yields partial
``TranscriptionResult``s (``is_final=False``) while audio arrives, then
one final (``is_final=True``). Partials are monotonically growing text —
exactly what live caption UIs render.
"""

from __future__ import annotations

import asyncio
import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from features.common.config import FeatureConfig
from features.common.errors import BackendUnavailableError

__all__ = [
    "Segment",
    "TranscriptionResult",
    "STTBackend",
    "WhisperBackend",
    "MockSTT",
    "make_stt_backend",
]


@dataclass
class Segment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


@dataclass
class TranscriptionResult:
    text: str
    segments: list[Segment] = field(default_factory=list)
    language: str | None = None
    duration_sec: float = 0.0
    is_final: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "segments": [s.to_dict() for s in self.segments],
            "language": self.language,
            "duration_sec": round(self.duration_sec, 3),
            "is_final": self.is_final,
        }


class STTBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    async def atranscribe(self, audio: bytes, fmt: str = "wav") -> TranscriptionResult: ...

    @abstractmethod
    async def astream(self, chunks: AsyncIterator[bytes],
                      fmt: str = "wav") -> AsyncIterator[TranscriptionResult]: ...


class WhisperBackend(STTBackend):
    """faster-whisper (preferred) or openai-whisper, lazy + async."""

    name = "whisper"

    def __init__(self, model: str | None = None, device: str | None = None,
                 compute_type: str | None = None):
        cfg = FeatureConfig(prefix="SK_STT")
        self.model_name = model or cfg.get_str("model") or "tiny"
        self.device = device or cfg.get_str("device") or "auto"
        self.compute_type = compute_type or cfg.get_str("compute_type") or "int8"
        self._impl = "faster"
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError:
            try:
                import whisper  # type: ignore
                self._impl = "openai"
                self._model = whisper.load_model(self.model_name, device=self.device)
                return
            except ImportError as exc:
                raise BackendUnavailableError(
                    "faster-whisper / openai-whisper not installed",
                    hint='pip install "faster-whisper>=1.0" (or openai-whisper)',
                ) from exc
        else:
            self._model = WhisperModel(self.model_name, device=self.device,
                                       compute_type=self.compute_type)

    def _transcribe_sync(self, audio: bytes, fmt: str) -> TranscriptionResult:
        import io

        if self._impl == "faster":
            segments_iter, info = self._model.transcribe(
                io.BytesIO(audio), language=None, vad_filter=True)
            segs: list[Segment] = []
            parts: list[str] = []
            for s in segments_iter:
                segs.append(Segment(s.start, s.end, s.text.strip()))
                parts.append(s.text.strip())
            return TranscriptionResult(
                text=" ".join(parts), segments=segs, language=info.language,
                duration_sec=float(info.duration))
        result = self._model.transcribe(audio)
        segs = [Segment(s["start"], s["end"], s["text"].strip())
                for s in result.get("segments", [])]
        return TranscriptionResult(text=result.get("text", "").strip(), segments=segs,
                                   language=result.get("language"),
                                   duration_sec=float(result.get("segments", [{}])[-1].get("end", 0)
                                                      or 0))

    async def atranscribe(self, audio: bytes, fmt: str = "wav") -> TranscriptionResult:
        return await asyncio.to_thread(self._transcribe_sync, audio, fmt)

    async def astream(self, chunks: AsyncIterator[bytes], fmt: str = "wav") -> AsyncIterator[TranscriptionResult]:
        # Re-transcribe the accumulated buffer as it grows; final at the end.
        buf = b""
        last_len = 0
        async for chunk in chunks:
            buf += chunk
            result = await self.atranscribe(buf, fmt)
            if len(result.text) > last_len or result.text:
                last_len = len(result.text)
                yield TranscriptionResult(text=result.text, segments=result.segments,
                                          language=result.language,
                                          duration_sec=result.duration_sec, is_final=False)
        result = await self.atranscribe(buf, fmt)
        yield TranscriptionResult(text=result.text, segments=result.segments,
                                  language=result.language,
                                  duration_sec=result.duration_sec, is_final=True)


class MockSTT(STTBackend):
    """Deterministic mock: 'speaks' one token per 1 KB of audio."""

    name = "mock"

    BYTES_PER_TOKEN = 1024

    def _transcribe_sync(self, audio: bytes) -> TranscriptionResult:
        n_tokens = max(1, len(audio) // self.BYTES_PER_TOKEN)
        words = ["alpha", "beta", "gamma", "delta", "echo", "foxtrot", "golf", "hotel"]
        segs = []
        parts = []
        for i in range(n_tokens):
            segs.append(Segment(start=i * 0.5, end=(i + 1) * 0.5,
                                text=words[i % len(words)]))
            parts.append(words[i % len(words)])
        return TranscriptionResult(text=" ".join(parts), segments=segs,
                                   language="en", duration_sec=n_tokens * 0.5)

    async def atranscribe(self, audio: bytes, fmt: str = "wav") -> TranscriptionResult:
        return self._transcribe_sync(audio)

    async def astream(self, chunks: AsyncIterator[bytes], fmt: str = "wav") -> AsyncIterator[TranscriptionResult]:
        buf = b""
        last = -1
        async for chunk in chunks:
            buf += chunk
            current = len(buf) // self.BYTES_PER_TOKEN
            if current > last:
                last = current
                res = self._transcribe_sync(buf)
                yield TranscriptionResult(text=res.text, segments=res.segments,
                                          language=res.language,
                                          duration_sec=res.duration_sec, is_final=False)
        res = self._transcribe_sync(buf)
        yield res


def make_stt_backend(kind: str | None = None) -> STTBackend:
    cfg = FeatureConfig(prefix="SK_STT")
    name = (kind or cfg.get_str("backend") or "auto").lower()
    if name == "mock":
        return MockSTT()
    if name == "whisper":
        return WhisperBackend()
    if name == "auto":
        try:
            return WhisperBackend()
        except BackendUnavailableError:
            return MockSTT()
    raise BackendUnavailableError(f"unknown stt backend {name!r}", hint="auto|whisper|mock")
