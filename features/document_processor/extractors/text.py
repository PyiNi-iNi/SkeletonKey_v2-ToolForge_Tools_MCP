"""Plain-text family extractor (txt/md/json/yaml/...). Zero deps."""

from __future__ import annotations

from . import ExtractedDoc


class TextExtractor:
    name = "text"

    def extract(self, data: bytes, filename: str) -> ExtractedDoc:
        text = data.decode("utf-8", errors="replace")
        if filename.lower().endswith(".ipynb"):
            text = _notebook_text(text)
        return ExtractedDoc(text=text, blocks=[{"text": text}], metadata={"kind": "text"})


def _notebook_text(raw: str) -> str:
    import json

    try:
        nb = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    parts = []
    for cell in nb.get("cells", []):
        src = "".join(cell.get("source", []))
        if cell.get("cell_type") == "markdown":
            parts.append(src)
        elif cell.get("cell_type") == "code":
            parts.append(src)
            for out in cell.get("outputs", []):
                if "text" in out:
                    parts.append("".join(out["text"]))
                elif "data" in out and "text/plain" in out["data"]:
                    parts.append("".join(out["data"]["text/plain"]))
    return "\n\n".join(p for p in parts if p.strip()) or raw
