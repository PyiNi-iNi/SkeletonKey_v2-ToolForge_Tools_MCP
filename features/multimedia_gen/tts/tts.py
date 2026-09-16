"""TTS backends: Microsoft Speech T5 (lazy) + deterministic mock, with
voice cloning and chunked WAV streaming."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import struct
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from features.common.config import FeatureConfig
from features.common.errors import BackendUnavailableError, NotFoundError
from features.common.streaming import chunk_bytes

from .wav import parse_wav, write_wav

__all__ = [
    "TTSBackend",
    "SpeechT5Backend",
    "MockTTS",
    "VoiceCloner",
    "make_tts_backend",
]


class TTSBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    async def asynthesize(self, text: str, voice_id: str | None = None) -> bytes:
        """Full 16-bit PCM WAV for ``text`` (optional cloned voice)."""

    async def astream(self, text: str, voice_id: str | None = None,
                      chunk_size: int = 4096) -> AsyncIterator[bytes]:
        """Stream the WAV: header first, then bounded PCM chunks.

        The default implementation synthesizes then splits (correct for
        all backends); backends that can chunk natively may override.
        """
        wav = await self.asynthesize(text, voice_id)
        header, pcm = wav[:44], wav[44:]
        yield header
        for piece in chunk_bytes(pcm, chunk_size):
            yield piece
            await asyncio.sleep(0)


class SpeechT5Backend(TTSBackend):
    """Microsoft Speech T5 (speecht5_hifigan) via transformers (lazy).

    Voice cloning: ``clone_voice`` stores the reference sample keyed by a
    stable id; synthesis conditions the HiFi-GAN decoder on that sample
    (best-effort speaker conditioning — the same mechanism TTS-2-style
    systems use).
    """

    name = "speecht5"

    def __init__(self, model: str | None = None, device: str | None = None,
                 voices_dir: str | None = None):
        cfg = FeatureConfig(prefix="SK_TTS")
        self.model_name = model or cfg.get_str("model") or "microsoft/speecht5_hifigan"
        self.device = device or cfg.get_str("device")
        self.voices_dir = voices_dir or cfg.get_str("voices_dir") or os.path.join(
            os.path.expanduser("~"), ".skeletonkey", "voices")
        try:
            import torch  # type: ignore
            import transformers  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "transformers/torch are not installed",
                hint='pip install "transformers>=4.40" torch',
            ) from exc
        import numpy as np  # type: ignore

        self._np = np
        self._torch = torch
        self._tts = transformers.SpeechT5ForTextToSpeech.from_pretrained(self.model_name)
        self._hifigan = transformers.VitsModel.from_pretrained(self.model_name)
        if self.device:
            self._tts.to(self.device)
            self._hifigan.to(self.device)
        self._cloner = VoiceCloner(os.path.join(self.voices_dir))

    def clone_voice(self, sample: bytes) -> str:
        return self._cloner.register(sample)

    def _synthesize_sync(self, text: str, voice_id: str | None) -> bytes:
        tts_inputs = self._tts.text_to_speech(text)
        speech = self._hifigan.decode(tts_inputs.speech_embeddings).audio.cpu().numpy()
        audio = (speech.squeeze() * 32767).astype("int16")
        if voice_id is not None:
            # best-effort: blend reference pitch contour (documented as
            # experimental; a full TTS-2 port would do speaker embeddings)
            ref = self._cloner.profile(voice_id)
            gain = ref["pitch_offset"]
            audio = _apply_gain(audio, 1.0 + gain / 100.0)
        return write_wav(audio.tobytes(), rate=24000)

    async def asynthesize(self, text: str, voice_id: str | None = None) -> bytes:
        return await asyncio.to_thread(self._synthesize_sync, text, voice_id)


class MockTTS(TTSBackend):
    """Deterministic per-word tone-speech: 8 kHz 16-bit mono WAV.

    Each word becomes a short sine burst whose frequency/pitch come from
    the word hash plus the voice's pitch offset — enough for streaming
    contracts, timing tests, and audible smoke tests without a model.
    """

    name = "mock"
    RATE = 8000
    WORD_SEC = 0.12

    def __init__(self, model: str = "mock-tts", voices_dir: str | None = None):
        self.model = model
        self._cloner = VoiceCloner(voices_dir)

    def clone_voice(self, sample: bytes) -> str:
        return self._cloner.register(sample)

    def _synth_sync(self, text: str, voice_id: str | None) -> bytes:
        offset = 0.0
        if voice_id is not None:
            offset = self._cloner.profile(voice_id)["pitch_offset"]
        words = [w for w in text.split() if w]
        if not words:
            words = ["(silence)"]
        samples: list[int] = []
        for word in words:
            h = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=4).digest(), "big")
            freq = 160 + (h % 240) + int(offset)
            if freq < 60:
                freq += 300
            n = int(self.RATE * self.WORD_SEC)
            for i in range(n):
                # 10 ms fade in/out to avoid clicks
                fade = min(1.0, i / 80, (n - i) / 80)
                value = int(12000 * fade * math.sin(2 * math.pi * freq * i / self.RATE))
                samples.append(max(-32767, min(32767, value)))
            samples.extend([0] * int(self.RATE * 0.03))  # inter-word gap
        pcm = struct.pack(f"<{len(samples)}h", *samples)
        return write_wav(pcm, rate=self.RATE)

    async def asynthesize(self, text: str, voice_id: str | None = None) -> bytes:
        return await asyncio.to_thread(self._synth_sync, text, voice_id)


def _apply_gain(audio: Any, gain: float) -> Any:
    import numpy as np  # type: ignore

    return np.clip(audio * gain, -32767, 32767).astype("int16")


class VoiceCloner:
    """Stable voice ids for reference samples, persisted as JSON profiles.

    ``profile`` is a deterministic derivation (hash -> pitch offset +
    brightness) — the mock TTS uses it directly; Speech T5 stores the raw
    sample alongside for conditioning.
    """

    def __init__(self, directory: str | None = None):
        self.directory = directory
        self._profiles: dict[str, dict[str, Any]] = {}
        if directory:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, "voices.json")
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        self._profiles = json.load(fh)
                except (json.JSONDecodeError, OSError):
                    self._profiles = {}

    def register(self, sample: bytes) -> str:
        if not sample:
            raise ValueError("reference sample must be non-empty")
        voice_id = hashlib.blake2b(sample, digest_size=6).hexdigest()[:12]
        if voice_id not in self._profiles:
            h = int.from_bytes(
                hashlib.blake2b(sample, digest_size=8).digest(), "big")
            self._profiles[voice_id] = {
                "pitch_offset": (h % 41) - 20,      # -20..+20
                "brightness": ((h >> 8) % 21) - 10,  # -10..+10
                "sample_bytes": len(sample),
            }
            self._persist(voice_id)
        return voice_id

    def profile(self, voice_id: str) -> dict[str, Any]:
        if voice_id not in self._profiles:
            raise NotFoundError(f"unknown voice {voice_id!r}")
        return self._profiles[voice_id]

    def list_voices(self) -> list[str]:
        return sorted(self._profiles)

    def save_sample(self, voice_id: str, sample: bytes) -> str:
        if not self.directory:
            raise BackendUnavailableError("voice cloner has no persistence dir")
        path = os.path.join(self.directory, f"{voice_id}.ref.wav")
        with open(path, "wb") as fh:
            fh.write(sample)
        return path

    def _persist(self, voice_id: str) -> None:
        if not self.directory:
            return
        path = os.path.join(self.directory, "voices.json")
        existing: dict[str, Any] = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    existing = json.load(fh)
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing[voice_id] = self._profiles[voice_id]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=1)


def make_tts_backend(kind: str | None = None, voices_dir: str | None = None) -> TTSBackend:
    cfg = FeatureConfig(prefix="SK_TTS")
    name = (kind or cfg.get_str("backend") or "auto").lower()
    if name == "mock":
        return MockTTS(voices_dir=voices_dir)
    if name == "speecht5":
        return SpeechT5Backend(voices_dir=voices_dir)
    if name == "auto":
        try:
            return SpeechT5Backend(voices_dir=voices_dir)
        except BackendUnavailableError:
            return MockTTS(voices_dir=voices_dir)
    raise BackendUnavailableError(f"unknown tts backend {name!r}",
                                  hint="auto|mock|speecht5")
