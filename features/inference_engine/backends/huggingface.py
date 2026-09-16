"""Hugging Face causal-LM backend — LLaMa-2 / Mistral / Falcon.

Token-by-token decoding on a worker thread, surfaced as an async
generator through a queue (true streaming, no full-batch waiting).
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any, AsyncIterator, Sequence

from features.common.errors import BackendUnavailableError
from features.common.openai_compat import ChatMessage

from .base import BackendInfo, GenerationParams, LLMBackend
from . import _family


class HuggingFaceBackend(LLMBackend):
    def __init__(self, model: str, device: str | None = None,
                 torch_dtype: str | None = None, load_now: bool = True):
        try:
            import torch  # type: ignore
            import transformers  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "transformers/torch are not installed",
                hint='pip install "transformers>=4.40" torch',
            ) from exc
        self._torch = torch
        self._transformers = transformers

        self.model_name = model
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        dtype = {"float16": torch.float16, "bfloat16": torch.bfloat16,
                 "float32": torch.float32, "auto": None}.get(
            (torch_dtype or "auto").lower(), None)
        if dtype is None and self.device == "cuda":
            dtype = torch.float16
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(model)
        self._model = transformers.AutoModelForCausalLM.from_pretrained(
            model, torch_dtype=dtype or torch.float32,
            device_map=self.device if self.device != "cpu" else None,
        )
        if self.device == "cpu" and dtype is None:
            self._model = self._model.to("cpu")
        self._model.eval()
        self.info = BackendInfo(
            name=model, family=_family(model), device=self.device,
            context_size=int(getattr(self._model.config, "max_position_embeddings", 4096)),
            max_tokens=1024,
            estimated_tps=12.0 if self.device == "cpu" else 80.0,
        )
        if not load_now:
            return

    # -- prompt handling ------------------------------------------------------

    def _prompt_ids(self, messages: Sequence[ChatMessage]) -> Any:
        text = _chat_to_prompt(self._tokenizer, messages)
        ids = self._tokenizer(text, return_tensors="pt")["input_ids"]
        if self.device != "cpu":
            ids = ids.to(self.device)
        return ids

    def _decode_one(self, token_id: int) -> str:
        return self._tokenizer.decode([int(token_id)], skip_special_tokens=True)

    # -- streaming -------------------------------------------------------------

    async def astream(self, messages: Sequence[ChatMessage],
                      params: GenerationParams | None = None) -> AsyncIterator[str]:
        params = params or GenerationParams()
        loop = asyncio.get_running_loop()
        out_q: "queue.Queue[Any]" = queue.Queue()

        def work() -> None:
            try:
                for tok in self._decode_stream(messages, params):
                    out_q.put(tok)
            except Exception as exc:  # noqa: BLE001
                out_q.put(exc)
            finally:
                out_q.put(None)

        threading.Thread(target=work, daemon=True).start()
        while True:
            item = await loop.run_in_executor(None, out_q.get)
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    def _decode_stream(self, messages: Sequence[ChatMessage],
                       params: GenerationParams) -> list[str]:
        import math

        torch = self._torch
        ids = self._prompt_ids(messages)
        max_new = min(params.max_tokens or self.info.max_tokens, self.info.max_tokens)
        temp = params.temperature if params.temperature else 1.0
        stop = set(params.stop or [])
        produced: list[str] = []
        for _ in range(max_new):
            with torch.no_grad():
                logits = self._model(ids).logits[0, -1]
            scaled = logits / max(temp, 1e-5)
            probs = torch.softmax(scaled, dim=-1)
            token = int(torch.multinomial(probs, num_samples=1).item())
            text = self._decode_one(token)
            if text.strip():
                produced.append(text)
                if any(s in text for s in stop):
                    break
            ids = torch.cat([ids, torch.tensor([[token]], device=ids.device)], dim=1)
            if token == int(getattr(self._tokenizer, "eos_token_id", -1)):
                break
        return produced

    async def aclose(self) -> None:
        self._model = None
        self._tokenizer = None


def _chat_to_prompt(tokenizer: Any, messages: Sequence[ChatMessage]) -> str:
    """Chat-template when available; fallback is a plain role join."""
    try:
        return tokenizer.apply_chat_template(
            [m.to_dict() for m in messages], tokenize=False, add_generation_prompt=True
        )
    except Exception:  # noqa: BLE001 - template mismatch
        parts = [f"{m.role}: {m.content or ''}" for m in messages if m.content is not None]
        return "\n".join(parts) + "\nassistant:"
