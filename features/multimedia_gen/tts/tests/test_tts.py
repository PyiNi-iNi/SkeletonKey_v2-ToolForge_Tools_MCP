"""Integration tests for multimedia_gen/tts (zero heavy deps)."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from features.common.errors import BackendUnavailableError, NotFoundError
from features.multimedia_gen.tts import (
    MockTTS,
    VoiceCloner,
    make_tts_backend,
    parse_wav,
    write_wav,
)


# --------------------------------------------------------------------------
# WAV codec
# --------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 100, 4096, 100000])
def test_wav_roundtrip(n):
    import struct

    pcm = struct.pack(f"<{n}h", *[(i * 7) % 3000 - 1500 for i in range(n)])
    wav = write_wav(pcm, rate=8000)
    parsed = parse_wav(wav)
    assert parsed["rate"] == 8000
    assert parsed["channels"] == 1
    assert parsed["bits"] == 16
    assert parsed["frames"] == n
    assert parsed["pcm"] == pcm


def test_wav_rejects_garbage():
    with pytest.raises(ValueError):
        parse_wav(b"not a wav file at all, just text")


def test_wav_rejects_non_pcm():
    # RIFF/WAVE with fmt=3 (float) must be rejected
    import struct

    bad = (b"RIFF" + struct.pack("<I", 36) + b"WAVE" +
           b"fmt " + struct.pack("<IHHIIHH", 16, 3, 1, 8000, 32000, 4, 32) +
           b"data" + struct.pack("<I", 0))
    with pytest.raises(ValueError):
        parse_wav(bad)


def test_wav_rejects_non16bit_write():
    with pytest.raises(ValueError):
        write_wav(b"\x00" * 10, bits=8)


# --------------------------------------------------------------------------
# Mock TTS
# --------------------------------------------------------------------------


async def test_mock_deterministic():
    tts = MockTTS()
    a = await tts.asynthesize("hello world")
    b = await tts.asynthesize("hello world")
    assert a == b
    parsed = parse_wav(a)
    assert parsed["rate"] == 8000
    assert parsed["frames"] > 1000


async def test_mock_longer_text_longer_wav():
    tts = MockTTS()
    short = parse_wav(await tts.asynthesize("hi"))
    long = parse_wav(await tts.asynthesize("hello there friend, how are you doing today"))
    assert long["frames"] > short["frames"] * 2


async def test_mock_text_sensitive():
    tts = MockTTS()
    a = await tts.asynthesize("hello")
    b = await tts.asynthesize("goodbye")
    assert a != b


async def test_mock_empty_text():
    tts = MockTTS()
    wav = await tts.asynthesize("")
    parse_wav(wav)  # still valid (silence token)


# --------------------------------------------------------------------------
# Voice cloning
# --------------------------------------------------------------------------


def test_voice_cloner_stable_ids():
    c = VoiceCloner()
    sample = b"reference audio sample"
    v1 = c.register(sample)
    v2 = c.register(sample)
    assert v1 == v2
    assert len(v1) == 12
    # different sample -> different id
    v3 = c.register(b"totally different reference")
    assert v3 != v1


def test_voice_cloner_profiles_in_range():
    c = VoiceCloner()
    for i in range(20):
        p = c.profile(c.register(f"sample {i}".encode()))
        assert -20 <= p["pitch_offset"] <= 20
        assert -10 <= p["brightness"] <= 10


def test_voice_cloner_unknown_voice():
    c = VoiceCloner()
    with pytest.raises(NotFoundError):
        c.profile("deadbeef0000")


def test_voice_cloner_empty_sample():
    c = VoiceCloner()
    with pytest.raises(ValueError):
        c.register(b"")


def test_voice_cloner_persists(tmp_path):
    c = VoiceCloner(directory=str(tmp_path))
    vid = c.register(b"ref sample data")
    c.save_sample(vid, b"ref sample data")
    assert os.path.exists(tmp_path / "voices.json")
    assert os.path.exists(tmp_path / f"{vid}.ref.wav")
    stored = json.loads((tmp_path / "voices.json").read_text())
    assert vid in stored
    # a new cloner on the same dir reloads the profile
    c2 = VoiceCloner(directory=str(tmp_path))
    assert c2.profile(vid)["pitch_offset"] == stored[vid]["pitch_offset"]


async def test_mock_voice_changes_output():
    tts = MockTTS()
    sample_a = b"speaker A reference"
    sample_b = b"speaker B reference, quite different"
    va, vb = tts.clone_voice(sample_a), tts.clone_voice(sample_b)
    base = await tts.asynthesize("hello there")
    voiced_a = await tts.asynthesize("hello there", voice_id=va)
    voiced_b = await tts.asynthesize("hello there", voice_id=vb)
    assert voiced_a != base
    assert voiced_b != base
    assert voiced_a != voiced_b  # (probabilistically; hashes differ)
    parse_wav(voiced_a)
    parse_wav(voiced_b)


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------


async def test_stream_header_then_chunks():
    tts = MockTTS()
    pieces = []
    async for piece in tts.astream("streaming speech synthesis test"):
        pieces.append(piece)
    assert pieces, "no pieces"
    header = pieces[0]
    assert header[:4] == b"RIFF" and header[8:12] == b"WAVE"
    assert len(header) == 44
    reassembled = b"".join(pieces)
    whole = await tts.asynthesize("streaming speech synthesis test")
    assert reassembled == whole
    parse_wav(reassembled)


async def test_stream_chunk_bound():
    tts = MockTTS()
    pieces = []
    async for piece in tts.astream("a somewhat longer sentence to get many chunks",
                                   chunk_size=256):
        pieces.append(piece)
    for piece in pieces[1:]:
        assert len(piece) <= 256
    assert b"".join(pieces) == await tts.asynthesize(
        "a somewhat longer sentence to get many chunks")


async def test_stream_empty_text():
    tts = MockTTS()
    pieces = [p async for p in tts.astream("")]
    assert pieces[0][:4] == b"RIFF"


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def test_speecht5_missing_runtime():
    from features.multimedia_gen.tts.tts import SpeechT5Backend

    with pytest.raises(BackendUnavailableError) as ei:
        SpeechT5Backend()
    assert "pip install" in ei.value.hint


def test_make_backend_auto_falls_back(monkeypatch):
    monkeypatch.delenv("SK_TTS_BACKEND", raising=False)
    assert make_tts_backend("auto").name == "mock"


def test_make_backend_unknown():
    with pytest.raises(BackendUnavailableError):
        make_tts_backend("espeak")
