"""Image generation backends.

``StableDiffusionBackend`` / ``FluxBackend`` wrap diffusers (lazy);
``PlaygroundBackend`` speaks PlaygroundAI's OpenAI-style images API;
``MockImageBackend`` renders a deterministic hash-driven gradient PNG.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from features.common.config import FeatureConfig
from features.common.errors import BackendUnavailableError, UpstreamError

from .png import write_png

__all__ = [
    "ImageParams",
    "ImageResult",
    "ImageGenBackend",
    "StableDiffusionBackend",
    "FluxBackend",
    "PlaygroundBackend",
    "MockImageBackend",
    "hash_prompt",
]


@dataclass
class ImageParams:
    width: int = 512
    height: int = 512
    steps: int = 20
    guidance: float = 7.5
    seed: int | None = None
    negative_prompt: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageResult:
    image: bytes
    mime: str = "image/png"
    meta: dict[str, Any] = field(default_factory=dict)


def hash_prompt(prompt: str, seed: int | None = None) -> int:
    digest = hashlib.blake2b(prompt.encode("utf-8"), digest_size=8)
    if seed is not None:
        digest.update(seed.to_bytes(8, "big"))
    return int.from_bytes(digest.digest(), "big")


class ImageGenBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    async def aimage(self, prompt: str, params: ImageParams | None = None,
                     progress: AsyncIterator[Any] | None = None) -> ImageResult:
        """Generate one image. ``progress`` (when given) is an async queue
        the backend pushes step events into: ``{"stage": ..., "step": i}``."""


class StableDiffusionBackend(ImageGenBackend):
    name = "stablediffusion"

    def __init__(self, model: str | None = None, device: str | None = None):
        cfg = FeatureConfig(prefix="SK_IMAGEGEN_SD")
        self.model_name = model or cfg.get_str("model") or "runwayml/stable-diffusion-v1-5"
        self.device = device or cfg.get_str("device")
        try:
            from diffusers import StableDiffusionPipeline  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "diffusers is not installed",
                hint='pip install "diffusers>=0.26" torch accelerate',
            ) from exc
        import torch  # type: ignore

        self._torch = torch
        self._pipe = StableDiffusionPipeline.from_pretrained(
            self.model_name, torch_dtype=torch.float16 if (self.device or "cpu") == "cuda"
            else torch.float32)
        if self.device:
            self._pipe.to(self.device)

    async def aimage(self, prompt, params=None, progress=None) -> ImageResult:
        params = params or ImageParams()

        def step_cb(pipe, step, timesteps, callback_kwargs):
            if progress is not None:
                asyncio.get_running_loop().call_soon_threadsafe(
                    progress.put_nowait,
                    {"stage": "denoise", "step": step + 1, "steps": len(timesteps)})
            return callback_kwargs

        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "num_inference_steps": params.steps,
            "guidance_scale": params.guidance,
            "width": params.width,
            "height": params.height,
            "negative_prompt": params.negative_prompt or None,
            "callback_on_step_end": step_cb if progress is not None else None,
        }
        if params.seed is not None:
            kwargs["generator"] = self._torch.Generator("cpu").manual_seed(params.seed)

        def work() -> Any:
            if progress is not None:
                progress.put_nowait({"stage": "model_load", "step": 0})
            img = self._pipe(**kwargs).images[0]
            if progress is not None:
                progress.put_nowait({"stage": "vae", "step": params.steps})
            return img

        img = await asyncio.to_thread(work)
        return _pil_to_png(img)


class FluxBackend(ImageGenBackend):
    name = "flux"

    def __init__(self, model: str | None = None, device: str | None = None):
        cfg = FeatureConfig(prefix="SK_IMAGEGEN_FLUX")
        self.model_name = model or cfg.get_str("model") or "black-forest-labs/FLUX.1-schnell"
        self.device = device or cfg.get_str("device")
        try:
            from diffusers import FluxPipeline  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "diffusers (>=0.30 for Flux) is not installed",
                hint='pip install "diffusers>=0.30" torch accelerate',
            ) from exc
        import torch  # type: ignore

        self._torch = torch
        self._pipe = FluxPipeline.from_pretrained(
            self.model_name, torch_dtype=torch.float16 if (self.device or "cpu") == "cuda"
            else torch.float32)
        if self.device:
            self._pipe.to(self.device)

    async def aimage(self, prompt, params=None, progress=None) -> ImageResult:
        params = params or ImageParams()

        def step_cb(pipe, step, timesteps, callback_kwargs):
            if progress is not None:
                asyncio.get_running_loop().call_soon_threadsafe(
                    progress.put_nowait,
                    {"stage": "denoise", "step": step + 1, "steps": params.steps})
            return callback_kwargs

        def work() -> Any:
            if progress is not None:
                progress.put_nowait({"stage": "model_load", "step": 0})
            img = self._pipe(
                prompt=prompt,
                num_inference_steps=params.steps,
                width=params.width, height=params.height,
                callback_on_step_end=step_cb if progress is not None else None,
            ).images[0]
            if progress is not None:
                progress.put_nowait({"stage": "vae", "step": params.steps})
            return img

        return _pil_to_png(await asyncio.to_thread(work))


class PlaygroundBackend(ImageGenBackend):
    """PlaygroundAI images API (OpenAI-style, key-only)."""

    name = "playground"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, timeout: float = 120.0):
        cfg = FeatureConfig(prefix="SK_IMAGEGEN_PLAYGROUND")
        self.api_key = api_key or cfg.get_str("key")
        self.base_url = (base_url or cfg.get_str("url")
                         or "https://api.playground.ai/v1").rstrip("/")
        self.model = model or cfg.get_str("model") or "playground-v2.5"
        self.timeout = timeout
        if not self.api_key:
            raise BackendUnavailableError(
                "PlaygroundAI needs an API key", hint="set SK_IMAGEGEN_PLAYGROUND_KEY")

    def build_payload(self, prompt: str, params: ImageParams) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt": prompt,
            "width": params.width,
            "height": params.height,
            "steps": params.steps,
            "num_images": 1,
            "guidance_scale": params.guidance,
            "negative_prompt": params.negative_prompt or None,
        }

    async def aimage(self, prompt, params=None, progress=None) -> ImageResult:
        params = params or ImageParams()
        if progress is not None:
            progress.put_nowait({"stage": "queue", "step": 0})

        def work() -> bytes:
            req = urllib.request.Request(
                f"{self.base_url}/images/generations",
                data=json.dumps(self.build_payload(prompt, params)).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.api_key}"},
                method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raise UpstreamError(f"HTTP {exc.code} from PlaygroundAI") from exc
            except urllib.error.URLError as exc:
                raise UpstreamError(f"network error: {exc.reason}") from exc
            b64 = data["data"][0]["b64_json"]
            return base64.b64decode(b64)

        image = await asyncio.to_thread(work)
        if progress is not None:
            progress.put_nowait({"stage": "done", "step": params.steps})
        return ImageResult(image=image, meta={"provider": "playground", "model": self.model})


class MockImageBackend(ImageGenBackend):
    """Deterministic hash-driven gradient PNG — zero dependencies."""

    name = "mock"

    def __init__(self, model: str = "mock-imagegen"):
        self.model = model

    async def aimage(self, prompt, params=None, progress=None) -> ImageResult:
        params = params or ImageParams()
        if progress is not None:
            progress.put_nowait({"stage": "queue", "step": 0})
            progress.put_nowait({"stage": "model_load", "step": 0})
        h = hash_prompt(prompt, params.seed)
        r0, g0, b0 = (h & 0xFF), ((h >> 8) & 0xFF), ((h >> 16) & 0xFF)
        r1, g1, b1 = (h >> 24) & 0xFF, (h >> 32) & 0xFF, (h >> 40) & 0xFF
        w, ht = min(params.width, 256), min(params.height, 256)  # keep mock fast

        def pixel(x: int, y: int) -> tuple[int, int, int, int]:
            fx, fy = x / max(1, w - 1), y / max(1, ht - 1)
            wave = math.sin(fx * 6.28 + (h % 7)) * 20
            return (
                int(r0 + (r1 - r0) * fx + wave),
                int(g0 + (g1 - g0) * fy - wave),
                int(b0 + (b1 - b0) * (fx + fy) / 2),
                255,
            )

        steps = max(1, min(params.steps, 8))  # simulated steps for event parity
        for i in range(steps):
            if progress is not None:
                progress.put_nowait({"stage": "denoise", "step": i + 1, "steps": steps})
                await asyncio.sleep(0)  # yield so consumers see live events
        if progress is not None:
            progress.put_nowait({"stage": "vae", "step": steps})

        image = await asyncio.to_thread(write_png, w, ht, pixel)
        meta = {"provider": "mock", "model": self.model, "width": w, "height": ht,
                "seed": h % (2 ** 32), "prompt": prompt}
        if progress is not None:
            progress.put_nowait({"stage": "done", "step": steps})
        return ImageResult(image=image, meta=meta)


def _pil_to_png(img: Any) -> ImageResult:
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return ImageResult(image=buf.getvalue(),
                       meta={"width": img.width, "height": img.height})
