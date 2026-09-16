"""VisionEngine — provider routing for the platform's vision calls."""

from __future__ import annotations

from typing import Any

from features.common.config import FeatureConfig
from features.common.errors import BackendUnavailableError

from .backends import (
    AnthropicVisionBackend,
    GeminiVisionBackend,
    LLaVaBackend,
    MockVisionBackend,
    OpenAIVisionBackend,
    VisionBackend,
    VisionInput,
)

__all__ = ["VisionEngine", "image_to_data_uri"]


def image_to_data_uri(data: bytes, mime: str | None = None) -> str:  # re-export
    from .backends import image_to_data_uri as _fn

    return _fn(data, mime)


class VisionEngine:
    """Picks a provider by SK_VISION_PROVIDER (auto: first key present,
    else local LLaVA if transformers installed, else mock)."""

    def __init__(self, provider: str | None = None, backend: VisionBackend | None = None):
        self._explicit = backend
        cfg = FeatureConfig(prefix="SK_VISION")
        self.provider_name = (provider or cfg.get_str("provider") or "auto").lower()

    def _build(self) -> VisionBackend:
        if self._explicit is not None:
            return self._explicit
        name = self.provider_name
        if name == "mock":
            return MockVisionBackend()
        if name == "openai":
            return OpenAIVisionBackend()
        if name == "anthropic":
            return AnthropicVisionBackend()
        if name == "gemini":
            return GeminiVisionBackend()
        if name == "llava":
            return LLaVaBackend()
        if name == "auto":
            try:
                return OpenAIVisionBackend()
            except BackendUnavailableError:
                pass
            try:
                return AnthropicVisionBackend()
            except BackendUnavailableError:
                pass
            try:
                return GeminiVisionBackend()
            except BackendUnavailableError:
                pass
            try:
                return LLaVaBackend()
            except BackendUnavailableError:
                pass
            return MockVisionBackend()
        raise BackendUnavailableError(f"unknown vision provider {name!r}",
                                      hint="auto|mock|openai|anthropic|gemini|llava")

    @property
    def backend(self) -> VisionBackend:
        return self._build()

    @property
    def provider(self) -> str:
        return self.backend.name

    def supported(self) -> dict[str, bool]:
        """Which providers are usable on this host (keys/models present)."""
        out: dict[str, bool] = {}
        for name, factory in (
            ("openai", OpenAIVisionBackend),
            ("anthropic", AnthropicVisionBackend),
            ("gemini", GeminiVisionBackend),
            ("llava", LLaVaBackend),
        ):
            try:
                factory()
                out[name] = True
            except BackendUnavailableError:
                out[name] = False
        out["mock"] = True
        return out

    async def adescribe(self, image: VisionInput, prompt: str) -> str:
        return await self.backend.adescribe(image, prompt)

    def describe(self, image: VisionInput, prompt: str) -> str:
        return self.backend.describe(image, prompt)
