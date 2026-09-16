"""Bake-off mode — run one prompt through N models and compare.

Async, bounded concurrency; per-model timing (tokens/sec) so the 80 t/s
target is visible per competitor. ``astream`` emits fine-grained events
for live side-by-side rendering in the UI.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

from features.common.errors import FeatureError
from features.common.openai_compat import ChatMessage
from features.inference_engine import InferenceEngine
from features.inference_engine.backends.base import GenerationParams

__all__ = ["BakeOff", "BakeOffEntry"]


async def _anext(aiter: AsyncIterator[Any]) -> Any:
    return await aiter.__anext__()


@dataclass
class BakeOffEntry:
    model: str
    text: str = ""
    tokens: int = 0
    elapsed_sec: float = 0.0
    tokens_per_sec: float = 0.0
    meets_target: bool = False
    ok: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model, "text": self.text, "tokens": self.tokens,
            "elapsed_sec": round(self.elapsed_sec, 6),
            "tokens_per_sec": round(self.tokens_per_sec, 2),
            "meets_target": self.meets_target, "ok": self.ok, "error": self.error,
        }


class BakeOff:
    TARGET_TPS = 80.0

    def __init__(self, engine: InferenceEngine, models: Sequence[str],
                 max_concurrent: int = 4, timeout_sec: float = 300.0):
        self.engine = engine
        self.models = list(models)
        self.max_concurrent = max(1, max_concurrent)
        self.timeout = timeout_sec

    async def _run_one(self, model: str, messages: Sequence[ChatMessage],
                       params: GenerationParams) -> BakeOffEntry:
        entry = BakeOffEntry(model=model)
        t0 = time.monotonic()
        try:
            stream = self.engine.astream(model, messages, params)
            deadline = t0 + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                try:
                    tok = await asyncio.wait_for(_anext(stream), timeout=remaining)
                except StopAsyncIteration:
                    break
                entry.text += tok
                entry.tokens += 1
        except (FeatureError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            entry.ok = False
            entry.error = f"{type(exc).__name__}: {exc}"
        entry.elapsed_sec = time.monotonic() - t0
        if entry.tokens and entry.elapsed_sec > 0:
            entry.tokens_per_sec = entry.tokens / entry.elapsed_sec
            entry.meets_target = entry.tokens_per_sec >= self.TARGET_TPS
        return entry

    async def run(self, prompt: str, params: GenerationParams | None = None,
                  system: str | None = None) -> list[BakeOffEntry]:
        messages = self._messages(prompt, system)
        params = params or GenerationParams()
        sem = asyncio.Semaphore(self.max_concurrent)

        async def gated(model: str) -> BakeOffEntry:
            async with sem:
                return await self._run_one(model, messages, params)

        return list(await asyncio.gather(*(gated(m) for m in self.models)))

    async def astream(self, prompt: str, params: GenerationParams | None = None,
                      system: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """Live events: start/delta/done per model, interleaved as they happen."""
        messages = self._messages(prompt, system)
        params = params or GenerationParams()
        q: "asyncio.Queue[Any]" = asyncio.Queue()
        sentinel: Any = object()

        async def worker(model: str) -> None:
            entry = BakeOffEntry(model=model)
            t0 = time.monotonic()
            await q.put({"type": "bakeoff_start", "model": model})
            try:
                async for tok in self.engine.astream(model, messages, params):
                    entry.text += tok
                    entry.tokens += 1
                    await q.put({"type": "bakeoff_delta", "model": model, "delta": tok})
            except Exception as exc:  # noqa: BLE001 - report per-model failures
                entry.ok = False
                entry.error = f"{type(exc).__name__}: {exc}"
            entry.elapsed_sec = time.monotonic() - t0
            if entry.tokens and entry.elapsed_sec > 0:
                entry.tokens_per_sec = entry.tokens / entry.elapsed_sec
                entry.meets_target = entry.tokens_per_sec >= self.TARGET_TPS
            await q.put({"type": "bakeoff_done", "model": model, **entry.to_dict()})
            await q.put(sentinel)

        yield {"type": "bakeoff_start", "models": list(self.models)}
        tasks = [asyncio.create_task(worker(m)) for m in self.models]
        remaining = len(tasks)
        try:
            while remaining:
                event = await q.get()
                if event is sentinel:
                    remaining -= 1
                    continue
                yield event
        finally:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _messages(self, prompt: str, system: str | None) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        if system:
            out.append(ChatMessage(role="system", content=system))
        out.append(ChatMessage(role="user", content=prompt))
        return out

    @staticmethod
    def render(entries: Sequence[BakeOffEntry]) -> str:
        """Side-by-side plain-text comparison (used by headless UI)."""
        lines = ["| " + " | ".join(e.model for e in entries) + " |",
                 "|" + "---|" * len(entries)]
        lines.append("| tokens | " + " | ".join(str(e.tokens) for e in entries) + " |")
        lines.append("| tok/s | " + " | ".join(
            f"{e.tokens_per_sec:.0f}" if e.tokens else "-" for e in entries) + " |")
        lines.append("| meets 80 t/s | " + " | ".join(
            "yes" if e.meets_target else "no" for e in entries) + " |")
        lines.append("| " + " | ".join("ok" if e.ok else f"ERR {e.error}" for e in entries) + " |")
        for e in entries:
            lines.append("")
            lines.append(f"[{e.model}]")
            lines.append(e.text or f"(error: {e.error})")
        return "\n".join(lines)
