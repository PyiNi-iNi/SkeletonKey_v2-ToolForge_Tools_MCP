"""LLM backend implementations (all heavy deps lazy)."""

from __future__ import annotations

from features.common.config import FeatureConfig
from features.common.errors import BackendUnavailableError

from .base import BackendInfo, GenerationParams, LLMBackend
from .mock import MockBackend

__all__ = [
    "BackendInfo",
    "GenerationParams",
    "LLMBackend",
    "MockBackend",
    "make_backend",
    "available_backends",
]

_FAMILY_BY_NAME = ("llama", "mistral", "falcon")


def _family(model: str) -> str:
    low = model.lower()
    for fam in _FAMILY_BY_NAME:
        if fam in low:
            return fam
    return "other"


def make_backend(kind: str | None = None, model: str | None = None,
                 device: str | None = None) -> LLMBackend:
    """Resolve a backend by kind. ``kind``: auto|mock|hf|gptq|llamacpp|gpt4all."""
    cfg = FeatureConfig(prefix="SK_INF")
    name = (kind or cfg.get_str("backend") or "auto").lower()
    model_name = model or cfg.get_str("model")
    if name == "mock":
        return MockBackend(model=model_name or "mock-1b")
    if name in {"hf", "huggingface"}:
        from .huggingface import HuggingFaceBackend

        return HuggingFaceBackend(model_name or cfg.get_str("model") or
                                  "mistralai/Mistral-7B-v0.1", device=device)
    if name == "gptq":
        from .gptq import GPTQBackend

        return GPTQBackend(model_name or cfg.get_str("model") or "", device=device)
    if name == "llamacpp":
        from .llamacpp import LlamaCppBackend

        return LlamaCppBackend(cfg.get_str("llamacpp_path") or "",
                               n_ctx=cfg.get_int("context", 4096))
    if name == "gpt4all":
        from .llamacpp import GPT4AllBackend

        return GPT4AllBackend(model_name or "ggml-model.bin")
    if name == "auto":
        for candidate in ("hf", "gptq", "llamacpp", "gpt4all"):
            try:
                return make_backend(candidate, model=model_name, device=device)
            except BackendUnavailableError:
                continue
        return MockBackend(model=model_name or "mock-1b")
    raise BackendUnavailableError(f"unknown inference backend {name!r}",
                                  hint="auto|mock|hf|gptq|llamacpp|gpt4all")


def available_backends() -> list[str]:
    import importlib.util

    probes = {"hf": "transformers", "gptq": "auto_gptq",
              "llamacpp": "llama_cpp", "gpt4all": "gpt4all"}
    return [k for k, p in probes.items() if importlib.util.find_spec(p) is not None]
