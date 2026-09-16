"""Integration tests for features.inference_engine (zero heavy deps)."""

from __future__ import annotations

import asyncio

import pytest

from features.common.errors import BackendUnavailableError, NotFoundError
from features.common.openai_compat import ChatMessage
from features.common.streaming import collect_stream
from features.db_manager import DBManager, HashingEmbedder
from features.inference_engine import (
    AttentionSinkManager,
    InferenceEngine,
    MockBackend,
    StreamingContext,
    hyde,
)
from features.inference_engine.backends import make_backend
from features.inference_engine.backends.base import GenerationParams


def msgs(*contents: str) -> list[ChatMessage]:
    return [ChatMessage(role="user" if i % 2 == 0 else "assistant", content=c)
            for i, c in enumerate(contents)]


# --------------------------------------------------------------------------
# Mock backend
# --------------------------------------------------------------------------


async def test_mock_deterministic():
    b = MockBackend("mock-1b")
    a = await b.generate(msgs("What is a vector database?"))
    c = await b.generate(msgs("What is a vector database?"))
    assert a == c
    assert len(a) > 0


async def test_mock_respects_max_tokens():
    b = MockBackend("mock-1b")
    short = await b.generate(msgs("hi"), GenerationParams(max_tokens=2))
    long = await b.generate(msgs("hi"), GenerationParams(max_tokens=40))
    assert len(short.split()) <= 3  # 2 tokens + slack
    assert len(long) > len(short)


async def test_mock_different_prompts_diverge():
    b = MockBackend("mock-1b")
    a = await b.generate(msgs("vector"))
    c = await b.generate(msgs("quantum"))
    # deterministic but prompt-dependent
    assert a != c


async def test_mock_stop_words():
    b = MockBackend("mock-1b")
    text = await b.generate(msgs("hi"), GenerationParams(max_tokens=40, stop=[" "]))
    # every stop word ends the stream on first token boundary
    assert len(text) <= 40


async def test_mock_scripted_responses():
    b = MockBackend("mock-1b")
    b.script('{"name": "get_weather", "arguments": {"city": "Paris"}}')
    b.script("second answer")
    first = await b.generate(msgs("weather?"))
    second = await b.generate(msgs("weather?"))
    assert first.startswith('{"name"')
    assert second == "second answer"
    # after scripts exhaust, deterministic fallback resumes
    third = await b.generate(msgs("weather?"))
    fourth = await b.generate(msgs("weather?"))
    assert third == fourth


async def test_mock_respond_fn():
    b = MockBackend("mock-1b")
    b.respond_fn = lambda messages, params: "custom override"
    assert await b.generate(msgs("x")) == "custom override"


async def test_mock_stream_tokens():
    b = MockBackend("mock-1b")
    items, report = await collect_stream(b.astream(msgs("stream me")))
    assert len(items) == report.count
    assert report.count >= 8
    assert "".join(items) == await b.generate(msgs("stream me"))


# --------------------------------------------------------------------------
# Engine registry
# --------------------------------------------------------------------------


async def test_engine_registry():
    engine = InferenceEngine()
    engine.register(MockBackend("custom-model"))
    assert set(engine.model_ids()) == {"mock-1b", "custom-model"}
    text = await engine.generate("custom-model", msgs("hi"))
    assert text
    assert engine.info("custom-model").family == "mock"


async def test_engine_unknown_model():
    engine = InferenceEngine()
    with pytest.raises(NotFoundError) as ei:
        await engine.generate("nope", msgs("hi"))
    assert "nope" in ei.value.message


async def test_engine_alias():
    engine = InferenceEngine()
    backend = MockBackend("real-model-id")
    engine.register(backend, alias="fast")
    assert engine.resolve("fast").info.name == "real-model-id"
    assert engine.resolve("real-model-id") is backend


async def test_engine_set_default():
    engine = InferenceEngine()
    engine.register(MockBackend("other"))
    engine.set_default("other")
    assert engine.default().info.name == "other"
    assert (await engine.generate(None, msgs("hi")))


async def test_engine_model_cards():
    engine = InferenceEngine()
    cards = engine.model_cards()
    assert cards and cards[0]["name"] == "mock-1b"
    assert cards[0]["device"] in {"cpu", "cuda", "mps"}


# --------------------------------------------------------------------------
# Backend factory
# --------------------------------------------------------------------------


def test_make_backend_mock():
    b = make_backend("mock", model="m")
    assert b.info.name == "m"
    assert b.info.family == "mock"


def test_make_backend_auto_falls_back(monkeypatch):
    monkeypatch.delenv("SK_INF_BACKEND", raising=False)
    b = make_backend("auto")
    # no HF/gptq/llamacpp/gpt4all in CI -> mock
    assert b.info.family == "mock"


def test_make_backend_hf_missing_gives_hint():
    with pytest.raises(BackendUnavailableError) as ei:
        make_backend("hf", model="x")
    assert "pip install" in (ei.value.hint or "")


def test_make_backend_gptq_requires_model():
    with pytest.raises(BackendUnavailableError):
        make_backend("gptq", model="")


def test_make_backend_unknown():
    with pytest.raises(BackendUnavailableError):
        make_backend("tensorflow")


def test_available_backends_reports_truth():
    from features.inference_engine.backends import available_backends

    backends = available_backends()
    assert isinstance(backends, list)
    # in CI none of the heavy runtimes should be present
    assert all(k in {"hf", "gptq", "llamacpp", "gpt4all"} for k in backends)


# --------------------------------------------------------------------------
# Attention Sinks
# --------------------------------------------------------------------------


def test_sink_manager_retains_sinks_and_window():
    mgr = AttentionSinkManager(sink_size=3, window=5)
    mgr.add([f"t{i}" for i in range(20)])
    ctx = mgr.context()
    assert ctx[:3] == ["t0", "t1", "t2"]           # sinks retained
    assert ctx[-5:] == ["t15", "t16", "t17", "t18", "t19"]  # recent window
    assert len(ctx) == 8
    assert mgr.is_bounded
    assert mgr.evicted == 12
    d = mgr.to_dict()
    assert d["total_seen"] == 20 and d["context_len"] == 8


def test_sink_manager_short_stream_keeps_all():
    mgr = AttentionSinkManager(sink_size=4, window=10)
    mgr.add(["a", "b", "c"])
    assert mgr.context() == ["a", "b", "c"]
    assert mgr.evicted == 0


def test_sink_manager_reset():
    mgr = AttentionSinkManager(sink_size=2, window=2)
    mgr.add(["a", "b", "c", "d"])
    mgr.reset()
    assert mgr.context() == []
    assert mgr.total_seen == 0


def test_sink_manager_bad_params():
    with pytest.raises(ValueError):
        AttentionSinkManager(sink_size=-1, window=4)


async def test_streaming_context_wraps_stream():
    b = MockBackend("mock-1b")
    sc = StreamingContext(AttentionSinkManager(sink_size=2, window=6))
    pairs = []
    async for tok, ctx_len in sc.stream(b.astream(msgs("long stream please"))):
        pairs.append((tok, ctx_len))
    assert len(pairs) >= 8
    # context length never exceeds sink+window
    assert max(c for _, c in pairs) <= 8
    assert sc.compact()  # non-empty retained context


# --------------------------------------------------------------------------
# HYDE
# --------------------------------------------------------------------------


async def test_hyde_produces_expansion():
    b = MockBackend("mock-1b")
    hyp = await hyde("What is vector search?", b)
    assert hyp.strip()
    assert len(hyp) >= 4  # non-trivial
    # deterministic with the mock
    hyp2 = await hyde("What is vector search?", b)
    assert hyp == hyp2


async def test_hyde_falls_back_to_question(monkeypatch):
    b = MockBackend("mock-1b")
    b.respond_fn = lambda m, p: "   "  # whitespace expansion -> fallback
    hyp = await hyde("keep me", b)
    assert hyp == "keep me"


async def test_hyde_retrieval_roundtrip(tmp_path):
    db = DBManager(backend="sqlite", path=str(tmp_path / "hyde.db"), dim=128)
    db.collection("docs").add_texts(
        ["FAISS is a library for fast approximate nearest neighbor search.",
         "The mitochondrion is the powerhouse of the cell."],
    )
    b = MockBackend("mock-1b")
    hyp = await hyde("Which library does fast nearest neighbor search?", b)
    hits = db.collection("docs").query_texts(hyp, n=1)
    assert hits
    db.close()


# --------------------------------------------------------------------------
# Benchmark
# --------------------------------------------------------------------------


async def test_benchmark_report():
    engine = InferenceEngine()
    report = await engine.benchmark("mock-1b", max_tokens=64)
    assert report.tokens > 0
    assert report.tokens_per_sec > 0
    d = report.to_dict()
    assert d["meets_target"] is True  # mock is much faster than 80 t/s
    assert d["model"] == "mock-1b"


async def test_benchmark_slow_stream_reports_miss():
    slow = MockBackend("slow-model", sleep_per_token=0.005)
    engine = InferenceEngine().register(slow)
    report = await engine.benchmark("slow-model", max_tokens=20)
    assert report.tokens_per_sec < 200  # 5ms/token ≈ 200 tps max
    d = report.to_dict()
    assert d["tokens_per_sec"] > 0
