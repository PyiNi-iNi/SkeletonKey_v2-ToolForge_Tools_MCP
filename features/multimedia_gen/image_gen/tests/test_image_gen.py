"""Integration tests for multimedia_gen/image_gen (zero heavy deps)."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from features.common.errors import BackendUnavailableError, UpstreamError
from features.common.http import HttpServer
from features.multimedia_gen.image_gen import (
    ImageGenPipeline,
    ImageParams,
    MockImageBackend,
    PlaygroundBackend,
    make_image_backend,
)
from features.multimedia_gen.image_gen.png import parse_png_header, write_png


# --------------------------------------------------------------------------
# PNG codec
# --------------------------------------------------------------------------


@pytest.mark.parametrize("w,h", [(1, 1), (8, 8), (33, 17), (64, 64)])
def test_png_roundtrip(w, h):
    data = write_png(w, h, lambda x, y: (x % 256, y % 256, 255 - (x + y) % 256, 255))
    head = parse_png_header(data)
    assert head["width"] == w and head["height"] == h
    assert head["bitdepth"] == 8
    assert head["colortype"] == 6  # RGBA


def test_png_rejects_garbage():
    with pytest.raises(ValueError):
        parse_png_header(b"not a png at all")


def test_png_rejects_bad_dimensions():
    with pytest.raises(ValueError):
        write_png(0, 5, lambda x, y: (0, 0, 0, 255))


def test_png_deterministic():
    a = write_png(16, 16, lambda x, y: (x, y, 0, 255))
    b = write_png(16, 16, lambda x, y: (x, y, 0, 255))
    assert a == b


# --------------------------------------------------------------------------
# Mock backend
# --------------------------------------------------------------------------


async def test_mock_deterministic():
    b = MockImageBackend()
    r1 = await b.aimage("a lighthouse", ImageParams(seed=7))
    r2 = await b.aimage("a lighthouse", ImageParams(seed=7))
    assert r1.image == r2.image
    parse_png_header(r1.image)


async def test_mock_prompt_sensitive():
    b = MockImageBackend()
    r1 = await b.aimage("lighthouse")
    r2 = await b.aimage("volcano")
    assert r1.image != r2.image


async def test_mock_meta_shape():
    b = MockImageBackend()
    r = await b.aimage("x", ImageParams(width=128, height=96, seed=1))
    assert r.mime == "image/png"
    assert r.meta["width"] == 128 and r.meta["height"] == 96
    assert r.meta["seed"] is not None


# --------------------------------------------------------------------------
# Streaming pipeline
# --------------------------------------------------------------------------


async def test_astream_image_event_order():
    pipe = ImageGenPipeline(MockImageBackend())
    events = []
    async for ev in pipe.astream_image("streaming lighthouse", ImageParams(steps=4)):
        events.append(ev)
    stages = [e["stage"] for e in events]
    assert stages[0] in {"queue", "model_load"}
    assert "denoise" in stages
    assert stages[-1] == "done"
    # progress monotonically increases across denoise events
    ps = [e["p"] for e in events if e["stage"] == "denoise"]
    assert ps == sorted(ps)
    assert ps[-1] == 1.0
    # final image is a valid PNG
    parse_png_header(events[-1]["image"])
    assert events[-1]["mime"] == "image/png"


async def test_astream_image_steps_count():
    pipe = ImageGenPipeline(MockImageBackend())
    events = [e async for e in pipe.astream_image("x", ImageParams(steps=5))]
    denoise = [e for e in events if e["stage"] == "denoise"]
    assert len(denoise) == 5
    assert denoise[-1]["step"] == 5 and denoise[-1]["steps"] == 5


async def test_generate_returns_result():
    pipe = ImageGenPipeline(MockImageBackend())
    result = await pipe.generate("simple", ImageParams(steps=2))
    assert result.image and result.meta["provider"] == "mock"


async def test_pipeline_error_propagates():
    class BoomBackend(MockImageBackend):
        async def aimage(self, prompt, params=None, progress=None):
            raise UpstreamError("gpu oom")

    pipe = ImageGenPipeline(BoomBackend())
    with pytest.raises(UpstreamError):
        async for _ in pipe.astream_image("x"):
            pass


# --------------------------------------------------------------------------
# Playground backend (wire format via local mock server)
# --------------------------------------------------------------------------


async def test_playground_wire_format():
    captured: list[dict] = []
    png_b64 = base64.b64encode(write_png(8, 8, lambda x, y: (9, 9, 9, 255))).decode()

    async def handler(req):
        captured.append({"headers": req.headers, "body": req.json()})
        from features.common import json_response

        return json_response({"data": [{"b64_json": png_b64}]})

    server = HttpServer(handler, host="127.0.0.1", port=0)
    await server.start()
    try:
        b = PlaygroundBackend(api_key="pg-test", base_url=f"http://{server.host}:{server.port}")
        result = await b.aimage("a castle", ImageParams(width=256, height=256, steps=10))
        assert result.image == base64.b64decode(png_b64)
        req = captured[0]
        assert req["headers"]["authorization"] == "Bearer pg-test"
        assert req["body"]["prompt"] == "a castle"
        assert req["body"]["width"] == 256
        assert req["body"]["steps"] == 10
    finally:
        await server.stop()


async def test_playground_upstream_error():
    async def handler(req):
        from features.common import json_response

        return json_response({"error": {"message": "quota"}}, status=429)

    server = HttpServer(handler, host="127.0.0.1", port=0)
    await server.start()
    try:
        b = PlaygroundBackend(api_key="k", base_url=f"http://{server.host}:{server.port}")
        with pytest.raises(UpstreamError) as ei:
            await b.aimage("x")
        assert "429" in ei.value.message
    finally:
        await server.stop()


def test_playground_requires_key():
    with pytest.raises(BackendUnavailableError) as ei:
        PlaygroundBackend(api_key=None)
    assert "SK_IMAGEGEN_PLAYGROUND_KEY" in (ei.value.hint or "")


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def test_auto_falls_back_to_mock(monkeypatch):
    monkeypatch.delenv("SK_IMAGEGEN_BACKEND", raising=False)
    monkeypatch.delenv("SK_IMAGEGEN_PLAYGROUND_KEY", raising=False)
    # diffusers not installed in CI
    assert make_image_backend("auto").name == "mock"


def test_explicit_sd_missing(monkeypatch):
    with pytest.raises(BackendUnavailableError) as ei:
        make_image_backend("stablediffusion")
    assert "diffusers" in ei.value.hint


def test_explicit_unknown():
    with pytest.raises(BackendUnavailableError):
        make_image_backend("midjourney")
