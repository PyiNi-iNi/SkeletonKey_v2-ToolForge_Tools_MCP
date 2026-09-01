"""Semantic routing stage - P5a protocol, P5b builtin backend (ADR-0012).

The router is two-stage by contract (P5a): the deterministic lexical path always
exists and never needs this module; the semantic stage is a *registered backend*
that reranks/blends on top. P5b ships one such backend - `LexicalSemantic`
("lexical-tfidf"), a pure-stdlib TF-IDF cosine over word + character-bigram
features - so ``registry.route(semantic=True)`` is a real two-stage comparison
out of the box, still with zero mandatory dependencies (ADR-0001): the core has
no dependency on any embedding model or download.

A backend is a module or package exposing either a `SemanticBackend` instance or a
zero-arg factory under the entry-point group `skeletonkey.semantic` (see
docs/TOOL-CONTRACT.md 7e). It must be deterministic for a given model/version so a
replay of the routing decision is reproducible. The builtin also registers under
that group, so an installed dist discovers it exactly like a third-party backend;
a dev checkout (no dist metadata) resolves it directly. No backend is ever active
unless `tools.semantic = true` - the default route stays the pure lexical path.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Protocol

_WORDS = re.compile(r"[a-z0-9]+")


class SemanticBackend(Protocol):
    """The one method the router needs. Pure function over text, no side effects."""

    name: str

    def score(self, query: str, description: str) -> float:
        """Similarity of a query to a tool's compact doc; higher = more relevant."""
        ...


class LexicalSemantic:
    """Deterministic zero-dependency similarity (name: ``lexical-tfidf``).

    Features are lowercased words (length > 1, weight 2.0) and character bigrams
    over the letter/digit stream (weight 1.0), so morphology and typos still
    overlap with a tool description. TF-IDF weights are computed over the
    query/doc pair as a two-document corpus - a micro-corpus, not a claim of
    corpus-wide statistics: idf(term) = log((1 + N) / (1 + df)) + 1 with N = 2.
    The score is the cosine of the two weighted vectors, in [0, 1], and the pure
    function of (query, description): same inputs, same score, same ranking.
    """

    name = "lexical-tfidf"
    version = "1"

    def score(self, query: str, description: str) -> float:
        q = _features(query or "")
        if not q:
            return 0.0
        d = _features(description or "")
        if not d:
            return 0.0
        corpus = (query or "", description or "")
        qw = _idf_weighted(q, corpus)
        dw = _idf_weighted(d, corpus)
        common = set(qw) & set(dw)
        num = sum(qw[k] * dw[k] for k in common)
        den = math.sqrt(sum(v * v for v in qw.values())) * math.sqrt(
            sum(v * v for v in dw.values())
        )
        return round(num / den, 6) if den > 0 else 0.0


def make_builtin_backend() -> LexicalSemantic:
    """Zero-arg factory: the entry-point form of the builtin backend."""
    return LexicalSemantic()


def _features(text: str) -> Counter[str]:
    """Word + character-bigram feature counts for one text."""
    out: Counter[str] = Counter()
    plain = re.sub(r"[^a-z0-9]", " ", text.lower())
    for w in _WORDS.findall(plain):
        if len(w) > 1:
            out["w:" + w] += 2.0
    flat = re.sub(r"[^a-z0-9]", "", text.lower())
    for i in range(max(0, len(flat) - 1)):
        out["c:" + flat[i : i + 2]] += 1.0
    return out


def _idf_weighted(counts: Counter[str], corpus: tuple[str, str]) -> dict[str, float]:
    """TF-IDF weights over the given micro-corpus (N = len(corpus))."""
    n = len(corpus)
    dfs: Counter[str] = Counter()
    for text in corpus:
        for tok in set(_features(text)):
            dfs[tok] += 1
    return {k: v * (math.log((1.0 + n) / (1.0 + dfs[k])) + 1.0) for k, v in counts.items()}


def _entry_point_backends() -> tuple[list[Any], list[dict[str, Any]]]:
    """Load every `skeletonkey.semantic` entry point, reporting per-backend errors."""
    out: list[Any] = []
    errors: list[dict[str, Any]] = []
    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="skeletonkey.semantic")
    except Exception as exc:  # pragma: no cover - importlib.metadata stays broken
        return out, [{"name": "importlib.metadata", "error": str(exc)}]
    for ep in eps or []:
        try:
            obj = ep.load()
            backend = obj() if callable(obj) else obj
            if backend is None or getattr(backend, "score", None) is None:
                raise TypeError("not a SemanticBackend (no score())")
            out.append(backend)
        except Exception as exc:
            errors.append({"name": ep.name, "error": str(exc)})
    return out, errors


def discover() -> tuple[list[Any], list[dict[str, Any]]]:
    """Every backend available: entry-point backends (by name) + the builtin.

    The builtin is deduplicated by name, so an installed dist that exposes
    ``lexical-tfidf`` through the entry-point group still yields exactly one.
    Entry-point backends sort by name (deterministic first-backend choice);
    the builtin is appended last. Load errors are returned, never swallowed -
    a broken backend is visible in ``registry.route``'s ``backend_errors``.
    """
    eps, errors = _entry_point_backends()
    eps.sort(key=lambda b: getattr(b, "name", "") or "")
    names = {getattr(b, "name", "") for b in eps}
    if "lexical-tfidf" not in names:
        eps.append(LexicalSemantic())
    return eps, errors


def discover_backends() -> list[Any]:
    """Backwards-compatible list form of :func:`discover`."""
    backends, _errors = discover()
    return backends
