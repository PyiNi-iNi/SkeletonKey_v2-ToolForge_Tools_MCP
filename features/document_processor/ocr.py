"""OCR engines. DocTR via docling is lazy; PassthroughOCR keeps the
pipeline functional on hosts without OCR installed (scanned images then
ingest as empty + a metadata flag)."""

from __future__ import annotations

from typing import Any, Protocol

from features.common.errors import BackendUnavailableError

__all__ = ["OCREngine", "DocTROCR", "PassthroughOCR"]


class OCREngine(Protocol):
    name: str

    def extract(self, image: bytes) -> str: ...


class DocTROCR:
    """DocTR-based OCR (docling). Lazy import; GPU optional (Paddle/Torch)."""

    name = "doctr"

    def __init__(self, device: str | None = None):
        try:
            from docling.document_converter import DocumentConverter  # type: ignore
        except ImportError as exc:
            raise BackendUnavailableError(
                "docling (DocTR) is not installed",
                hint='pip install "docling>=2.0" pillow',
            ) from exc
        self._converter = DocumentConverter()
        self._device = device

    def extract(self, image: bytes) -> str:
        import io

        from PIL import Image  # type: ignore

        img = Image.open(io.BytesIO(image))
        result = self._converter.convert(image=img)
        return result.document.export_to_markdown()


class PassthroughOCR:
    """No-op OCR: signals 'no OCR installed' without failing the pipeline."""

    name = "passthrough"

    def extract(self, image: bytes) -> str:
        return ""


def make_ocr(prefer: str = "auto") -> OCREngine:
    if prefer in {"auto", "doctr"}:
        try:
            return DocTROCR()
        except BackendUnavailableError:
            if prefer == "doctr":
                raise
    return PassthroughOCR()
