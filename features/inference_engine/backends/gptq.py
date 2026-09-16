"""AutoGPTQ backend — 4/8-bit quantized HF models (A100/V100 friendly)."""

from __future__ import annotations

from typing import Any, AsyncIterator, Sequence

from features.common.errors import BackendUnavailableError
from features.common.openai_compat import ChatMessage

from .base import BackendInfo, GenerationParams, LLMBackend
from .huggingface import _chat_to_prompt


class GPTQBackend(LLMBackend):
    def __init__(self, model: str, device: str | None = None, load_now: bool = True):
        if not model:
            raise BackendUnavailableError(
                "GPTQ backend needs a model id (SK_INF_MODEL)",
                hint="e.g. TheBloke/Llama-2-13B-chat-GPTQ",
            )
        try:
            import torch  # type: ignore
            from auto_gptq import AutoGPTQForCausalLM  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "auto-gptq is not installed",
                hint='pip install "auto-gptq>=0.7"',
            ) from exc
        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._tokenizer = _import_tokenizers().AutoTokenizer.from_pretrained(model, trust_remote_code=True)
        self._model = AutoGPTQForCausalLM.from_quantized(
            model, device=self.device, trust_remote_code=True
        )
        self.info = BackendInfo(
            name=model, family=_quant_family(model), device=self.device,
            quantization=str(getattr(self._model.config, "quantization_config", {}).get("bits", "gptq")),
            context_size=int(getattr(self._model.config, "max_position_embeddings", 4096)),
            max_tokens=1024, estimated_tps=45.0,
        )
        if not load_now:
            return

    async def astream(self, messages: Sequence[ChatMessage],
                      params: GenerationParams | None = None) -> AsyncIterator[str]:
        import asyncio

        params = params or GenerationParams()
        text = _chat_to_prompt(self._tokenizer, messages)
        kwargs: dict[str, Any] = {
            "input_ids": self._tokenizer(text, return_tensors="pt").to(self.device)["input_ids"],
            "max_new_tokens": min(params.max_tokens or self.info.max_tokens, self.info.max_tokens),
            "do_sample": (params.temperature or 1.0) > 0.0,
            "streamer": None,
        }
        if params.temperature:
            kwargs["temperature"] = params.temperature
        out = await asyncio.to_thread(self._model.generate, **kwargs)
        full = self._tokenizer.decode(out[0], skip_special_tokens=True)
        for tok in full.split(" "):
            yield " " + tok

    async def aclose(self) -> None:
        self._model = None


def _import_tokenizers() -> Any:
    import transformers  # type: ignore

    return transformers


def _quant_family(model: str) -> str:
    from . import _family

    return _family(model)
