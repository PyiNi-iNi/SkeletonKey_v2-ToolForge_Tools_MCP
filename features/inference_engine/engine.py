"""InferenceEngine — model registry + streaming facade for the platform.

Backends register under their ``info.name`` (a model id or alias). The
api_proxy maps its ``/v1/models`` from here, and every feature that needs
a completion goes through ``astream``/``generate`` so streaming, timing,
and the 80 tok/s benchmark live in exactly one place.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Sequence

from features.common.errors import NotFoundError
from features.common.openai_compat import ChatMessage
from features.common.streaming import TokenClock

from .backends.base import BackendInfo, GenerationParams, LLMBackend
from .backends.mock import MockBackend

__all__ = ["InferenceEngine", "SpeedReport", "TARGET_TOKENS_PER_SEC"]

TARGET_TOKENS_PER_SEC = 80.0


@dataclass
class SpeedReport:
    model: str
    tokens: int
    elapsed_sec: float
    tokens_per_sec: float
    meets_target: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "tokens": self.tokens,
            "elapsed_sec": round(self.elapsed_sec, 6),
            "tokens_per_sec": round(self.tokens_per_sec, 2),
            "meets_target": self.meets_target,
        }


class InferenceEngine:
    def __init__(self, default: LLMBackend | None = None):
        self._backends: dict[str, LLMBackend] = {}
        self._default: LLMBackend = default or MockBackend()
        self._backends[self._default.info.name] = self._default

    # -- registry ------------------------------------------------------------

    def register(self, backend: LLMBackend, alias: str | None = None) -> "InferenceEngine":
        self._backends[backend.info.name] = backend
        if alias:
            self._backends[alias] = backend
        return self

    def default(self) -> LLMBackend:
        return self._default

    def set_default(self, name: str) -> None:
        self._default = self.resolve(name)

    def resolve(self, name: str | None) -> LLMBackend:
        if name is None:
            return self._default
        backend = self._backends.get(name)
        if backend is None:
            raise NotFoundError(f"model {name!r} is not registered",
                                hint=f"available: {self.model_ids()}")
        return backend

    def model_ids(self) -> list[str]:
        seen: dict[str, str] = {}
        for alias, backend in self._backends.items():
            seen.setdefault(backend.info.name, alias)
        return sorted(seen)

    def info(self, name: str | None = None) -> BackendInfo:
        return self.resolve(name).info

    def model_cards(self) -> list[dict[str, Any]]:
        seen: dict[int, LLMBackend] = {}
        for b in self._backends.values():
            seen[id(b)] = b
        return [b.info.to_dict() for b in seen.values()]

    # -- generation -----------------------------------------------------------

    async def astream(self, model: str | None, messages: Sequence[ChatMessage],
                      params: GenerationParams | None = None) -> AsyncIterator[str]:
        backend = self.resolve(model)
        async for tok in backend.astream(messages, params):
            yield tok

    async def generate(self, model: str | None, messages: Sequence[ChatMessage],
                       params: GenerationParams | None = None) -> str:
        backend = self.resolve(model)
        return await backend.generate(messages, params)

    def close(self) -> None:
        seen: set[int] = set()
        for b in self._backends.values():
            if id(b) not in seen:
                seen.add(id(b))
                b.close()

    # -- benchmark ------------------------------------------------------------

    async def benchmark(self, model: str | None, messages: Sequence[ChatMessage] | None = None,
                        max_tokens: int = 128) -> SpeedReport:
        backend = self.resolve(model)
        messages = messages or [ChatMessage(role="user", content="Count from one to twenty.")]
        clock = TokenClock(target_tps=TARGET_TOKENS_PER_SEC)
        t0 = time.monotonic()
        n = 0
        async for _ in backend.astream(messages, GenerationParams(max_tokens=max_tokens)):
            n += 1
        elapsed = time.monotonic() - t0
        tps = n / elapsed if elapsed > 0 else float("inf")
        return SpeedReport(
            model=backend.info.name, tokens=n, elapsed_sec=elapsed,
            tokens_per_sec=tps, meets_target=tps >= TARGET_TOKENS_PER_SEC,
        )
