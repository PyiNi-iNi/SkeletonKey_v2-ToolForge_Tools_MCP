"""SkeletonKey features — the modular AI orchestration platform.

Each feature lives in its own directory with its own README, requirements.txt
(heavy, optional dependencies only) and tests/. Import path is
``features.<name>``. The core of every feature is pure-stdlib (ADR-0001
applies to the features tree as well); heavy backends are lazy-imported and
every backend pair ships a zero-dependency fallback so the whole platform
runs, and is fully testable, on a bare CPython install.

See ``features/README.md`` for the architecture map and
``docs/adr/0014-features-architecture.md`` for the decisions.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

__all__ = ["Feature", "FEATURES", "feature_names", "load_feature", "feature_status"]

# name -> (module, one-line purpose, status)
FEATURES: dict[str, tuple[str, str, str]] = {
    "common": ("features.common", "shared support: OpenAI schemas, streaming, config, http", "core"),
    "db_manager": ("features.db_manager", "private persistent vector DB + embedding engine", "core"),
    "document_processor": (
        "features.document_processor",
        "semantic chunking, OCR, and PDF/Excel/code ingestion pipelines",
        "core",
    ),
    "inference_engine": (
        "features.inference_engine",
        "LLM backends (LLaMa2/Mistral/Falcon), Attention Sinks, HYDE, streaming",
        "core",
    ),
    "ui_manager": ("features.ui_manager", "Gradio UI, bake-off mode, state, authentication", "core"),
    "vision_engine": ("features.vision_engine", "LLaVa / Claude-3 / Gemini / GPT-4 vision", "core"),
    "multimedia_gen": ("features.multimedia_gen", "image gen, STT, TTS (all streaming)", "core"),
    "agent_framework": (
        "features.agent_framework",
        "AutoGen conversations, code/CSV agents, web search",
        "core",
    ),
    "api_proxy": ("features.api_proxy", "OpenAI-compatible server: function calling, JSON mode", "core"),
    "evaluator": ("features.evaluator", "reward-model evaluation + 1000-case automated suite", "core"),
}


@dataclass(frozen=True)
class Feature:
    name: str
    module: str
    purpose: str
    status: str


def feature_names() -> list[str]:
    return sorted(FEATURES)


def load_feature(name: str):
    """Import and return a feature package. Unknown names raise KeyError."""
    if name not in FEATURES:
        raise KeyError(f"unknown feature {name!r}; expected one of {feature_names()}")
    module, _, _ = FEATURES[name]
    return importlib.import_module(module)


def feature_status(name: str) -> str:
    if name not in FEATURES:
        raise KeyError(f"unknown feature {name!r}")
    return FEATURES[name][2]
