"""inference_engine — LLM backends with streaming, Attention Sinks, HYDE.

Backends: Hugging Face (LLaMa-2 / Mistral / Falcon), AutoGPTQ, llama.cpp,
GPT4All (CPU), plus a deterministic MockBackend for tests and air-gapped
demos. Everything streams via async generators (80 tok/s target is
measured, not assumed). Attention Sinks bound streaming context; HYDE
expands queries into hypothetical documents for retrieval.
"""

from __future__ import annotations

from .attention_sinks import AttentionSinkManager, StreamingContext
from .backends import make_backend
from .backends.base import BackendInfo, GenerationParams, LLMBackend
from .backends.mock import MockBackend
from .engine import InferenceEngine
from .hyde import hyde, hyde_for_retrieval

__all__ = [
    "AttentionSinkManager",
    "BackendInfo",
    "GenerationParams",
    "InferenceEngine",
    "LLMBackend",
    "MockBackend",
    "StreamingContext",
    "hyde",
    "hyde_for_retrieval",
    "make_backend",
]
