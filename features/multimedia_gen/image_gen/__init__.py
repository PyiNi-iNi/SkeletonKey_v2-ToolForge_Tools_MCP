"""image_gen — Stable Diffusion / Flux / PlaygroundAI with streaming progress.

Backends emit stage events (queue → model_load → denoise steps → vae →
done) so UIs can render a live progress bar; the final event carries the
PNG bytes. A deterministic pure-Python PNG mock (``png.write_png``) keeps
the pipeline fully testable without torch/diffusers.
"""

from __future__ import annotations

from .backends import (
    FluxBackend,
    ImageGenBackend,
    ImageParams,
    ImageResult,
    MockImageBackend,
    PlaygroundBackend,
    StableDiffusionBackend,
)
from .pipeline import ImageGenPipeline, make_image_backend
from .png import parse_png_header, write_png

__all__ = [
    "FluxBackend",
    "ImageGenBackend",
    "ImageGenPipeline",
    "ImageParams",
    "ImageResult",
    "MockImageBackend",
    "PlaygroundBackend",
    "StableDiffusionBackend",
    "make_image_backend",
    "parse_png_header",
    "write_png",
]
