"""Optional semantic routing stage - P5a contract, P5b implementation.

The router is two-stage by contract (P5a): the deterministic lexical path always
exists and never needs this module; the semantic stage is a *registered backend*
that reranks/blends on top. The core ships no backend - the zero-dependency rule
(ADR-0001) says mandatory dependencies are forbidden, not that optional ones cannot
exist - so with nothing installed `Registry.route` reports the honest
`mode: "lexical"` plus the reason, and behaves identically to `search()`.

A backend is a module or package exposing either a `SemanticBackend` instance or a
zero-arg factory under the entry-point group `skeletonkey.semantic` (see
docs/TOOL-CONTRACT.md 7e). It must be deterministic for a given model/version so a
replay of the routing decision is reproducible.
"""

from __future__ import annotations

from typing import Any, Protocol


class SemanticBackend(Protocol):
    """The one method the router needs. Pure function over text, no side effects."""

    name: str

    def score(self, query: str, description: str) -> float:
        """Similarity of a query to a tool's compact doc; higher = more relevant."""
        ...


def discover_backends() -> list[Any]:
    """Load every `skeletonkey.semantic` entry point, best-effort.

    A broken backend is a load error, not a reason to take the lexical path silently -
    the router reports `load_errors` alongside the count so the failure is visible.
    """
    out: list[Any] = []
    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="skeletonkey.semantic")
    except Exception:
        return out
    for ep in eps or []:
        try:
            obj = ep.load()
            backend = obj() if callable(obj) else obj
            if backend is not None and getattr(backend, "score", None) is not None:
                out.append(backend)
        except Exception:
            continue
    return out
