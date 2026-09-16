"""vision_engine — multi-provider vision (LLaVa / Claude-3 / Gemini / GPT-4).

Provider backends speak each vendor's wire format (OpenAI chat with
image_url parts, Anthropic messages, Gemini generateContent) over plain
stdlib urllib; LLaVa runs locally via lazy transformers. A deterministic
MockVision keeps the feature fully testable air-gapped.
"""

from __future__ import annotations

from .backends import (
    AnthropicVisionBackend,
    GeminiVisionBackend,
    LLaVaBackend,
    MockVisionBackend,
    OpenAIVisionBackend,
    VisionBackend,
)
from .engine import VisionEngine, image_to_data_uri

__all__ = [
    "AnthropicVisionBackend",
    "GeminiVisionBackend",
    "LLaVaBackend",
    "MockVisionBackend",
    "OpenAIVisionBackend",
    "VisionBackend",
    "VisionEngine",
    "image_to_data_uri",
]
