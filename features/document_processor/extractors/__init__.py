"""Format extractors: raw bytes/text -> list of text blocks with metadata.

Every extractor has a zero-dependency fallback so ingestion never hard-
fails on a bare install.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ExtractedDoc:
    text: str
    blocks: list[dict[str, Any]]  # e.g. [{"text":..., "sheet":..., "row":...}]
    metadata: dict[str, Any]


class Extractor(Protocol):
    name: str

    def extract(self, data: bytes, filename: str) -> ExtractedDoc: ...


from .code import CodeExtractor  # noqa: E402
from .excel import ExcelExtractor  # noqa: E402
from .pdf import PDFExtractor  # noqa: E402
from .text import TextExtractor  # noqa: E402

__all__ = ["ExtractedDoc", "Extractor", "CodeExtractor", "ExcelExtractor",
           "PDFExtractor", "TextExtractor", "extractor_for"]

_TEXT_EXTS = {".txt", ".md", ".rst", ".log", ".json", ".yaml", ".yml",
              ".toml", ".xml", ".html", ".htm", ".ipynb"}
_CODE_EXTS = {".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp",
              ".cs", ".rb", ".php", ".sh", ".ps1", ".sql", ".swift", ".kt"}
_EXCEL_EXTS = {".xlsx", ".xlsm", ".csv", ".tsv", ".xls"}


def extractor_for(filename: str) -> Extractor:
    lowered = filename.lower()
    import os

    ext = os.path.splitext(lowered)[1]
    if ext == ".pdf":
        return PDFExtractor()
    if ext in _EXCEL_EXTS:
        return ExcelExtractor()
    if ext in _CODE_EXTS:
        return CodeExtractor()
    return TextExtractor()
