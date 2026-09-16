"""multimedia_gen — image generation, speech-to-text, text-to-speech.

Three self-contained sub-features, each with its own README/requirements/
tests:

- ``image_gen/``  Stable Diffusion / Flux / PlaygroundAI (+ pure-Python
                  PNG mock), streaming progress events
- ``stt/``        Whisper streaming transcription (chunked partials)
- ``tts/``        Microsoft Speech T5 + voice cloning (WAV streaming)

Every pipeline streams (async generators) and degrades gracefully to a
zero-dependency deterministic mock when the heavy runtime is missing.
"""

from __future__ import annotations

__all__ = ["image_gen", "stt", "tts"]
