"""Async streaming helpers.

The platform target is **80 tokens/sec** on chat streams (see the
``TokenClock`` target). Everything streaming is an ``AsyncIterator``;
helpers here measure, buffer, and adapt streams without depending on any
third-party package.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, TypeVar

__all__ = [
    "TARGET_TOKENS_PER_SEC",
    "TokenClock",
    "TimingReport",
    "collect_stream",
    "stream_with_timing",
    "limit_stream",
    "chunk_bytes",
    "merge_stream_errors",
]

TARGET_TOKENS_PER_SEC = 80.0

T = TypeVar("T")


@dataclass
class TimingReport:
    count: int = 0
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    target_tps: float = TARGET_TOKENS_PER_SEC

    @property
    def elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    @property
    def tokens_per_sec(self) -> float:
        if self.elapsed <= 0:
            return float("inf") if self.count else 0.0
        return self.count / self.elapsed

    @property
    def meets_target(self) -> bool:
        return self.tokens_per_sec >= self.target_tps

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "elapsed_sec": round(self.elapsed, 6),
            "tokens_per_sec": round(self.tokens_per_sec, 3) if self.count else 0.0,
            "meets_target": self.meets_target,
            "target_tps": self.target_tps,
        }


class TokenClock:
    """Ticks per emitted item; reports throughput against the 80 t/s target."""

    def __init__(self, target_tps: float = TARGET_TOKENS_PER_SEC):
        self.report = TimingReport(target_tps=target_tps)
        self._started = False

    def start(self) -> None:
        self.report.started_at = time.monotonic()
        self._started = True

    def tick(self, n: int = 1) -> None:
        if not self._started:
            self.start()
        self.report.count += n

    def stop(self) -> TimingReport:
        self.report.finished_at = time.monotonic()
        return self.report


async def collect_stream(stream: AsyncIterator[T]) -> tuple[list[T], TimingReport]:
    """Drain a stream, returning items and their timing report."""
    clock = TokenClock()
    items: list[T] = []
    async for item in stream:
        items.append(item)
        clock.tick()
    return items, clock.stop()


async def stream_with_timing(
    stream: AsyncIterator[T], target_tps: float = TARGET_TOKENS_PER_SEC
) -> AsyncIterator[tuple[T, TimingReport]]:
    """Yield each item paired with a *snapshot* of timing so far."""
    clock = TokenClock(target_tps=target_tps)
    async for item in stream:
        clock.tick()
        yield item, TimingReport(
            count=clock.report.count,
            started_at=clock.report.started_at,
            target_tps=target_tps,
        )


async def limit_stream(stream: AsyncIterator[T], max_items: int) -> AsyncIterator[T]:
    """Yield at most ``max_items`` items, then stop consuming."""
    if max_items <= 0:
        return
    n = 0
    async for item in stream:
        yield item
        n += 1
        if n >= max_items:
            return


def chunk_bytes(data: bytes, size: int = 4096) -> list[bytes]:
    """Split bytes into bounded chunks (for chunked audio/image streams)."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    return [data[i : i + size] for i in range(0, len(data), size)]


async def merge_stream_errors(
    stream: AsyncIterator[T], on_error: Any = None
) -> AsyncIterator[T]:
    """Pass a stream through, converting exceptions into a final sentinel.

    ``on_error(exc)`` may return a replacement final item; otherwise the
    exception propagates. Lets long-lived consumers fail cleanly.
    """
    try:
        async for item in stream:
            yield item
    except Exception as exc:  # noqa: BLE001 - boundary of a remote stream
        if on_error is not None:
            replacement = on_error(exc)
            if replacement is not None:
                yield replacement
        else:
            raise
