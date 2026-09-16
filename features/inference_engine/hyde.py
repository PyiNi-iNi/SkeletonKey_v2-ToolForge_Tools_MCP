"""HYDE — Hypothetical Document Embeddings (Gao et al. 2022).

Expands a query into a hypothetical answer document, then embeds *that*
for retrieval. Bridges the lexical gap between short questions and the
long-form passages in the private DB.
"""

from __future__ import annotations

from typing import Any, Sequence

from features.common.openai_compat import ChatMessage

from .backends.base import GenerationParams, LLMBackend

__all__ = ["hyde", "hyde_for_retrieval"]

_HYDE_PROMPT = (
    "Write a short hypothetical answer (2-4 sentences) to the question "
    "below. It should read like an encyclopedic passage that would contain "
    "the real answer if retrieved from a corpus. Question: {question}"
)


async def hyde(question: str, backend: LLMBackend,
               max_tokens: int = 96) -> str:
    """Generate the hypothetical document for a question."""
    messages = [ChatMessage(role="user", content=_HYDE_PROMPT.format(question=question))]
    text = await backend.generate(messages, GenerationParams(max_tokens=max_tokens))
    text = text.strip()
    return text or question  # never return an empty expansion


def hyde_for_retrieval(question: str, backend: LLMBackend, db: Any,
                       namespace: str = "documents", n: int = 5) -> dict[str, Any]:
    """Sync convenience: expand, then retrieve with the hypothetical doc.

    ``db`` is a ``features.db_manager.DBManager``.
    """
    import asyncio

    async def _run() -> dict[str, Any]:
        hypothetical = await hyde(question, backend)
        col = db.collection(namespace)
        hits = await col.aquery_texts(hypothetical, n=n)
        return {"question": question, "hypothetical": hypothetical, "hits": hits}

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())
    raise RuntimeError("hyde_for_retrieval called from inside a running loop; use hyde() + db directly")
