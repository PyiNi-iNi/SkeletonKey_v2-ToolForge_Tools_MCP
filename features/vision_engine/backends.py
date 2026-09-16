"""Vision provider backends.

Each backend: ``async adescribe(image, prompt) -> str``. Remote providers
POST to a configurable base URL (defaulting to the public APIs) using
stdlib urllib in a worker thread — no aiohttp dependency.
"""

from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from features.common.config import FeatureConfig
from features.common.errors import BackendUnavailableError, UpstreamError

__all__ = [
    "VisionInput",
    "VisionBackend",
    "OpenAIVisionBackend",
    "AnthropicVisionBackend",
    "GeminiVisionBackend",
    "LLaVaBackend",
    "MockVisionBackend",
    "image_to_data_uri",
    "mime_for_bytes",
]

_MIME_BY_HEAD = {
    b"\x89PNG": "image/png",
    b"\xff\xd8": "image/jpeg",
    b"GIF8": "image/gif",
    b"RIFF": "image/webp",
    b"BM": "image/bmp",
}


def mime_for_bytes(data: bytes) -> str:
    for head, mime in _MIME_BY_HEAD.items():
        if data.startswith(head):
            if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                return "image/webp"
            return mime
    return "image/png"


def image_to_data_uri(data: bytes, mime: str | None = None) -> str:
    mime = mime or mime_for_bytes(data)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


@dataclass
class VisionInput:
    """Image as bytes (preferred) or a path (lazy read)."""

    data: bytes | None = None
    path: str | None = None
    mime: str | None = None

    @classmethod
    def from_path(cls, path: str) -> "VisionInput":
        with open(path, "rb") as fh:
            return cls(data=fh.read(), path=path)

    def bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        if self.path:
            return VisionInput.from_path(self.path).data  # type: ignore[return-value]
        raise BackendUnavailableError("VisionInput has neither data nor path")

    def data_uri(self) -> str:
        return image_to_data_uri(self.bytes(), self.mime)


class VisionBackend(ABC):
    name: str = "abstract"

    @abstractmethod
    async def adescribe(self, image: VisionInput, prompt: str) -> str: ...

    def describe(self, image: VisionInput, prompt: str) -> str:
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.adescribe(image, prompt))
        raise RuntimeError("describe() called inside a running loop; use adescribe()")


def _http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str],
                    timeout: float = 60.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:  # noqa: BLE001
            pass
        raise UpstreamError(f"HTTP {exc.code} from {url}", detail=body) from exc
    except urllib.error.URLError as exc:
        raise UpstreamError(f"network error calling {url}: {exc.reason}") from exc


class OpenAIVisionBackend(VisionBackend):
    """GPT-4o / gpt-4-vision via the OpenAI chat completions wire format."""

    name = "openai"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, timeout: float = 60.0):
        cfg = FeatureConfig(prefix="SK_VISION_OPENAI")
        self.api_key = api_key or cfg.get_str("key")
        self.base_url = (base_url or cfg.get_str("url") or "https://api.openai.com/v1").rstrip("/")
        self.model = model or cfg.get_str("model") or "gpt-4o"
        self.timeout = timeout
        if not self.api_key:
            raise BackendUnavailableError(
                "OpenAI vision needs an API key", hint="set SK_VISION_OPENAI_KEY")

    def build_payload(self, image: VisionInput, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image.data_uri()}},
                ],
            }],
            "max_tokens": 512,
        }

    async def adescribe(self, image: VisionInput, prompt: str) -> str:
        payload = self.build_payload(image, prompt)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        import asyncio

        data = await asyncio.to_thread(
            _http_post_json, f"{self.base_url}/chat/completions", payload, headers, self.timeout)
        return data["choices"][0]["message"]["content"]


class AnthropicVisionBackend(VisionBackend):
    """Claude 3 (haiku/sonnet/opus) via the /v1/messages wire format."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, timeout: float = 60.0):
        cfg = FeatureConfig(prefix="SK_VISION_ANTHROPIC")
        self.api_key = api_key or cfg.get_str("key")
        self.base_url = (base_url or cfg.get_str("url")
                         or "https://api.anthropic.com").rstrip("/")
        self.model = model or cfg.get_str("model") or "claude-3-opus-20240229"
        self.timeout = timeout
        if not self.api_key:
            raise BackendUnavailableError(
                "Anthropic vision needs an API key", hint="set SK_VISION_ANTHROPIC_KEY")

    def build_payload(self, image: VisionInput, prompt: str) -> dict[str, Any]:
        raw = image.bytes()
        return {
            "model": self.model,
            "max_tokens": 512,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image",
                     "source": {"type": "base64",
                                "media_type": image.mime or mime_for_bytes(raw),
                                "data": base64.b64encode(raw).decode("ascii")}},
                ],
            }],
        }

    async def adescribe(self, image: VisionInput, prompt: str) -> str:
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        import asyncio

        data = await asyncio.to_thread(
            _http_post_json, f"{self.base_url}/v1/messages",
            self.build_payload(image, prompt), headers, self.timeout)
        return "".join(b.get("text", "") for b in data.get("content", []))


class GeminiVisionBackend(VisionBackend):
    """Gemini Pro Vision via generateContent."""

    name = "gemini"

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, timeout: float = 60.0):
        cfg = FeatureConfig(prefix="SK_VISION_GEMINI")
        self.api_key = api_key or cfg.get_str("key")
        self.base_url = (base_url or cfg.get_str("url")
                         or "https://generativelanguage.googleapis.com").rstrip("/")
        self.model = model or cfg.get_str("model") or "gemini-pro-vision"
        self.timeout = timeout
        if not self.api_key:
            raise BackendUnavailableError(
                "Gemini vision needs an API key", hint="set SK_VISION_GEMINI_KEY")

    def build_payload(self, image: VisionInput, prompt: str) -> dict[str, Any]:
        raw = image.bytes()
        return {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": image.mime or mime_for_bytes(raw),
                                     "data": base64.b64encode(raw).decode("ascii")}},
                ],
            }],
        }

    async def adescribe(self, image: VisionInput, prompt: str) -> str:
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self.api_key}
        import asyncio

        data = await asyncio.to_thread(_http_post_json, url, self.build_payload(image, prompt),
                                       headers, self.timeout)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise UpstreamError("malformed Gemini response", detail=str(data)) from exc


class LLaVaBackend(VisionBackend):
    """Local LLaVA via transformers (lazy)."""

    name = "llava"

    def __init__(self, model: str | None = None, device: str | None = None):
        cfg = FeatureConfig(prefix="SK_VISION_LLAVA")
        self.model_name = model or cfg.get_str("model") or "llava-hf/llava-1.5-7b-hf"
        self.device = device or cfg.get_str("device")
        try:
            import torch  # type: ignore
            import transformers  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "transformers/torch are not installed",
                hint='pip install "transformers>=4.40" torch pillow',
            ) from exc
        self._torch = torch
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(self.model_name)
        self._processor = transformers.AutoProcessor.from_pretrained(self.model_name)
        self._model = transformers.LlavaForConditionalGeneration.from_pretrained(
            self.model_name, torch_dtype=torch.float16 if (device or "cpu") == "cuda"
            else torch.float32,
        )
        if self.device:
            self._model.to(self.device)
        self._model.eval()

    async def adescribe(self, image: VisionInput, prompt: str) -> str:
        import asyncio
        from PIL import Image  # type: ignore

        return await asyncio.to_thread(self._describe_sync, image, prompt, Image)

    def _describe_sync(self, image: VisionInput, prompt: str, Image: Any) -> str:
        img = Image.open(io.BytesIO(image.bytes())).convert("RGB")
        inputs = self._processor(images=img, text=prompt, return_tensors="pt")
        if self.device:
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with self._torch.no_grad():
            out = self._model.generate(**inputs, max_new_tokens=256)
        return self._tokenizer.decode(out[0], skip_special_tokens=True)


class MockVisionBackend(VisionBackend):
    """Deterministic vision for tests/air-gapped: describes from prompt + image hash."""

    name = "mock"

    def __init__(self, model: str = "mock-vision"):
        self.model = model

    async def adescribe(self, image: VisionInput, prompt: str) -> str:
        digest = base64.b64encode(
            __import__("hashlib").blake2b(image.bytes(), digest_size=4).digest()
        ).decode("ascii")[:6]
        words = [w for w in prompt.lower().split() if w.isalpha()][:4]
        subject = " ".join(words) or "the image"
        return (f"[mock vision] describing {subject}: a {digest}-fingerprinted image "
                f"matching the prompt '{prompt.strip()}' (no local model installed).")
