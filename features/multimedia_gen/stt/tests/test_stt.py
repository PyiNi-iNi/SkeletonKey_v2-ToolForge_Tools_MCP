"""Integration tests for multimedia_gen/stt (zero heavy deps)."""

from __future__ import annotations

import asyncio

import pytest

from features.common.errors import BackendUnavailableError
from features.multimedia_gen.stt import (
    MockSTT,
    TranscriptionResult,
    make_stt_backend,
)


def audio_chunks(total: int = 8192, chunk: int = 2048):
    async def gen():
        for i in range(0, total, chunk):
            yield bytes(((i + j) % 256 for j in range(min(chunk, total - i))))
            await asyncio.sleep(0)

    return gen()


async def test_mock_transcribe_deterministic():
    stt = MockSTT()
    a = b"\x01" * 4096
    r1 = await stt.atranscribe(a)
    r2 = await stt.atranscribe(a)
    assert r1 == r2
    assert r1.is_final is True
    assert r1.text
    assert r1.language == "en"


async def test_mock_grows_with_audio():
    stt = MockSTT()
    small = await stt.atranscribe(b"\x00" * 1024)
    big = await stt.atranscribe(b"\x00" * 10240)
    assert len(big.text.split()) == 10
    assert len(small.text.split()) == 1
    assert big.duration_sec > small.duration_sec
    assert len(big.segments) == 10
    # segments are contiguous in time
    for prev, nxt in zip(big.segments, big.segments[1:]):
        assert nxt.start >= prev.end - 1e-9


async def test_mock_segment_shape():
    stt = MockSTT()
    r = await stt.atranscribe(b"\x00" * 3072)
    d = r.to_dict()
    assert set(d) == {"text", "segments", "language", "duration_sec", "is_final"}
    assert d["segments"][0]["start"] == 0.0
    assert d["segments"][0]["end"] == 0.5


async def test_mock_empty_audio_still_speaks():
    stt = MockSTT()
    r = await stt.atranscribe(b"")
    assert r.text  # one token minimum


async def test_stream_partials_grow_then_final():
    stt = MockSTT()
    results = []
    async for r in stt.astream(audio_chunks(8192, 2048)):
        results.append(r)
    assert results, "no results"
    assert all(not r.is_final for r in results[:-1])
    assert results[-1].is_final is True
    # monotonic growth
    lens = [len(r.text) for r in results]
    assert lens == sorted(lens)
    assert results[-1].text  # non-empty final


async def test_stream_final_matches_whole():
    stt = MockSTT()
    buf = bytes((i % 251 for i in range(5000)))
    final = None
    async for r in stt.astream(audio_chunks(5000, 1250)):
        if r.is_final:
            final = r
    assert final is not None
    whole = await stt.atranscribe(buf)
    assert final.text == whole.text
    assert final.duration_sec == whole.duration_sec


def test_whisper_missing_runtime():
    from features.multimedia_gen.stt.stt import WhisperBackend

    with pytest.raises(BackendUnavailableError) as ei:
        WhisperBackend()
    assert "whisper" in ei.value.hint


def test_make_backend_auto_falls_back(monkeypatch):
    monkeypatch.delenv("SK_STT_BACKEND", raising=False)
    assert make_stt_backend("auto").name == "mock"


def test_make_backend_unknown():
    with pytest.raises(BackendUnavailableError):
        make_stt_backend("watdeep")


async def test_result_dataclass_defaults():
    r = TranscriptionResult(text="hi")
    assert r.is_final is True
    assert r.segments == []
    d = r.to_dict()
    assert d["duration_sec"] == 0.0
