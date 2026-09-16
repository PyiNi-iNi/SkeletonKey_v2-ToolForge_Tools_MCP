"""CPU-friendly runtimes: llama.cpp (GGUF) and GPT4All."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Sequence

from features.common.errors import BackendUnavailableError
from features.common.openai_compat import ChatMessage

from .base import BackendInfo, GenerationParams, LLMBackend


class LlamaCppBackend(LLMBackend):
    def __init__(self, model_path: str, n_ctx: int = 4096, n_threads: int | None = None):
        try:
            from llama_cpp import Llama  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "llama-cpp-python is not installed",
                hint='pip install "llama-cpp-python>=0.2"',
            ) from exc
        if not model_path:
            raise BackendUnavailableError(
                "llama.cpp backend needs a GGUF path (SK_INF_LLAMACPP_PATH)",
                hint="download a .gguf model first",
            )
        self._llm = Llama(model_path=model_path, n_ctx=n_ctx, n_threads=n_threads,
                          verbose=False)
        self.info = BackendInfo(
            name=model_path.rsplit("/", 1)[-1], family="llamacpp", device="cpu",
            context_size=n_ctx, max_tokens=1024, estimated_tps=18.0,
        )

    async def astream(self, messages: Sequence[ChatMessage],
                      params: GenerationParams | None = None) -> AsyncIterator[str]:
        params = params or GenerationParams()
        prompt = _chat_text(messages)
        kwargs: dict[str, Any] = {
            "max_tokens": min(params.max_tokens or self.info.max_tokens, self.info.max_tokens),
            "temperature": params.temperature if params.temperature is not None else 0.7,
            "stream": True,
        }
        if params.stop:
            kwargs["stop"] = params.stop

        def work() -> list[str]:
            out = []
            for piece in self._llm(prompt, **kwargs):
                out.append(piece["tokens"][0] if isinstance(piece, dict) and piece.get("tokens")
                           else str(piece))
            return out

        for tok in await asyncio.to_thread(work):
            yield tok


class GPT4AllBackend(LLMBackend):
    def __init__(self, model: str = "ggml-model.bin"):
        try:
            from gpt4all import GPT4All  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "gpt4all is not installed",
                hint='pip install "gpt4all>=1.0"',
            ) from exc
        self._gpt4all = GPT4All(model, verbose=False)
        self.info = BackendInfo(name=model.rsplit("/", 1)[-1], family="gpt4all",
                                device="cpu", context_size=2048, max_tokens=512,
                                estimated_tps=10.0)

    async def astream(self, messages: Sequence[ChatMessage],
                      params: GenerationParams | None = None) -> AsyncIterator[str]:
        params = params or GenerationParams()
        prompt = _chat_text(messages)

        def work() -> list[str]:
            return list(self._gpt4all.generate(
                prompt,
                max_tokens=min(params.max_tokens or self.info.max_tokens, self.info.max_tokens),
                temp=params.temperature if params.temperature is not None else 0.7,
            ))

        for tok in await asyncio.to_thread(work):
            yield tok


def _chat_text(messages: Sequence[ChatMessage]) -> str:
    return "\n".join(f"{m.role.upper()}: {m.content or ''}" for m in messages
                     if m.content is not None)
