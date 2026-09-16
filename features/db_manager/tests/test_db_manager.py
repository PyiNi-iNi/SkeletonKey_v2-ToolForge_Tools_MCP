"""Integration tests for features.db_manager (zero heavy deps required)."""

from __future__ import annotations

import http.client
import json

import pytest

from features.common.errors import BackendUnavailableError, ValidationError
from features.db_manager import (
    DBManager,
    HashingEmbedder,
    MemoryStore,
    SqliteStore,
)
from features.db_manager.backends import available_backends, make_store
from features.db_manager.server import make_db_server
from features.common.http import HttpServer


# --------------------------------------------------------------------------
# Embedding engine
# --------------------------------------------------------------------------


@pytest.mark.parametrize("dim", [64, 128, 384, 768])
def test_hashing_embedder_dimensions(dim):
    emb = HashingEmbedder(dim=dim)
    vecs = emb.embed(["hello world", "the quick brown fox"])
    assert len(vecs) == 2
    assert all(len(v) == dim for v in vecs)


@pytest.mark.parametrize(
    "text",
    [
        "hello world",
        "Vector databases store embeddings.",
        "Émojis 🎉 and Unicode text",
        "12345 numbers and symbols !?#$%&",
        "",
        "   ",
        "a" * 5000,
    ],
)
def test_hashing_embedder_deterministic_and_normalized(text):
    emb = HashingEmbedder(dim=128)
    v1 = emb.embed([text])[0]
    v2 = emb.embed([text])[0]
    assert v1 == v2
    norm = sum(x * x for x in v1) ** 0.5
    if text.strip():
        assert abs(norm - 1.0) < 1e-9
    else:
        assert norm == 0.0


def test_hashing_embedder_ranking_sanity():
    emb = HashingEmbedder(dim=512)
    q = emb.embed(["the cat sat on the mat"])[0]
    near = emb.embed(["a cat sat on a mat"])[0]
    far = emb.embed(["quantum entanglement of photons"])[0]

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))

    assert cos(q, near) > cos(q, far)


def test_hashing_embedder_seed_changes_vectors():
    a = HashingEmbedder(dim=128, seed=1).embed(["hello"])[0]
    b = HashingEmbedder(dim=128, seed=2).embed(["hello"])[0]
    assert a != b


def test_engine_auto_falls_back_to_hashing(monkeypatch):
    from features.db_manager.embeddings import EmbeddingEngine

    monkeypatch.delenv("SK_DB_EMBED_BACKEND", raising=False)
    # sentence-transformers not installed in CI -> hashing fallback
    engine = EmbeddingEngine()
    assert engine.dim > 0
    vec = engine.encode_one("ping")
    assert len(vec) == engine.dim


def test_engine_explicit_hash_backend():
    from features.db_manager.embeddings import EmbeddingEngine
    from features.common.config import FeatureConfig

    engine = EmbeddingEngine(HashingEmbedder(dim=96))
    assert engine.dim == 96


# --------------------------------------------------------------------------
# Memory store
# --------------------------------------------------------------------------


def test_memory_store_roundtrip():
    s = MemoryStore(dim=3)
    s.add(["a", "b"], [[1.0, 0, 0], [0, 1.0, 0]], [{"k": "a"}, {"k": "b"}])
    assert s.count() == 2
    hits = s.query([[1.0, 0.0, 0]], n=2)
    assert [h.id for h in hits] == ["a", "b"]
    assert hits[0].score == pytest.approx(1.0)
    got = s.get(["b"])
    assert got[0]["metadata"] == {"k": "b"}
    assert s.delete(["a"]) == 1
    assert s.count() == 1
    assert s.delete(["a", "nope"]) == 0


def test_memory_store_where_filter():
    s = MemoryStore(dim=2)
    s.add(["1", "2", "3"], [[1, 0], [0.9, 0.1], [0, 1]],
           [{"t": "x"}, {"t": "y"}, {"t": "x"}])
    hits = s.query([[1, 0]], n=5, where={"t": "x"})
    assert sorted(h.id for h in hits) == ["1", "3"]


def test_memory_store_dimension_mismatch():
    s = MemoryStore(dim=2)
    s.add(["a"], [[1.0, 0.0]])
    with pytest.raises(ValueError):
        s.query([[1, 0, 0]])


# --------------------------------------------------------------------------
# SQLite store (persistence)
# --------------------------------------------------------------------------


@pytest.fixture
def sqlite_path(tmp_path):
    return str(tmp_path / "private.db")


def test_sqlite_roundtrip(sqlite_path):
    s = SqliteStore(namespace="notes", path=sqlite_path, dim=4)
    s.add(["a", "b"], [[1, 0, 0, 0], [0, 1, 0, 0]], [{"n": 1}, {"n": 2}])
    assert s.count() == 2
    hits = s.query([[1, 0, 0, 0]], n=2)
    assert hits[0].id == "a"
    assert hits[0].score == pytest.approx(1.0)
    assert s.get(["a"])[0]["metadata"] == {"n": 1}
    s.close()


def test_sqlite_persists_across_reopen(sqlite_path):
    s = SqliteStore(namespace="notes", path=sqlite_path, dim=2)
    s.add(["a", "b"], [[1, 0], [0, 1]], [{"n": 1}, {"n": 2}])
    s.close()
    s2 = SqliteStore(namespace="notes", path=sqlite_path, dim=2)
    assert s2.count() == 2
    assert s2.query([[1, 0]], n=1)[0].id == "a"
    assert s2.namespaces() == ["notes"]
    s2.close()


def test_sqlite_upsert_and_delete(sqlite_path):
    s = SqliteStore(namespace="n", path=sqlite_path, dim=2)
    s.add(["a"], [[1, 0]], [{"v": 1}])
    s.add(["a"], [[0, 1]], [{"v": 2}])
    assert s.count() == 1
    assert s.get(["a"])[0]["metadata"] == {"v": 2}
    assert s.delete(["a"]) == 1
    assert s.count() == 0
    s.close()


def test_sqlite_namespaces_isolated(sqlite_path):
    s1 = SqliteStore(namespace="one", path=sqlite_path, dim=2)
    s2 = SqliteStore(namespace="two", path=sqlite_path, dim=2)
    s1.add(["x"], [[1, 0]])
    s2.add(["y"], [[0, 1]])
    assert s1.count() == 1 and s2.count() == 1
    assert s1.namespaces() == ["one", "two"]
    s1.close()
    s2.close()


# --------------------------------------------------------------------------
# Backend selection
# --------------------------------------------------------------------------


def test_make_store_sqlite_explicit(sqlite_path):
    s = make_store("sqlite", namespace="d", path=sqlite_path, dim=4)
    assert s.name == "sqlite"
    s.close()


def test_make_store_memory():
    s = make_store("memory", namespace="d", dim=4)
    assert s.name == "memory"


def test_make_store_unknown_backend():
    with pytest.raises(BackendUnavailableError):
        make_store("postgres", namespace="d", dim=4)


def test_auto_selection_picks_sqlite_without_heavy_backends(monkeypatch):
    monkeypatch.delenv("SK_DB_BACKEND", raising=False)
    available = available_backends()
    assert "sqlite" in available
    # no heavy backends installed in CI -> auto must land on sqlite
    if "faiss" not in available and "chroma" not in available:
        s = make_store(None, namespace="d", dim=4)
        assert s.name == "sqlite"
        s.close()


def test_make_store_faiss_missing_gives_hint(monkeypatch):
    import importlib.util

    real = importlib.util.find_spec

    def fake(name, *a, **k):
        if name in {"faiss", "chromadb", "weaviate"}:
            return None
        return real(name, *a, **k)

    monkeypatch.setattr(importlib.util, "find_spec", fake)
    with pytest.raises(BackendUnavailableError) as ei:
        make_store("faiss", namespace="d", dim=4)
    assert "pip install" in (ei.value.hint or "")


# --------------------------------------------------------------------------
# DBManager facade
# --------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    manager = DBManager(backend="sqlite", path=str(tmp_path / "db.sqlite"), dim=64)
    yield manager
    manager.close()


def test_dbmanager_add_and_rank(db):
    col = db.collection("docs")
    col.add_texts(
        [
            "Chroma is a vector database for embeddings",
            "The mitochondria is the powerhouse of the cell",
            "FAISS is a library for fast similarity search",
        ],
        metadatas=[{"src": "db"}, {"src": "bio"}, {"src": "db"}],
    )
    hits = col.query_texts("vector database for embeddings", n=2)
    assert len(hits) == 2
    assert hits[0]["metadata"]["src"] == "db"
    # doc 1 must beat doc 2 on this query
    srcs = [h["metadata"]["src"] for h in hits]
    assert srcs[0] == "db"


def test_dbmanager_where(db):
    col = db.collection("docs2")
    col.add_texts(["alpha", "beta"], metadatas=[{"cat": "a"}, {"cat": "b"}])
    hits = col.query_texts("alpha", n=5, where={"cat": "b"})
    assert [h["id"] for h in hits] and all(h["metadata"]["cat"] == "b" for h in hits)


def test_dbmanager_count_delete(db):
    col = db.collection("c")
    ids = col.add_texts(["one", "two", "three"])
    assert col.count() == 3
    assert col.delete(ids[:2]) == 2
    assert col.count() == 1
    assert len(col.get(ids)) == 1


def test_dbmanager_dimension_guard(db):
    col = db.collection("d")
    with pytest.raises(ValidationError):
        col.query([0.1] * 5)


def test_dbmanager_closed_rejects_use(db):
    db.close()
    with pytest.raises(ValidationError):
        db.collection("x").store


async def test_dbmanager_async_paths(tmp_path):
    db = DBManager(backend="memory", dim=64)
    col = db.collection("a")
    ids = await col.aadd_texts(["hello async world", "another doc"])
    assert len(ids) == 2
    hits = await col.aquery_texts("hello async", n=1)
    assert hits[0]["id"] == ids[0]
    db.close()


def test_dbmanager_namespaces(tmp_path):
    db = DBManager(backend="sqlite", path=str(tmp_path / "n.sqlite"), dim=32)
    db.collection("alpha").add_texts(["x"])
    db.collection("beta").add_texts(["y"])
    assert sorted(db.namespaces()) == ["alpha", "beta"]
    db.close()


# --------------------------------------------------------------------------
# REST server
# --------------------------------------------------------------------------


async def test_db_server_roundtrip(tmp_path):
    db = DBManager(backend="sqlite", path=str(tmp_path / "s.sqlite"), dim=64)
    handler = make_db_server(db)
    server = HttpServer(handler, host="127.0.0.1", port=0)
    await server.start()
    try:
        import asyncio

        def call(method, path, body=None):
            conn = http.client.HTTPConnection(server.host, server.port, timeout=10)
            conn.request(method, path,
                         body=json.dumps(body).encode() if body is not None else None,
                         headers={"Content-Type": "application/json"})
            r = conn.getresponse()
            data = r.read()
            conn.close()
            return r.status, json.loads(data)

        health = await asyncio.to_thread(call, "GET", "/health")
        assert health[0] == 200 and health[1]["ok"] is True
        ing = await asyncio.to_thread(call, "POST", "/v1/ingest", {
            "namespace": "ns1",
            "texts": ["openai compatible vector db", "cooking pasta at home"],
        })
        assert ing[0] == 200 and ing[1]["count"] == 2
        q = await asyncio.to_thread(call, "POST", "/v1/query", {
            "namespace": "ns1", "query": "vector database", "n": 2})
        assert q[0] == 200
        assert len(q[1]["hits"]) == 2
        assert all(h["id"] in ing[1]["ids"] for h in q[1]["hits"])
        ns = await asyncio.to_thread(call, "GET", "/v1/namespaces")
        assert ns[0] == 200 and "ns1" in ns[1]["namespaces"]
    finally:
        await server.stop()
        db.close()


async def test_db_server_token_auth(tmp_path):
    db = DBManager(backend="memory", dim=32)
    handler = make_db_server(db, token="sekrit")
    server = HttpServer(handler, host="127.0.0.1", port=0)
    await server.start()
    try:
        import asyncio

        def call(method, path, token=None):
            conn = http.client.HTTPConnection(server.host, server.port, timeout=10)
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            conn.request(method, path, headers=headers)
            r = conn.getresponse()
            data = r.read()
            conn.close()
            return r.status, json.loads(data) if data else None

        bad = await asyncio.to_thread(call, "GET", "/health", token="wrong")
        assert bad[0] == 401
        none = await asyncio.to_thread(call, "GET", "/health")
        assert none[0] == 401
        good = await asyncio.to_thread(call, "GET", "/health", token="sekrit")
        assert good[0] == 200
    finally:
        await server.stop()
        db.close()
