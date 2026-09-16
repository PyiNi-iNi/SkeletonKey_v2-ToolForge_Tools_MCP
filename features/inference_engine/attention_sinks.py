"""Attention Sinks — bounded context for long-running streams.

Based on the "Streaming LLM" observation (Xiao et al. 2023): keeping the
initial "sink" tokens plus a sliding window of recent tokens preserves
perplexity far better than a naive sliding window alone. Here we operate
at the token-string level so any backend (including the mock) can use it
to bound what gets re-sent as context on long conversations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable


@dataclass
class AttentionSinkManager:
    """Retains the first ``sink_size`` tokens + the last ``window`` tokens."""

    sink_size: int = 4
    window: int = 512

    def __init__(self, sink_size: int = 4, window: int = 512):
        if sink_size < 0 or window < 0:
            raise ValueError("sink_size and window must be >= 0")
        self.sink_size = sink_size
        self.window = window
        self._sink: list[str] = []
        self._recent: list[str] = []
        self.total_seen = 0

    def add(self, tokens: Iterable[str]) -> None:
        for tok in tokens:
            self.total_seen += 1
            if len(self._sink) < self.sink_size:
                self._sink.append(tok)
            else:
                self._recent.append(tok)
                if len(self._recent) > self.window:
                    self._recent.pop(0)

    def add_token(self, token: str) -> None:
        self.add([token])

    def context(self) -> list[str]:
        return self._sink + self._recent

    @property
    def is_bounded(self) -> bool:
        return len(self.context()) <= self.sink_size + self.window

    @property
    def evicted(self) -> int:
        return max(0, self.total_seen - len(self.context()))

    def reset(self) -> None:
        self._sink.clear()
        self._recent.clear()
        self.total_seen = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sink_size": self.sink_size,
            "window": self.window,
            "total_seen": self.total_seen,
            "context_len": len(self.context()),
            "evicted": self.evicted,
        }


class StreamingContext:
    """Wrap a token stream, maintaining the sink manager as tokens flow.

    Yields ``(token, context_length)`` pairs so callers can decide when
    to re-prompt the model with a compacted context.
    """

    def __init__(self, manager: AttentionSinkManager | None = None):
        self.manager = manager or AttentionSinkManager()

    async def stream(self, tokens: AsyncIterator[str]) -> AsyncIterator[tuple[str, int]]:
        async for tok in tokens:
            self.manager.add_token(tok)
            yield tok, len(self.manager.context())

    def compact(self) -> str:
        """Join the retained context (sinks + window) for re-prompting."""
        return "".join(self.manager.context())
