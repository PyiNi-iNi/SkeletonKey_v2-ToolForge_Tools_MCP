"""Tests for features.common: OpenAI schemas, streaming, config, errors, http."""

from __future__ import annotations

import asyncio
import json
import re

import http.client

import pytest

from features import common as c
from features.common import (
    ChatCompletionChunk,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    EmbeddingRequest,
    FeatureConfig,
    HttpServer,
    Request,
    Response,
    TokenClock,
    ToolCall,
    Usage,
    collect_stream,
    json_response,
    new_id,
    parse_sse_stream,
    sse_done,
    sse_event,
    stream_with_timing,
)
from features.common.errors import (
    AuthError,
    BackendUnavailableError,
    FeatureError,
    ValidationError,
    from_error,
)


# --------------------------------------------------------------------------
# OpenAI schemas
# --------------------------------------------------------------------------


def test_chat_request_roundtrip_with_tools():
    raw = {
        "model": "mock-1b",
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
        "tool_choice": "auto",
        "temperature": 0.2,
        "max_tokens": 128,
        "custom_field": {"kept": True},
    }
    req = ChatCompletionRequest.from_dict(raw)
    assert req.model == "mock-1b"
    assert len(req.messages) == 4
    assert req.messages[2].tool_calls[0].name == "get_weather"
    assert req.messages[2].tool_calls[0].args == {"city": "Paris"}
    assert req.tools is not None and req.tools[0].function["name"] == "get_weather"
    assert req.extra == {"custom_field": {"kept": True}}
    back = req.to_dict()
    assert back["custom_field"] == {"kept": True}
    assert back["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"city": "Paris"}'


def test_chat_request_requires_messages():
    with pytest.raises(ValueError):
        ChatCompletionRequest.from_dict({"model": "x"})


def test_json_mode_detection():
    req = ChatCompletionRequest.from_dict(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}],
         "response_format": {"type": "json_object"}}
    )
    assert req.wants_json_mode
    assert not req.wants_json_schema
    req2 = ChatCompletionRequest.from_dict(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}],
         "response_format": {"type": "json_schema",
                             "json_schema": {"name": "s", "schema": {"type": "object"}}}}
    )
    assert req2.wants_json_schema
    assert req2.json_schema() == {"type": "object"}


def test_effective_max_tokens_prefers_completion_tokens():
    req = ChatCompletionRequest.from_dict(
        {"model": "m", "messages": [{"role": "user", "content": "hi"}],
         "max_tokens": 10, "max_completion_tokens": 20}
    )
    assert req.effective_max_tokens == 20


def test_response_shape_is_openai():
    resp = ChatCompletionResponse(
        id=new_id(),
        model="mock-1b",
        choices=[
            ChatCompletionChoice(index=0, message=ChatMessage(role="assistant", content="Hello!"))
        ],
        usage=Usage.from_counts(5, 2),
    )
    d = resp.to_dict()
    assert d["object"] == "chat.completion"
    assert d["choices"][0]["message"]["role"] == "assistant"
    assert d["usage"] == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}


def test_chunk_shape_is_openai():
    chunk = ChatCompletionChunk(
        id=new_id("chatcmpl"),
        model="mock-1b",
        delta=ChatMessage(role="assistant", content="Hel"),
    )
    d = chunk.to_dict()
    assert d["object"] == "chat.completion.chunk"
    assert d["choices"][0]["delta"]["content"] == "Hel"
    assert "finish_reason" not in d["choices"][0]
    chunk.finish_reason = "stop"
    assert chunk.to_dict()["choices"][0]["finish_reason"] == "stop"


def test_sse_roundtrip():
    body = sse_event({"a": 1}) + sse_event({"b": 2}) + sse_done()
    assert body.endswith(b"data: [DONE]\n\n")
    parsed = parse_sse_stream(body)
    assert parsed == [{"a": 1}, {"b": 2}]


def test_embedding_request_inputs():
    req = EmbeddingRequest.from_dict({"model": "e", "input": ["a", "b"]})
    assert req.inputs == ["a", "b"]
    req2 = EmbeddingRequest.from_dict({"model": "e", "input": "solo"})
    assert req2.inputs == ["solo"]
    with pytest.raises(ValueError):
        EmbeddingRequest.from_dict({"model": "e", "input": 42})


def test_ids_are_unique_and_prefixed():
    ids = {new_id("cmpl") for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("cmpl-") for i in ids)


def test_unknown_request_fields_preserved():
    raw = {"model": "m", "messages": [{"role": "user", "content": "x"}],
           "some_future_param": [1, 2]}
    req = ChatCompletionRequest.from_dict(raw)
    assert req.extra["some_future_param"] == [1, 2]
    assert "some_future_param" in req.to_dict()


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------


async def _gen(n, sleep=0.0):
    for i in range(n):
        if sleep:
            await asyncio.sleep(sleep)
        yield f"t{i}"


async def test_collect_stream_counts_and_times():
    items, report = await collect_stream(_gen(37))
    assert len(items) == 37
    assert report.count == 37
    d = report.to_dict()
    assert d["count"] == 37
    assert d["meets_target"] is True  # instantaneous beats 80 t/s


async def test_stream_with_timing_snapshots():
    snapshots = [t for _, t in await _drain(stream_with_timing(_gen(5)))]
    assert [s.count for s in snapshots] == [1, 2, 3, 4, 5]


async def _drain(stream):
    out = []
    async for item in stream:
        out.append(item)
    return out


async def test_limit_stream():
    from features.common import limit_stream

    assert len(await _drain(_gen(100))) == 100
    limited = await _drain(limit_stream(_gen(100), 3))
    assert limited == ["t0", "t1", "t2"]
    empty = await _drain(limit_stream(_gen(10), 0))
    assert empty == []


def test_chunk_bytes():
    from features.common import chunk_bytes

    data = bytes(range(256)) * 2
    chunks = chunk_bytes(data, 100)
    assert b"".join(chunks) == data
    assert all(len(x) <= 100 for x in chunks)
    with pytest.raises(ValueError):
        chunk_bytes(data, 0)


def test_token_clock_target_math():
    clock = TokenClock(target_tps=80.0)
    clock.start()
    clock.tick(100)
    report = clock.stop()
    assert report.count == 100
    assert report.tokens_per_sec > 0


async def test_throttled_stream_measures_tps():
    async def slow():
        for _ in range(40):
            await asyncio.sleep(0.002)
            yield "tok"

    _, report = await collect_stream(slow())
    assert report.tokens_per_sec > 300  # ~40 tokens in ~80 ms; well above 80 t/s
    assert report.meets_target


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def test_env_typed_reads(monkeypatch):
    from features.common import env_bool, env_float, env_int, env_list, env_str

    monkeypatch.setenv("SK_TEST_STR", "hello")
    monkeypatch.setenv("SK_TEST_BOOL", "true")
    monkeypatch.setenv("SK_TEST_BOOL2", "0")
    monkeypatch.setenv("SK_TEST_INT", "42")
    monkeypatch.setenv("SK_TEST_INT_BAD", "nope")
    monkeypatch.setenv("SK_TEST_FLOAT", "1.5")
    monkeypatch.setenv("SK_TEST_LIST", "a, b ,c")
    monkeypatch.delenv("SK_MISSING", raising=False)
    assert env_str("SK_TEST_STR") == "hello"
    assert env_bool("SK_TEST_BOOL") is True
    assert env_bool("SK_TEST_BOOL2") is False
    assert env_int("SK_TEST_INT", 0) == 42
    assert env_int("SK_TEST_INT_BAD", default=7) == 7
    assert env_float("SK_TEST_FLOAT", 0.0) == 1.5
    assert env_list("SK_TEST_LIST") == ["a", "b", "c"]
    assert env_str("SK_MISSING", default="d") == "d"


def test_feature_config_resolution(monkeypatch):
    monkeypatch.setenv("SK_DB_DIM", "777")
    cfg = FeatureConfig(prefix="SK_DB")
    assert cfg.get_int("dim", default=384) == 777
    cfg2 = FeatureConfig(prefix="SK_DB", overrides={"dim": 128})
    assert cfg2.get_int("dim", default=384) == 128
    cfg3 = FeatureConfig(prefix="SK_DB")
    assert cfg3.get_int("missing_key", default=384) == 384
    monkeypatch.setenv("SK_DB_BACKEND", "faiss")
    assert cfg3.get_str("backend") == "faiss"


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


def test_error_taxonomy():
    e = BackendUnavailableError("faiss missing", hint='pip install "skeletonkey-toolforge[faiss]"')
    assert e.code == "backend_unavailable"
    assert e.http_status == 503
    d = e.to_dict()
    assert d["hint"].startswith("pip install")
    assert AuthError("nope").http_status == 401
    assert ValidationError("bad").http_status == 422


def test_from_error_maps_feature_and_generic():
    d1 = from_error(FeatureError("x"))
    assert d1["code"] == "feature_error"
    d2 = from_error(ValueError("boom"))
    assert d2["code"] == "internal_error"
    assert "ValueError" in d2["message"]


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------


@pytest.fixture
async def running_server():
    async def handler(req: Request) -> Response:
        if req.path == "/json":
            return json_response({"echo": req.json(), "method": req.method,
                                  "tok": req.bearer_token()})
        if req.path == "/stream":
            async def gen():
                for i in range(3):
                    yield sse_event({"i": i})
                yield sse_done()

            return Response(status=200, stream=gen(),
                            headers=[("Content-Type", "text/event-stream")])
        if req.path == "/error":
            raise ValidationError("nope")
        if req.path == "/plain":
            return Response(status=204)
        return json_response({"error": {"code": "not_found"}}, status=404)

    server = HttpServer(handler, host="127.0.0.1", port=0)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def _http_request_blocking(server, method: str, path: str, body: dict | None = None,
                           token: str | None = None) -> tuple[int, bytes, dict]:
    payload = json.dumps(body).encode() if body is not None else None
    conn = http.client.HTTPConnection(server.host, server.port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    hdrs = dict(resp.getheaders())
    conn.close()
    return resp.status, data, hdrs


async def _http_request(server, *args, **kwargs):
    """Run the blocking stdlib client in a thread — never block the loop
    that the (same-process) test server is running on."""
    return await asyncio.to_thread(_http_request_blocking, server, *args, **kwargs)


async def test_http_json_roundtrip(running_server):
    status, data, _ = await _http_request(running_server, "POST", "/json",
                                          body={"a": 1}, token="sekrit")
    assert status == 200
    body = json.loads(data)
    assert body["echo"] == {"a": 1}
    assert body["method"] == "POST"
    assert body["tok"] == "sekrit"


async def test_http_stream_sse(running_server):
    status, data, hdrs = await _http_request(running_server, "GET", "/stream")
    assert status == 200
    assert hdrs.get("Transfer-Encoding") == "chunked"
    parsed = parse_sse_stream(data)
    assert parsed == [{"i": 0}, {"i": 1}, {"i": 2}]
    assert data.endswith(b"data: [DONE]\n\n")


async def test_http_error_mapping(running_server):
    status, data, _ = await _http_request(running_server, "POST", "/error")
    assert status == 422
    assert json.loads(data)["error"]["code"] == "validation_error"


async def test_http_404_and_no_content(running_server):
    status, data, _ = await _http_request(running_server, "GET", "/nope")
    assert status == 404
    status2, _, _ = await _http_request(running_server, "GET", "/plain")
    assert status2 == 204


async def test_http_keepalive_multiple_requests(running_server):
    def do_keepalive():
        conn = http.client.HTTPConnection(running_server.host, running_server.port, timeout=10)
        try:
            for _ in range(3):
                conn.request("GET", "/plain")
                resp = conn.getresponse()
                resp.read()
                assert resp.status == 204
        finally:
            conn.close()

    await asyncio.to_thread(do_keepalive)
