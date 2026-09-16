"""ImageGenPipeline — streaming image generation with live progress events.

``astream_image`` yields:
    {"stage": "queue"|"model_load"|"denoise", "step": i, "steps": N, "p": 0-1}
    {"stage": "done", "image": <png bytes>, "meta": {...}}

so UIs can animate the denoising loop while the model works.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from features.common.config import FeatureConfig
from features.common.errors import BackendUnavailableError

from .backends import (
    FluxBackend,
    ImageGenBackend,
    ImageParams,
    ImageResult,
    MockImageBackend,
    PlaygroundBackend,
    StableDiffusionBackend,
)

__all__ = ["ImageGenPipeline", "make_image_backend"]


def make_image_backend(kind: str | None = None) -> ImageGenBackend:
    cfg = FeatureConfig(prefix="SK_IMAGEGEN")
    name = (kind or cfg.get_str("backend") or "auto").lower()
    if name == "mock":
        return MockImageBackend()
    if name == "stablediffusion":
        return StableDiffusionBackend()
    if name == "flux":
        return FluxBackend()
    if name == "playground":
        return PlaygroundBackend()
    if name == "auto":
        for candidate in ("stablediffusion", "flux", "playground"):
            try:
                return make_image_backend(candidate)
            except BackendUnavailableError:
                continue
        return MockImageBackend()
    raise BackendUnavailableError(f"unknown image backend {name!r}",
                                  hint="auto|mock|stablediffusion|flux|playground")


class ImageGenPipeline:
    def __init__(self, backend: ImageGenBackend | None = None):
        self.backend = backend or make_image_backend()

    async def astream_image(self, prompt: str,
                            params: ImageParams | None = None) -> AsyncIterator[dict[str, Any]]:
        params = params or ImageParams()
        q: "asyncio.Queue[Any]" = asyncio.Queue()
        sentinel: Any = object()

        async def run() -> ImageResult:
            try:
                return await self.backend.aimage(prompt, params, progress=q)
            finally:
                q.put_nowait(sentinel)  # unblocks the consumer exactly once

        task = asyncio.create_task(run())
        try:
            while True:
                item = await q.get()
                if item is sentinel:
                    break
                event = dict(item)
                if item.get("stage") == "denoise" and item.get("steps"):
                    event["p"] = round(item["step"] / item["steps"], 4)
                yield event
            result = await task  # re-raises backend errors
            yield {"stage": "done", "image": result.image, "mime": result.mime,
                   "meta": result.meta}
        except BaseException:
            task.cancel()
            raise

    async def generate(self, prompt: str,
                       params: ImageParams | None = None) -> ImageResult:
        return await self.backend.aimage(prompt, params)
