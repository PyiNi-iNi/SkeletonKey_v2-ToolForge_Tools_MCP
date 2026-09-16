"""Integration tests for features.vision_engine (zero heavy deps).

Remote providers are exercised against a local mock HTTP server that
echoes/captures the exact wire format we send.
"""

from __future__ import annotations

import asyncio
import base64
import http.client
import json
import struct
import zlib

import pytest

from features.common.errors import BackendUnavailableError, UpstreamError
from features.common.http import HttpServer
from features.vision_engine import (
    AnthropicVisionBackend,
    GeminiVisionBackend,
    LLaVaBackend,
    MockVisionBackend,
    OpenAIVisionBackend,
    VisionEngine,
)
from features.vision_engine.backends import (
    VisionInput,
    image_to_data_uri,
    mime_for_bytes,
)


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


def make_png(width: int = 8, height: int = 8, rgb: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    """Minimal valid RGB PNG, stdlib only (zlib + struct)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    for _ in range(height):
        raw += b"\x00" + bytes(rgb) * width
    idat = zlib.compress(raw)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
            chunk(b"IDAT", idat) + chunk(b"IEND", b""))


PNG = make_png()
JPEG = b"\xff\xd8\xff\xe0" + b"fakejpeg" * 10
GIF = b"GIF89a" + b"\x00" * 16


@pytest.fixture
async def captured_server():
    """Local OpenAI/Anthropic/Gemini mimic that records requests."""
    captured: list[dict] = []
    replies = {"openai": None, "anthropic": None, "gemini": None}

    async def handler(req):
        captured.append({"path": req.path, "headers": req.headers, "body": req.json()})
        if req.path.endswith("/chat/completions"):
            from features.common import json_response
            return json_response(replies["openai"] or {
                "choices": [{"message": {"role": "assistant", "content": "a red square"}}]})
        if req.path.endswith("/v1/messages"):
            from features.common import json_response
            return json_response(replies["anthropic"] or {
                "content": [{"type": "text", "text": "claude says hi"}]})
        if ":generateContent" in req.path:
            from features.common import json_response
            return json_response(replies["gemini"] or {
                "candidates": [{"content": {"parts": [{"text": "gemini sees it"}]}}]})
        from features.common import json_response
        return json_response({"error": {"message": "not found"}}, status=404)

    server = HttpServer(handler, host="127.0.0.1", port=0)
    await server.start()
    try:
        yield captured, replies, server
    finally:
        await server.stop()


# --------------------------------------------------------------------------
# Image plumbing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data,expected",
    [(PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"),
     (b"RIFFxxxxWEBPdata", "image/webp"), (b"???", "image/png")],
)
def test_mime_detection(data, expected):
    assert mime_for_bytes(data) == expected


def test_data_uri_roundtrip():
    uri = image_to_data_uri(PNG)
    assert uri.startswith("data:image/png;base64,")
    payload = base64.b64decode(uri.split(",", 1)[1])
    assert payload == PNG


def test_vision_input_from_path(tmp_path):
    p = tmp_path / "img.png"
    p.write_bytes(PNG)
    vi = VisionInput.from_path(str(p))
    assert vi.bytes() == PNG
    assert vi.data_uri().startswith("data:image/png")


# --------------------------------------------------------------------------
# Provider wire formats (against local mock servers)
# --------------------------------------------------------------------------


async def test_openai_wire_format(captured_server):
    captured, replies, server = captured_server
    b = OpenAIVisionBackend(api_key="sk-test", base_url=f"http://{server.host}:{server.port}")
    text = await b.adescribe(VisionInput(data=PNG), "what is this?")
    assert text == "a red square"
    req = captured[-1]
    assert req["path"] == "/chat/completions"
    assert req["headers"]["authorization"] == "Bearer sk-test"
    body = req["body"]
    assert body["model"] == "gpt-4o"
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "what is this?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_openai_upstream_error(captured_server):
    captured, replies, server = captured_server
    replies["openai"] = {"error": {"message": "bad key"}}

    async def handler401(req):
        from features.common import json_response
        return json_response({"error": {"message": "bad key"}}, status=401)

    await server.stop()
    server2 = HttpServer(handler401, host="127.0.0.1", port=0)
    await server2.start()
    try:
        b = OpenAIVisionBackend(api_key="sk-bad", base_url=f"http://{server2.host}:{server2.port}")
        with pytest.raises(UpstreamError) as ei:
            await b.adescribe(VisionInput(data=PNG), "x")
        assert "401" in ei.value.message
    finally:
        await server2.stop()


async def test_anthropic_wire_format(captured_server):
    captured, replies, server = captured_server
    b = AnthropicVisionBackend(api_key="ant-test",
                               base_url=f"http://{server.host}:{server.port}",
                               model="claude-3-haiku-20240307")
    text = await b.adescribe(VisionInput(data=JPEG, mime="image/jpeg"), "describe")
    assert text == "claude says hi"
    req = captured[-1]
    assert req["path"] == "/v1/messages"
    assert req["headers"]["x-api-key"] == "ant-test"
    assert req["headers"]["anthropic-version"] == "2023-06-01"
    parts = req["body"]["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image"
    assert parts[1]["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(parts[1]["source"]["data"]) == JPEG


async def test_gemini_wire_format(captured_server):
    captured, replies, server = captured_server
    b = GeminiVisionBackend(api_key="gm-test", base_url=f"http://{server.host}:{server.port}",
                            model="gemini-pro-vision-001")
    text = await b.adescribe(VisionInput(data=PNG), "what do you see")
    assert text == "gemini sees it"
    req = captured[-1]
    assert req["path"] == "/v1beta/models/gemini-pro-vision-001:generateContent"
    assert req["headers"]["x-goog-api-key"] == "gm-test"
    parts = req["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "what do you see"}
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == PNG


async def test_gemini_malformed_response(captured_server):
    captured, replies, server = captured_server
    replies["gemini"] = {"candidates": []}
    b = GeminiVisionBackend(api_key="gm-test", base_url=f"http://{server.host}:{server.port}")
    with pytest.raises(UpstreamError):
        await b.adescribe(VisionInput(data=PNG), "x")


# --------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------


def test_providers_require_keys(monkeypatch):
    for var in ("SK_VISION_OPENAI_KEY", "SK_VISION_ANTHROPIC_KEY", "SK_VISION_GEMINI_KEY"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(BackendUnavailableError):
        OpenAIVisionBackend()
    with pytest.raises(BackendUnavailableError):
        AnthropicVisionBackend()
    with pytest.raises(BackendUnavailableError):
        GeminiVisionBackend()


def test_llava_missing_runtime():
    with pytest.raises(BackendUnavailableError) as ei:
        LLaVaBackend()
    assert "pip install" in (ei.value.hint or "")


def test_engine_auto_selects_openai_when_key(monkeypatch):
    monkeypatch.setenv("SK_VISION_OPENAI_KEY", "sk-x")
    monkeypatch.delenv("SK_VISION_ANTHROPIC_KEY", raising=False)
    monkeypatch.delenv("SK_VISION_GEMINI_KEY", raising=False)
    engine = VisionEngine(provider=None)
    assert engine.provider == "openai"


def test_engine_auto_falls_back_to_mock(monkeypatch):
    for var in ("SK_VISION_OPENAI_KEY", "SK_VISION_ANTHROPIC_KEY",
                "SK_VISION_GEMINI_KEY", "SK_VISION_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    engine = VisionEngine()
    assert engine.provider == "mock"


def test_engine_explicit_backend(monkeypatch):
    for var in ("SK_VISION_OPENAI_KEY", "SK_VISION_ANTHROPIC_KEY", "SK_VISION_GEMINI_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SK_VISION_ANTHROPIC_KEY", "ant-x")
    engine = VisionEngine(provider="anthropic")
    assert engine.provider == "anthropic"


def test_engine_explicit_backend_missing_key(monkeypatch):
    for var in ("SK_VISION_OPENAI_KEY", "SK_VISION_ANTHROPIC_KEY", "SK_VISION_GEMINI_KEY"):
        monkeypatch.delenv(var, raising=False)
    engine = VisionEngine(provider="anthropic")
    with pytest.raises(BackendUnavailableError) as ei:
        engine.provider
    assert "SK_VISION_ANTHROPIC_KEY" in (ei.value.hint or "")


def test_engine_unknown_provider():
    engine = VisionEngine(provider="blip")
    with pytest.raises(BackendUnavailableError):
        engine.provider


def test_engine_supported_probe(monkeypatch):
    monkeypatch.setenv("SK_VISION_OPENAI_KEY", "sk-x")
    for var in ("SK_VISION_ANTHROPIC_KEY", "SK_VISION_GEMINI_KEY"):
        monkeypatch.delenv(var, raising=False)
    engine = VisionEngine()
    s = engine.supported()
    assert s["openai"] is True
    assert s["anthropic"] is False
    assert s["mock"] is True


async def test_engine_mock_describe(monkeypatch):
    for var in ("SK_VISION_OPENAI_KEY", "SK_VISION_ANTHROPIC_KEY", "SK_VISION_GEMINI_KEY"):
        monkeypatch.delenv(var, raising=False)
    engine = VisionEngine()
    text = await engine.adescribe(VisionInput(data=PNG), "describe the red square")
    assert "mock vision" in text
    assert "red square" in text
    # deterministic
    assert text == await engine.adescribe(VisionInput(data=PNG), "describe the red square")


def test_sync_describe_outside_loop():
    b = MockVisionBackend()
    out = b.describe(VisionInput(data=PNG), "hello")
    assert out.startswith("[mock vision]")


def test_mock_distinct_images():
    b = MockVisionBackend()

    async def _run():
        a = await b.adescribe(VisionInput(data=PNG), "x")
        c = await b.adescribe(VisionInput(data=make_png(rgb=(0, 0, 255))), "x")
        return a, c

    a, c = asyncio.run(_run())
    assert a != c
