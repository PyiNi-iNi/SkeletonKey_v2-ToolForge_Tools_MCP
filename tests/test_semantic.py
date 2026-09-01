"""P5b semantic backend (ADR-0012): unit behaviour, discovery, determinism.

The backend must be a pure function (replay-safe), dependency-free, and honest
about what it is: word + char-bigram TF-IDF cosine, score in [0, 1]. The
route-level AC2 property lives in tests/test_discovery.py; this file pins the
backend itself and the discovery/entry-point contract.
"""

from __future__ import annotations

from typing import Any

from skeletonkey.core.semantic import (
    LexicalSemantic,
    discover,
    discover_backends,
    make_builtin_backend,
)

DESC_PATCH = "apply a replacement edit to one file with a content hash"
QUERY_PATCH = "apply an edit to a file safely"
QUERY_GIT = "commit the staged changes with a message"


def test_scores_range_and_monotonicity():
    b = LexicalSemantic()
    same = b.score(QUERY_PATCH, QUERY_PATCH)
    related = b.score(QUERY_PATCH, DESC_PATCH)
    unrelated = b.score(QUERY_PATCH, QUERY_GIT)
    assert 0.0 <= unrelated < related <= same <= 1.0, (same, related, unrelated)
    assert 0.0 <= same <= 1.0


def test_empty_description_scores_zero():
    b = LexicalSemantic()
    assert b.score("anything", "") == 0.0
    assert b.score("", DESC_PATCH) == 0.0
    assert b.score("", "") == 0.0


def test_deterministic_and_versioned():
    b = LexicalSemantic()
    a = [b.score(QUERY_PATCH, DESC_PATCH) for _ in range(5)]
    assert len(set(a)) == 1
    assert b.name == "lexical-tfidf" and b.version == "1"


def test_typo_and_morphology_still_overlap():
    """Char bigrams make near-misses land above a truly foreign query."""
    b = LexicalSemantic()
    typo = b.score("aplly edit too file", DESC_PATCH)
    foreign = b.score(QUERY_GIT, DESC_PATCH)
    assert typo > foreign, (typo, foreign)


def test_symmetric_score():
    b = LexicalSemantic()
    assert b.score(QUERY_PATCH, DESC_PATCH) == b.score(DESC_PATCH, QUERY_PATCH)


def test_discovery_always_ships_the_builtin():
    backends, errors = discover()
    assert errors == []
    names = [getattr(x, "name", "") for x in backends]
    assert names.count("lexical-tfidf") == 1, names
    assert "lexical-tfidf" in names
    # backwards-compatible list form agrees
    assert [getattr(x, "name", "") for x in discover_backends()] == names


def test_make_builtin_backend_returns_working_instance():
    b = make_builtin_backend()
    assert isinstance(b, LexicalSemantic)
    assert b.score(QUERY_PATCH, DESC_PATCH) > 0.0


def test_entry_points_merge_and_dedupe_by_name(monkeypatch):
    """A third-party backend joins the builtin; the builtin is never duplicated."""
    import skeletonkey.core.semantic as semmod

    class Other:
        name = "other-plug"
        version = "0"

        def score(self, query: str, description: str) -> float:
            return 0.25

    class Ep:
        def __init__(self, name: str, obj: Any) -> None:
            self.name = name
            self._obj = obj

        def load(self) -> Any:
            return self._obj

    fake_eps = [Ep("other-plug", Other()), Ep("lexical-tfidf", make_builtin_backend())]

    monkeypatch.setattr(semmod, "_entry_point_backends", lambda: (fake_eps, []))
    backends, errors = discover()
    assert errors == []
    names = [getattr(x, "name", "") for x in backends]
    assert names == ["lexical-tfidf", "other-plug"], names  # sorted, deduped


def test_broken_entry_point_is_a_load_error_not_silence(monkeypatch):
    import skeletonkey.core.semantic as semmod

    class Broken:
        name = "broken"
        load = None  # type: ignore[assignment]

    monkeypatch.setattr(
        semmod, "_entry_point_backends",
        lambda: ([], [{"name": "broken", "error": "boom"}]),
    )
    backends, errors = discover()
    assert "lexical-tfidf" in [getattr(x, "name", "") for x in backends]
    assert errors == [{"name": "broken", "error": "boom"}]
