"""Web search integration (keyless DuckDuckGo HTML) + grounded answer agent.

``DuckDuckGoSearch`` scrapes the HTML endpoint (no API key);
``MockSearch`` gives deterministic results for tests; ``SearchAgent``
combines search + the inference engine to answer with cited sources.
"""

from __future__ import annotations

import asyncio
import html
import re
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from features.common.errors import UpstreamError, ValidationError
from features.common.openai_compat import ChatMessage

__all__ = [
    "SearchResult",
    "WebSearch",
    "DuckDuckGoSearch",
    "MockSearch",
    "SearchAgent",
]


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class WebSearch(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...


class DuckDuckGoSearch(WebSearch):
    """Keyless search via DuckDuckGo's HTML endpoint."""

    ENDPOINT = "https://duckduckgo.com/html/"

    def __init__(self, timeout: float = 15.0, endpoint: str | None = None):
        self.timeout = timeout
        self.endpoint = endpoint or self.ENDPOINT

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not query.strip():
            raise ValidationError("query must be non-empty")
        url = self.endpoint + "?" + urllib.parse.urlencode({"q": query})

        def work() -> str:
            req = urllib.request.Request(url, headers={"User-Agent": "skeletonkey-agent/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                raise UpstreamError(f"search request failed: {exc}") from exc

        page = await asyncio.to_thread(work)
        return self.parse(page, max_results)

    @staticmethod
    def parse(page: str, max_results: int = 5) -> list[SearchResult]:
        """Parse DDG HTML result blocks (class="result" / data-result)."""
        out: list[SearchResult] = []
        # links: <a rel="nofollow" class="result__a" href="...">Title</a>
        link_re = re.compile(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL)
        snippet_re = re.compile(
            r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.DOTALL)
        snippets = [re.sub(r"<[^>]+>", "", s) for s in snippet_re.findall(page)]
        for i, (href, title) in enumerate(link_re.findall(page)):
            out.append(SearchResult(
                title=_clean(title), url=_resolve(href), snippet=_clean(snippets[i])
                if i < len(snippets) else ""))
            if len(out) >= max_results:
                break
        return out


def _clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(text).strip()


def _resolve(href: str) -> str:
    """DDG wraps result URLs in /l/?uddg=<quoted> redirects."""
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/l/") or "uddg=" in href:
        parsed = urllib.parse.urlparse(
            href if href.startswith("http") else "https://duckduckgo.com" + href)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            return qs["uddg"][0]
    return href


class MockSearch(WebSearch):
    """Deterministic offline results (tests / air-gapped)."""

    TOPICS = [
        ("Vector databases overview", "https://example.com/vector-dbs",
         "Chroma, FAISS, and Weaviate compare for embedding retrieval."),
        ("Streaming LLM attention sinks", "https://example.com/attention-sinks",
         "Retaining initial tokens preserves perplexity in streaming LLMs."),
        ("HYDE hypothetical documents", "https://example.com/hyde",
         "Expanding queries into hypothetical passages improves retrieval."),
        ("OpenAI function calling", "https://example.com/function-calling",
         "Structured tool invocation with JSON schemas in chat completions."),
        ("Whisper streaming transcription", "https://example.com/whisper-stream",
         "Chunked partial transcripts enable live captions."),
    ]

    def __init__(self):
        pass

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not query.strip():
            raise ValidationError("query must be non-empty")
        import hashlib

        h = int.from_bytes(hashlib.blake2b(query.lower().encode(), digest_size=4).digest(), "big")
        pool = list(self.TOPICS)
        pool.sort(key=lambda t: (h + t[0].__hash__()) % 7)  # stable shuffle
        out = []
        for i, (title, url, snippet) in enumerate(pool):
            out.append(SearchResult(title=f"{title} — for: {query}", url=url,
                                    snippet=snippet))
            if len(out) >= max_results:
                break
        return out


class SearchAgent:
    """search → ground → answer, with sources attached."""

    SYSTEM = (
        "You are a research assistant. Use ONLY the provided sources. "
        "Cite sources as [n] and end with a Sources list."
    )

    def __init__(self, engine: Any, search: WebSearch | None = None):
        self.engine = engine
        self.search = search or MockSearch()

    def _prompt(self, question: str, results: list[SearchResult]) -> list[ChatMessage]:
        source_text = "\n".join(
            f"[{i + 1}] {r.title}\n{r.url}\n{r.snippet}" for i, r in enumerate(results))
        user = (f"Question: {question}\n\nSources:\n{source_text}\n\n"
                f"Answer the question using the sources, with [n] citations.")
        return [ChatMessage(role="system", content=self.SYSTEM),
                ChatMessage(role="user", content=user)]

    async def ask(self, question: str, model: str | None = None,
                  max_results: int = 5) -> dict[str, Any]:
        from features.inference_engine.backends.base import GenerationParams

        results = await self.search.search(question, max_results=max_results)
        answer = await self.engine.generate(
            model, self._prompt(question, results), GenerationParams(max_tokens=300))
        return {"question": question, "answer": answer,
                "sources": [r.to_dict() for r in results]}
