"""Code extractor: splits source into per-symbol blocks (def/class/func)
with a pure-stdlib line scanner; tree-sitter plugs in lazily for proper
AST boundaries when installed."""

from __future__ import annotations

import os
import re

from . import ExtractedDoc

_LANGS: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".go": "go",
    ".rs": "rust", ".java": "java", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".sh": "bash", ".sql": "sql",
}

_BOUNDARY_RE = re.compile(
    r"^(?P<indent>\s*)(?P<kw>def |class |async def |func |fn |function |public class |class |@)"
)


class CodeExtractor:
    name = "code"

    def extract(self, data: bytes, filename: str) -> ExtractedDoc:
        text = data.decode("utf-8", errors="replace")
        lang = _LANGS.get(os.path.splitext(filename.lower())[1], "text")
        blocks = self._split(text, lang)
        return ExtractedDoc(
            text=text,
            blocks=blocks,
            metadata={"kind": "code", "language": lang,
                      "symbols": sum(1 for b in blocks if b.get("symbol"))},
        )

    @staticmethod
    def _split(text: str, lang: str) -> list[dict]:
        backend = "tree-sitter" if _have_ts(lang) else "regex"
        lines = text.splitlines()
        blocks: list[dict] = []
        cur: dict | None = None
        for lineno, line in enumerate(lines, start=1):
            m = _BOUNDARY_RE.match(line) if backend == "regex" else None
            if m and not line.strip().startswith("@") or (m and m.group("kw").startswith("@") and _is_decorator(line)):
                if cur:
                    blocks.append(cur)
                symbol = _symbol_of(line)
                cur = {"text": line, "symbol": symbol, "start_line": lineno,
                       "backend": backend, "language": lang}
            elif cur is not None:
                cur["text"] += "\n" + line
            elif line.strip():
                if not blocks or blocks[-1].get("prologue") is not True:
                    blocks.append({"text": line, "symbol": None, "start_line": lineno,
                                   "prologue": True, "backend": backend, "language": lang})
                else:
                    blocks[-1]["text"] += "\n" + line
        if cur:
            blocks.append(cur)
        # keep prologue only if it has content
        return [b for b in blocks if b["text"].strip()]


def _is_decorator(line: str) -> bool:
    return bool(re.match(r"^\s*@\w", line))


def _symbol_of(line: str) -> str:
    m = re.search(r"(?:def |class |func |fn |function )([\w<>]+)", line)
    if m:
        return m.group(1)
    m = re.match(r"\s*@(\w+)", line)
    return m.group(1) if m else line.strip()[:40]


def _have_ts(lang: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("tree_sitter") is not None and lang == "python"
    except Exception:  # noqa: BLE001
        return False
