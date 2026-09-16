"""PDF extractor. pypdf when installed; otherwise a zero-dependency scanner
that pulls text from uncompressed ``Tj``/``TJ`` content-stream operators
(enough for simple text PDFs and the test fixtures)."""

from __future__ import annotations

import re

from features.common.errors import BackendUnavailableError

from . import ExtractedDoc


class PDFExtractor:
    name = "pdf"

    def extract(self, data: bytes, filename: str) -> ExtractedDoc:
        backend = self._backend()
        if backend == "pypdf":
            text, pages = self._pypdf(data)
        else:
            text, pages = self._raw_scan(data)
        blocks = [{"text": p, "page": i + 1} for i, p in enumerate(pages) if p.strip()]
        meta = {"kind": "pdf", "pages": len(pages), "backend": backend}
        return ExtractedDoc(text=text, blocks=blocks, metadata=meta)

    @staticmethod
    def _backend() -> str:
        try:
            import pypdf  # type: ignore  (lazy by design)

            return "pypdf"
        except ImportError:
            return "raw"

    @staticmethod
    def _pypdf(data: bytes) -> tuple[str, list[str]]:
        import io

        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages]
        return "\n\n".join(pages), pages

    @staticmethod
    def _raw_scan(data: bytes) -> tuple[str, list[str]]:
        """Extract text from (possibly Flate-compressed) content streams."""
        import zlib

        text_chunks: list[str] = []
        # find all stream ... endstream blocks
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.DOTALL):
            raw = m.group(1)
            decoded = _try_inflate(raw)
            if decoded is None:
                decoded = raw
            text_chunks.extend(_tj_texts(decoded))
        # pages approximated by content streams
        pages = text_chunks or [b"".join(text_chunks).decode("latin-1", "replace")]
        text = "\n".join(pages)
        return text, pages


def _try_inflate(raw: bytes) -> bytes | None:
    import zlib

    for candidate in (raw, raw.strip()):
        try:
            return zlib.decompress(candidate)
        except zlib.error:
            continue
    return None


_TJ_STR = re.compile(rb"\((?:\\.|[^()\\])*\)")
_TJ_ARRAY = re.compile(rb"\[(?:\((?:\\.|[^()\\])*\)|[-0-9. ]+)+\]")


def _tj_texts(content: bytes) -> list[str]:
    out: list[str] = []
    for m in _TJ_STR.finditer(content):
        tok = m.group(0)[1:-1]
        out.append(_unescape(tok))
    # TJ arrays (multi-part) — keep simple: concatenate the strings inside
    for m in _TJ_ARRAY.finditer(content):
        parts = _TJ_STR.findall(m.group(0))
        if parts:
            out.append("".join(_unescape(p[1:-1]) for p in parts))
    # de-dup: TJ arrays often repeat the Tj strings; keep first occurrence order
    seen: set[str] = set()
    deduped = []
    for s in out:
        if s and s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


def _unescape(tok: bytes) -> str:
    out = []
    i = 0
    while i < len(tok):
        ch = tok[i]
        if ch == 0x5C and i + 1 < len(tok):  # backslash
            nxt = tok[i + 1]
            if nxt in b"n":
                out.append("\n")
            elif nxt in b"r":
                out.append("\r")
            elif nxt in b"t":
                out.append("\t")
            elif nxt in b"()\\":
                out.append(chr(nxt))
            elif 0x30 <= nxt <= 0x37:  # octal
                j = i + 1
                digits = b""
                while j < len(tok) and j < i + 4 and 0x30 <= tok[j] <= 0x37:
                    digits += tok[j]
                    j += 1
                out.append(chr(int(digits, 8) & 0xFF))
                i = j
                continue
            else:
                out.append(chr(nxt))
            i += 2
            continue
        out.append(chr(ch))
        i += 1
    return "".join(out)
