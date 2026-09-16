"""Deterministic MockBackend — the zero-dependency inference runtime.

Streams a reproducible token sequence derived from (model, messages,
seed). Two determinism hooks make it usable as a *test oracle*:

- ``script(tokens_or_text)``: force the next N generations to emit an
  exact sequence (used by api_proxy function-calling tests).
- ``respond_fn``: a callable ``(messages, params) -> str | list[str]``
  evaluated before the deterministic fallback.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any, AsyncIterator, Callable, Sequence

from features.common.openai_compat import ChatMessage

from .base import BackendInfo, GenerationParams, LLMBackend

_WORDS = [
    "the", "vector", "database", "stores", "embeddings", "for", "fast",
    "similarity", "search", "across", "billion", "scale", "indexes",
    "a", "model", "generates", "tokens", "asynchronously", "streaming",
    "at", "eighty", "tokens", "per", "second", "with", "attention",
    "sinks", "retaining", "the", "initial", "context", "boundary",
    "hyde", "expands", "queries", "into", "hypothetical", "documents",
    "before", "retrieval", "so", "answers", "ground", "in", "evidence",
]


class MockBackend(LLMBackend):
    def __init__(self, model: str = "mock-1b", sleep_per_token: float = 0.0,
                 estimated_tps: float = 8000.0):
        self.info = BackendInfo(
            name=model, family="mock", device="cpu",
            context_size=16384, max_tokens=2048, estimated_tps=estimated_tps,
        )
        self._sleep = sleep_per_token
        self._scripts: list[str] = []
        self.respond_fn: Callable[[Sequence[ChatMessage], GenerationParams], Any] | None = None
        self.last_messages: Sequence[ChatMessage] = ()

    def script(self, tokens: str | list[str]) -> "MockBackend":
        self._scripts.append(tokens if isinstance(tokens, str) else "".join(tokens))
        return self

    async def astream(self, messages: Sequence[ChatMessage],
                      params: GenerationParams | None = None) -> AsyncIterator[str]:
        import asyncio

        params = params or GenerationParams()
        self.last_messages = tuple(messages)

        if self._scripts:
            text = self._scripts.pop(0)
            async for tok in _chunked(text, 4):
                if self._sleep:
                    await asyncio.sleep(self._sleep)
                yield tok
            return

        if self.respond_fn is not None:
            forced = self.respond_fn(messages, params)
            if forced is not None:
                text = forced if isinstance(forced, str) else "".join(forced)
                async for tok in _chunked(text, 4):
                    if self._sleep:
                        await asyncio.sleep(self._sleep)
                    yield tok
                return

        seed = _seed_from(self.info.name, messages, params.seed)
        rng = random.Random(seed)
        n = min(params.max_tokens or 24, 8 + rng.randrange(16))
        prompt_tail = (messages[-1].content or "")[-80:]
        produced: list[str] = []
        for i in range(n):
            if i == 0:
                produced.append(" " + _answer_word(rng, prompt_tail))
            else:
                produced.append(" " + _WORDS[rng.randrange(len(_WORDS))])
            token = produced[-1]
            if params.stop and any(s in "".join(produced) for s in params.stop):
                break
            if self._sleep:
                await asyncio.sleep(self._sleep)
            yield token


async def _chunked(text: str, size: int) -> AsyncIterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _seed_from(model: str, messages: Sequence[ChatMessage], seed: int | None) -> int:
    if seed is not None:
        base = seed
    else:
        h = hashlib.blake2b(digest_size=8)
        h.update(model.encode("utf-8"))
        for m in messages:
            h.update(m.role.encode("utf-8"))
            h.update((m.content or "").encode("utf-8"))
        base = int.from_bytes(h.digest(), "big")
    return base


def _answer_word(rng: random.Random, prompt_tail: str) -> str:
    low = prompt_tail.lower()
    table = [
        ("vector", "retrieval"),
        ("database", "index"),
        ("latency", "throughput"),
        ("python", "script"),
        ("json", "object"),
    ]
    for key, word in table:
        if key in low:
            return word
    return _WORDS[rng.randrange(len(_WORDS))]
