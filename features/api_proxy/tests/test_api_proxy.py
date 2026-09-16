"""Integration tests for features.api_proxy.

Includes the parameterized schema-validator matrix (the bulk of the
platform's automated test count) plus live HTTP wire-format tests.
"""

from __future__ import annotations

import asyncio
import http.client
import json

import pytest

from features.api_proxy.function_calling import (
    extract_json_block,
    parse_tool_calls,
    validate,
    validate_strict,
)
from features.api_proxy.proxy import OpenAIProxy, estimate_tokens
from features.common.errors import ValidationError
from features.common.http import HttpServer
from features.common.openai_compat import Tool, parse_sse_stream
from features.db_manager import EmbeddingEngine
from features.inference_engine import InferenceEngine, MockBackend

# --------------------------------------------------------------------------
# Schema validator — parameterized matrix
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instance,schema,valid",
    [
        # -- type checks -----------------------------------------------------
        ({"a": 1}, {"type": "object"}, True),
        ({"a": 1}, {"type": "array"}, False),
        ([1, 2], {"type": "array"}, True),
        ([1, 2], {"type": "object"}, False),
        ("hi", {"type": "string"}, True),
        (3, {"type": "string"}, False),
        (5, {"type": "integer"}, True),
        (5.5, {"type": "integer"}, False),
        (True, {"type": "integer"}, False),  # bool is not an integer
        (5.5, {"type": "number"}, True),
        (5, {"type": "number"}, True),
        (True, {"type": "number"}, False),  # bool is not a number
        (True, {"type": "boolean"}, True),
        (1, {"type": "boolean"}, False),
        (None, {"type": "null"}, True),
        (0, {"type": "null"}, False),
        # -- type unions -----------------------------------------------------
        (5, {"type": ["string", "integer"]}, True),
        ("x", {"type": ["string", "integer"]}, True),
        ([1], {"type": ["string", "integer"]}, False),
        # -- enum -------------------------------------------------------------
        ("a", {"type": "string", "enum": ["a", "b"]}, True),
        ("c", {"type": "string", "enum": ["a", "b"]}, False),
        (3, {"enum": [1, 2, 3]}, True),
        (4, {"enum": [1, 2, 3]}, False),
        (None, {"enum": [None, "x"]}, True),
        # -- string constraints -----------------------------------------------
        ("abcd", {"type": "string", "minLength": 4}, True),
        ("abc", {"type": "string", "minLength": 4}, False),
        ("abc", {"type": "string", "maxLength": 3}, True),
        ("abcd", {"type": "string", "maxLength": 3}, False),
        ("ABC123", {"type": "string", "pattern": "^[A-Z0-9]+$"}, True),
        ("abc", {"type": "string", "pattern": "^[A-Z]+$"}, False),
        ("e-mail@x.com", {"type": "string", "pattern": "@"}, True),
        # -- numeric constraints ------------------------------------------------
        (5, {"type": "number", "minimum": 5}, True),
        (4, {"type": "number", "minimum": 5}, False),
        (9, {"type": "number", "maximum": 10}, True),
        (11, {"type": "number", "maximum": 10}, False),
        (7, {"type": "integer", "minimum": 1, "maximum": 10}, True),
        (0, {"type": "integer", "minimum": 1}, False),
        # -- array constraints ---------------------------------------------------
        ([1, 2], {"type": "array", "minItems": 2}, True),
        ([1], {"type": "array", "minItems": 2}, False),
        ([1, 2], {"type": "array", "maxItems": 2}, True),
        ([1, 2, 3], {"type": "array", "maxItems": 2}, False),
        ([1, 2, 3], {"type": "array", "items": {"type": "integer"}}, True),
        ([1, "x"], {"type": "array", "items": {"type": "integer"}}, False),
        ([], {"type": "array", "items": {"type": "string"}}, True),
        # -- object constraints ----------------------------------------------------
        ({"a": 1, "b": 2}, {"type": "object", "required": ["a"]}, True),
        ({"b": 2}, {"type": "object", "required": ["a"]}, False),
        ({"a": 1, "b": 2}, {"type": "object", "required": ["a", "b"]}, True),
        ({"a": 1}, {"type": "object", "required": ["a", "b"]}, False),
        ({"a": 1, "x": 9}, {"type": "object", "properties": {"a": {"type": "integer"}},
                                "additionalProperties": False}, False),
        ({"a": 1}, {"type": "object", "properties": {"a": {"type": "integer"}},
                            "additionalProperties": False}, True),
        ({"a": 1, "x": 9}, {"type": "object", "properties": {"a": {"type": "integer"}},
                                "additionalProperties": {"type": "integer"}}, True),
        ({"a": 1, "x": "no"}, {"type": "object", "properties": {"a": {"type": "integer"}},
                                 "additionalProperties": {"type": "integer"}}, False),
        ({"a": "wrong"}, {"type": "object", "properties": {"a": {"type": "integer"}}},
         False),
        # -- nested ------------------------------------------------------------------
        ({"user": {"name": "a", "age": 3}},
         {"type": "object",
          "properties": {"user": {"type": "object",
                                   "properties": {"name": {"type": "string"},
                                                  "age": {"type": "integer"}},
                                   "required": ["name", "age"]}}},
         True),
        ({"user": {"name": "a"}},
         {"type": "object",
          "properties": {"user": {"type": "object",
                                   "properties": {"name": {"type": "string"},
                                                  "age": {"type": "integer"}},
                                   "required": ["name", "age"]}}},
         False),
        ({"tags": [["a"]]},
         {"type": "object",
          "properties": {"tags": {"type": "array",
                                  "items": {"type": "array",
                                            "items": {"type": "string"}}}}},
         True),
        ({"tags": [["a"], 5]},
         {"type": "object",
          "properties": {"tags": {"type": "array",
                                  "items": {"type": "array",
                                            "items": {"type": "string"}}}}},
         False),
        # -- permissive -------------------------------------------------------------
        ({"anything": 1}, {}, True),
        ({"anything": 1}, {"type": "object"}, True),
        ({"a": [1, {"b": None}]}, {"type": "object"}, True),
    ],
)
def test_schema_validator_matrix(instance, schema, valid):
    errors = validate(instance, schema)
    assert (len(errors) == 0) is valid, f"expected valid={valid}, got errors {errors}"


def test_validate_returns_paths():
    errors = validate(
        {"user": {"age": "not-a-number"}},
        {"type": "object", "properties": {
            "user": {"type": "object", "properties": {"age": {"type": "integer"}}}}})
    assert any("user.age" in e for e in errors)


def test_validate_strict_raises():
    with pytest.raises(ValidationError) as ei:
        validate_strict(1, {"type": "string"})
    assert ei.value.detail


def test_validator_empty_schema_accepts_all():
    for value in (None, 0, "", [], {}):
        assert validate(value, {}) == []


# --------------------------------------------------------------------------
# JSON extraction + tool-call parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,found",
    [
        ('```json\n{"a": 1}\n```', True),
        ('```{"a": 1}```', True),
        ('before {"a": {"b": [1,2]}} after', True),
        ('no json here', False),
        ('{"broken": ', False),
        ('text {"a": "}" } end', True),  # brace inside string
        ('{"a": "x{y"}', True),
    ],
)
def test_extract_json_block(text, found):
    data = extract_json_block(text)
    assert (data is not None) is found
    if found:
        assert isinstance(data, dict)


def test_parse_tool_call_plain_name():
    text = '{"name": "add", "arguments": {"a": 1, "b": 2}}'
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].name == "add"
    assert calls[0].args == {"a": 1, "b": 2}


def test_parse_tool_call_function_shape():
    text = '```json\n{"function": {"name": "get_weather", "arguments": "{\\"city\\": \\"Paris\\"}"}}\n```'
    calls = parse_tool_calls(text)
    assert calls[0].name == "get_weather"
    assert calls[0].args == {"city": "Paris"}  # string args get parsed


def test_parse_tool_call_list_shape():
    text = ('{"tool_calls": [{"id": "c1", "type": "function", '
            '"function": {"name": "a", "arguments": {}}}, '
            '{"id": "c2", "type": "function", '
            '"function": {"name": "b", "arguments": {"x": 1}}}]}')
    calls = parse_tool_calls(text)
    assert [c.name for c in calls] == ["a", "b"]
    assert calls[0].id == "c1"


def test_parse_tool_call_unknown_filtered():
    tools = [Tool.from_dict({"type": "function",
                             "function": {"name": "add", "parameters": {}}})]
    text = '{"name": "drop_table", "arguments": {}} {"name": "add", "arguments": {}}'
    # only first JSON block is used by design
    calls = parse_tool_calls('{"name": "add", "arguments": {"a": 1, "b": 2}}', tools)
    assert [c.name for c in calls] == ["add"]
    text2 = '{"name": "drop_table", "arguments": {}}'
    assert parse_tool_calls(text2, tools) == []


def test_parse_tool_call_no_json():
    assert parse_tool_calls("just prose, no calls") == []


# --------------------------------------------------------------------------
# Proxy unit behavior
# --------------------------------------------------------------------------


@pytest.fixture
def proxy():
    from features.db_manager import HashingEmbedder

    engine = InferenceEngine()
    engine.register(MockBackend("proxy-model"))
    return OpenAIProxy(engine, embed_engine=EmbeddingEngine(HashingEmbedder(dim=64)))


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("x" * 400) == 100


async def test_proxy_models_list(proxy):
    models = proxy.models()
    ids = {m.id for m in models}
    assert "proxy-model" in ids
    assert any(m.owned_by == "skeletonkey" for m in models)


async def test_proxy_chat_nonstream(proxy):
    body = await proxy.chat({
        "model": "proxy-model",
        "messages": [{"role": "user", "content": "hello there friend"}],
        "temperature": 0.5,
    })
    assert body["object"] == "chat.completion"
    assert body["model"] == "proxy-model"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] > 0


async def test_proxy_chat_unknown_model(proxy):
    from features.common.errors import NotFoundError

    with pytest.raises(NotFoundError) as ei:
        await proxy.chat({"model": "ghost", "messages": [{"role": "user", "content": "x"}]})
    assert "does not exist" in ei.value.message


async def test_proxy_completions_multi_prompt(proxy):
    body = await proxy.completions({
        "model": "proxy-model", "prompt": ["one", "two"], "max_tokens": 8})
    assert body["object"] == "text_completion"
    assert len(body["choices"]) == 2
    assert body["choices"][0]["index"] == 0 and body["choices"][1]["index"] == 1
    assert all(c["finish_reason"] == "stop" for c in body["choices"])


async def test_proxy_embeddings(proxy):
    body = await proxy.embeddings({"model": "text-embedding-ada-002",
                                   "input": ["alpha", "beta"]})
    assert body["object"] == "list"
    assert len(body["data"]) == 2
    assert body["data"][0]["index"] == 0
    assert len(body["data"][0]["embedding"]) == 64
    assert body["usage"]["prompt_tokens"] >= 2


async def test_proxy_embeddings_not_configured():
    bare = OpenAIProxy(InferenceEngine())
    from features.common.errors import FeatureError

    with pytest.raises(FeatureError):
        await bare.embeddings({"model": "m", "input": "x"})


async def test_proxy_json_mode_valid_first_try(proxy):
    mock = MockBackend("proxy-model")
    mock.script('```json\n{"answer": 42, "units": "cm"}\n```')
    proxy.engine._backends["proxy-model"] = mock
    body = await proxy.chat({
        "model": "proxy-model",
        "messages": [{"role": "user", "content": "json please"}],
        "response_format": {"type": "json_object"},
    })
    text = body["choices"][0]["message"]["content"]
    assert extract_json_block(text) == {"answer": 42, "units": "cm"}


async def test_proxy_json_mode_retries_on_invalid(proxy):
    mock = MockBackend("proxy-model")
    mock.script("not json at all, sorry")
    mock.script('{"answer": 7}')
    proxy.engine._backends["proxy-model"] = mock
    body = await proxy.chat({
        "model": "proxy-model",
        "messages": [{"role": "user", "content": "json"}],
        "response_format": {"type": "json_object"},
    })
    assert extract_json_block(body["choices"][0]["message"]["content"]) == {"answer": 7}
    # the corrective feedback reached the model
    assert len(mock.last_messages) >= 2
    assert "valid JSON" in mock.last_messages[-1].content


async def test_proxy_json_mode_exhausts_retries(proxy):
    mock = MockBackend("proxy-model")
    for _ in range(5):
        mock.script("still not json")
    proxy.engine._backends["proxy-model"] = mock
    with pytest.raises(ValidationError):
        await proxy.chat({
            "model": "proxy-model",
            "messages": [{"role": "user", "content": "json"}],
            "response_format": {"type": "json_object"},
        })


async def test_proxy_json_schema_strict(proxy):
    schema = {"type": "object",
              "properties": {"city": {"type": "string"},
                             "temp": {"type": "number", "minimum": -50, "maximum": 60}},
              "required": ["city", "temp"], "additionalProperties": False}
    mock = MockBackend("proxy-model")
    mock.script('{"city": "Paris", "temp": 21, "extra": 1}')  # extra prop
    mock.script('{"city": "Paris", "temp": 21}')
    proxy.engine._backends["proxy-model"] = mock
    body = await proxy.chat({
        "model": "proxy-model",
        "messages": [{"role": "user", "content": "weather"}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "w", "schema": schema}},
    })
    assert extract_json_block(body["choices"][0]["message"]["content"]) == {
        "city": "Paris", "temp": 21}


async def test_proxy_function_calling_loop(proxy):
    def get_weather(args):
        return {"city": args.get("city", "?"), "temp_c": 21, "sky": "clear"}

    mock = MockBackend("proxy-model")
    # round 1: model asks for the tool; round 2: model answers
    mock.script('{"name": "get_weather", "arguments": {"city": "Paris"}}')
    mock.script("The weather in Paris is 21 C and clear.")
    proxy.engine._backends["proxy-model"] = mock
    proxy.tool_executors["get_weather"] = get_weather
    body = await proxy.chat({
        "model": "proxy-model",
        "messages": [{"role": "user", "content": "weather in Paris?"}],
        "tools": [{"type": "function",
                   "function": {"name": "get_weather",
                                "parameters": {"type": "object",
                                               "properties": {"city": {"type": "string"}}}}}],
    })
    msg = body["choices"][0]["message"]
    assert "21 C" in msg["content"]
    # the executor result was fed back as a tool message
    tool_msgs = [m for m in mock.last_messages if m.role == "tool"]
    assert tool_msgs
    assert "temp_c" in tool_msgs[0].content
    # the model's own tool-call message is in the history too
    assert any(m.tool_calls for m in mock.last_messages if m.role == "assistant")


async def test_proxy_tool_executor_error_feedback(proxy):
    def boom(args):
        raise ValueError("sensor offline")

    mock = MockBackend("proxy-model")
    mock.script('{"name": "get_temp", "arguments": {}}')
    mock.script("The sensor is offline, sorry.")
    proxy.engine._backends["proxy-model"] = mock
    proxy.tool_executors["get_temp"] = boom
    body = await proxy.chat({
        "model": "proxy-model",
        "messages": [{"role": "user", "content": "temperature?"}],
        "tools": [{"type": "function",
                   "function": {"name": "get_temp", "parameters": {"type": "object"}}}],
    })
    assert "offline" in body["choices"][0]["message"]["content"]


async def test_proxy_stream_chat_chunks(proxy):
    chunks = []
    async for chunk in proxy.stream_chat({
        "model": "proxy-model",
        "messages": [{"role": "user", "content": "stream hello"}],
    }):
        chunks.append(chunk)
    assert chunks, "no chunks"
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    first = chunks[0]["choices"][0]["delta"]
    assert "content" in first
    last = chunks[-1]["choices"][0]
    assert last["finish_reason"] == "stop"
    assert "content" not in last["delta"] or last["delta"]["content"] is None
    # all chunks share the id
    assert len({c["id"] for c in chunks}) == 1


async def test_proxy_stream_completions(proxy):
    chunks = [c async for c in proxy.stream_completions({
        "model": "proxy-model", "prompt": "stream me"})]
    assert chunks
    assert all(c["object"] == "text_completion" for c in chunks)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert "".join(c["choices"][0]["text"] for c in chunks)


# --------------------------------------------------------------------------
# Live HTTP wire tests
# --------------------------------------------------------------------------


@pytest.fixture
async def http_proxy():
    engine = InferenceEngine()
    mock = MockBackend("http-model")
    engine.register(mock)
    proxy = OpenAIProxy(engine, embed_engine=EmbeddingEngine(),
                        tool_executors={"add": lambda a: a["a"] + a["b"]})
    server = HttpServer(_handler(proxy), host="127.0.0.1", port=0)
    await server.start()
    try:
        yield mock, server
    finally:
        await server.stop()


def _handler(proxy):
    from features.api_proxy.server import make_proxy_server

    return make_proxy_server(proxy)


def _call(server, method, path, body=None, token=None):
    conn = http.client.HTTPConnection(server.host, server.port, timeout=15)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                 headers=headers)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


async def test_http_chat_nonstream(http_proxy):
    _, server = http_proxy
    status, data = await asyncio.to_thread(
        _call, server, "POST", "/v1/chat/completions",
        {"model": "http-model", "messages": [{"role": "user", "content": "hi there"}]})
    assert status == 200
    body = json.loads(data)
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "usage" in body


async def test_http_chat_stream_sse(http_proxy):
    _, server = http_proxy
    status, data = await asyncio.to_thread(
        _call, server, "POST", "/v1/chat/completions",
        {"model": "http-model", "stream": True,
         "messages": [{"role": "user", "content": "stream please"}]})
    assert status == 200
    assert data.endswith(b"data: [DONE]\n\n")
    chunks = parse_sse_stream(data)
    assert len(chunks) >= 2
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


async def test_http_completions_and_stream(http_proxy):
    _, server = http_proxy
    status, data = await asyncio.to_thread(
        _call, server, "POST", "/v1/completions",
        {"model": "http-model", "prompt": "legacy prompt"})
    body = json.loads(data)
    assert status == 200 and body["object"] == "text_completion"
    status, data = await asyncio.to_thread(
        _call, server, "POST", "/v1/completions",
        {"model": "http-model", "prompt": "legacy", "stream": True})
    assert parse_sse_stream(data)


async def test_http_embeddings(http_proxy):
    _, server = http_proxy
    status, data = await asyncio.to_thread(
        _call, server, "POST", "/v1/embeddings",
        {"model": "text-embedding-ada-002", "input": ["one", "two", "three"]})
    body = json.loads(data)
    assert status == 200
    assert len(body["data"]) == 3
    assert all(d["embedding"] for d in body["data"])


async def test_http_models_and_health(http_proxy):
    _, server = http_proxy
    status, data = await asyncio.to_thread(_call, server, "GET", "/v1/models")
    body = json.loads(data)
    assert status == 200 and body["object"] == "list"
    assert any(m["id"] == "http-model" for m in body["data"])
    status, data = await asyncio.to_thread(_call, server, "GET", "/health")
    assert status == 200 and json.loads(data)["ok"] is True


async def test_http_unknown_model_404(http_proxy):
    _, server = http_proxy
    status, data = await asyncio.to_thread(
        _call, server, "POST", "/v1/chat/completions",
        {"model": "ghost", "messages": [{"role": "user", "content": "x"}]})
    assert status == 404
    err = json.loads(data)["error"]
    assert "does not exist" in err["message"]
    assert err["type"] == "invalid_request_error"


async def test_http_bad_request_400(http_proxy):
    _, server = http_proxy

    def call_bad():
        conn = http.client.HTTPConnection(server.host, server.port, timeout=10)
        conn.request("POST", "/v1/chat/completions", body=b"{not json",
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        r.read()
        conn.close()
        return r.status

    status = await asyncio.to_thread(call_bad)
    assert status in {400, 422}


async def test_http_function_calling_end_to_end(http_proxy):
    mock, server = http_proxy
    mock.script('{"name": "add", "arguments": {"a": 19, "b": 23}}')
    mock.script("The sum is 42.")
    status, data = await asyncio.to_thread(
        _call, server, "POST", "/v1/chat/completions",
        {"model": "http-model",
         "messages": [{"role": "user", "content": "what is 19+23?"}],
         "tools": [{"type": "function",
                    "function": {"name": "add", "parameters": {"type": "object"}}}]})
    assert status == 200
    body = json.loads(data)
    assert "42" in body["choices"][0]["message"]["content"]


async def test_http_auth_enforced():
    engine = InferenceEngine()
    engine.register(MockBackend("auth-model"))
    proxy = OpenAIProxy(engine)
    from features.api_proxy.server import make_proxy_server

    server = HttpServer(make_proxy_server(proxy, token="sekrit"),
                        host="127.0.0.1", port=0)
    await server.start()
    try:
        status, data = await asyncio.to_thread(_call, server, "GET", "/v1/models")
        assert status == 401
        assert json.loads(data)["error"]["code"] == "auth_error"
        status, data = await asyncio.to_thread(
            _call, server, "GET", "/v1/models", token="wrong")
        assert status == 401
        status, data = await asyncio.to_thread(
            _call, server, "GET", "/v1/models", token="sekrit")
        assert status == 200
    finally:
        await server.stop()
