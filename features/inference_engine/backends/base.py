"""LLM backend contract.

All backends stream tokens as ``AsyncIterator[str]``; ``generate`` is the
blocking-collect convenience. Params are OpenAI-style.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Sequence

from features.common.openai_compat import ChatMessage

__all__ = ["BackendInfo", "GenerationParams", "LLMBackend"]


@dataclass(frozen=True)
class BackendInfo:
    name: str                      # registry id (model id or alias)
    family: str = "unknown"        # llama2 | mistral | falcon | mock | ...
    device: str = "cpu"            # cpu | cuda | mps
    quantization: str | None = None
    context_size: int = 4096
    max_tokens: int = 1024
    estimated_tps: float = 80.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "family": self.family, "device": self.device,
            "quantization": self.quantization, "context_size": self.context_size,
            "max_tokens": self.max_tokens, "estimated_tps": self.estimated_tps,
            **self.extra,
        }


@dataclass
class GenerationParams:
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None
    seed: int | None = None

    @classmethod
    def from_request(cls, req: Any) -> "GenerationParams":
        stop = req.stop
        if isinstance(stop, str):
            stop = [stop]
        return cls(
            temperature=req.temperature,
            top_p=req.top_p,
            max_tokens=req.effective_max_tokens,
            stop=stop,
            seed=req.seed,
        )


def _stop_words_hit(token: str, stop: list[str] | None) -> bool:
    return bool(stop) and any(token.endswith(s) or s in token for s in stop)


class LLMBackend(ABC):
    info: BackendInfo

    @abstractmethod
    def astream(self, messages: Sequence[ChatMessage],
                params: GenerationParams | None = None) -> AsyncIterator[str]:
        """Yield decoded tokens (strings) one at a time."""

    async def generate(self, messages: Sequence[ChatMessage],
                       params: GenerationParams | None = None) -> str:
        out: list[str] = []
        async for tok in self.astream(messages, params):
            out.append(tok)
        return "".join(out)

    async def aclose(self) -> None:
        return None

    def close(self) -> None:
        try:
            asyncio.get_running_loop().create_task(self.aclose())
        except RuntimeError:
            pass
