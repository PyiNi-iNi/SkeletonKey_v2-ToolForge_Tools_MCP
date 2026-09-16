"""Integration tests for features.document_processor (zero heavy deps)."""

from __future__ import annotations

import io
import json
import re
import struct
import zlib

import pytest

from features.common.errors import BackendUnavailableError, NotFoundError, ValidationError
from features.db_manager import DBManager, HashingEmbedder
from features.document_processor.chunking import (
    RecursiveChunker,
    SemanticChunker,
    split_sentences,
)
from features.document_processor.extractors import extractor_for
from features.document_processor.ocr import PassthroughOCR, make_ocr
from features.document_processor.pipeline import IngestionPipeline


# --------------------------------------------------------------------------
# Fixtures: tiny real files, built with stdlib only
# --------------------------------------------------------------------------


def make_pdf(lines: list[str], compress: bool = False) -> bytes:
    """Build a minimal single/multi-page text PDF."""
    objs: list[bytes] = []
    n_pages = max(1, (len(lines) + 1) // 2)
    content_ids = list(range(5, 5 + n_pages))
    page_ids = [i + 2 for i in range(n_pages)]

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    add(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())

    font_id = 4
    for p in range(n_pages):
        content_id = content_ids[p]
        page_body = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        ).encode()
        add(page_body)
    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for p in range(n_pages):
        text_ops = []
        for j, line in enumerate(lines[p * 2 : p * 2 + 2]):
            y = 720 - j * 30
            safe = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            text_ops.append(f"BT /F1 12 Tf 72 {y} Td ({safe}) Tj ET".encode())
        content = b"\n".join(text_ops)
        if compress:
            content = zlib.compress(content)
        stream = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
        add(stream)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode())
        out.write(body)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n".encode())
    out.write(f"startxref\n{xref_pos}\n".encode())
    out.write(b"%%EOF")
    return out.getvalue()


def make_xlsx(rows: list[list[str]]) -> bytes:
    """Build a minimal valid xlsx (zip of XML parts) with stdlib only."""
    strings: list[str] = []

    def sidx(val: str) -> int:
        if val not in strings:
            strings.append(val)
        return strings.index(val)

    sheet_rows = []
    for r_i, row in enumerate(rows, start=1):
        cells = []
        for c_i, val in enumerate(row):
            ref = _colref(c_i) + str(r_i)
            if val == "" or not _is_number(val):
                cells.append(f'<c r="{ref}" t="s"><v>{sidx(val)}</v></c>')
            else:
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
        sheet_rows.append(f'<row r="{r_i}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    sst_items = "".join(f"<si><t>{s}</t></si>" for s in strings)
    sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">{sst_items}</sst>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    buf = io.BytesIO()
    import zipfile

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr("xl/sharedStrings.xml", sst_xml)
    return buf.getvalue()


def _colref(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _is_number(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------
# Sentence splitting
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected_n",
    [
        ("One. Two. Three!", 3),
        ("First sentence. Second question? Third exclamation!", 3),
        ("No punctuation here", 1),
        ("Line one\n\nLine two.", 2),
        ("", 0),
    ],
)
def test_split_sentences(text, expected_n):
    sents = split_sentences(text)
    assert len(sents) == expected_n
    for piece, a, b in sents:
        assert text[a:b] == piece


def test_split_sentence_offsets_reconstruct():
    text = "Alpha beta gamma. Delta epsilon. Zeta eta."
    sents = split_sentences(text)
    assert all(text[a:b].strip() for _, a, b in sents)
    assert sents[0][1] == 0
    assert sents[-1][2] == len(text)


# --------------------------------------------------------------------------
# Semantic chunker
# --------------------------------------------------------------------------

TWO_TOPIC = (
    "The mitochondria produces energy for the cell. "
    "Respiration happens inside the inner membrane. "
    "ATP is the energy currency of life. "
    "Chroma is a vector database for embeddings. "
    "FAISS provides fast approximate nearest neighbor search. "
    "HNSW indexes support billion scale retrieval."
)


class MockTopicEmbedder:
    """Deterministic two-topic embedder: exact vectors for algorithm tests."""

    dim = 2

    def _vec(self, text: str) -> list[float]:
        low = text.lower()
        bio = any(w in low for w in ("mitochondria", "respiration", "atp", "cell",
                                     "membrane", "energy", "life"))
        db = any(w in low for w in ("chroma", "faiss", "hnsw", "vector", "database",
                                    "embedding", "search", "index"))
        if bio and not db:
            return [1.0, 0.0]
        if db and not bio:
            return [0.0, 1.0]
        return [1.0 / 5 ** 0.5, 1.0 / 5 ** 0.5]  # normalized mixed

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


def test_semantic_chunker_finds_topic_break():
    chunks = SemanticChunker(embedder=MockTopicEmbedder(), min_chunk_chars=20).chunk(TWO_TOPIC)
    assert len(chunks) == 2
    first, second = chunks[0].text, chunks[1].text
    assert "mitochondria" in first and "Chroma" not in first
    assert "Chroma" in second and "mitochondria" not in second


def test_semantic_chunker_hash_embedder_properties():
    # the zero-dep embedder is noisy; assert structural properties only
    emb = HashingEmbedder(dim=512)
    chunks = SemanticChunker(embedder=emb).chunk(TWO_TOPIC)
    assert len(chunks) >= 1
    assert all(c.text.strip() for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_semantic_chunker_coverage_and_order():
    emb = HashingEmbedder(dim=512)
    chunks = SemanticChunker(embedder=emb).chunk(TWO_TOPIC)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(chunks[i].start <= chunks[i + 1].start for i in range(len(chunks) - 1))
    covered = set()
    for c in chunks:
        covered.update(range(c.start, c.end))
    # most of the text is covered (whitespace at boundaries is ok)
    assert len(covered) > 0.9 * len(TWO_TOPIC.strip())


def test_semantic_chunker_max_size_guard():
    emb = HashingEmbedder(dim=256)
    long_text = " ".join(f"word{i} about vector databases." for i in range(400))
    chunks = SemanticChunker(embedder=emb, target_chunk_chars=300,
                             min_chunk_chars=50).chunk(long_text)
    assert max(len(c.text) for c in chunks) <= 300 + 40  # +long-word slack
    assert len(chunks) > 1


def test_semantic_chunker_hard_split_unpunctuated():
    emb = HashingEmbedder(dim=128)
    long_one_sentence = "x" * 2000
    chunks = SemanticChunker(embedder=emb, target_chunk_chars=400).chunk(long_one_sentence)
    assert max(len(c.text) for c in chunks) <= 400
    assert len(chunks) >= 5


def test_semantic_chunker_short_text_single_chunk():
    emb = HashingEmbedder(dim=128)
    chunks = SemanticChunker(embedder=emb).chunk("Just a short note.")
    assert len(chunks) == 1
    assert chunks[0].text == "Just a short note."


def test_semantic_chunker_empty():
    emb = HashingEmbedder(dim=64)
    assert SemanticChunker(embedder=emb).chunk("   ") == []


def test_chunker_bad_params():
    with pytest.raises(ValidationError):
        SemanticChunker(HashingEmbedder(dim=8), target_chunk_chars=10, min_chunk_chars=20)
    with pytest.raises(ValidationError):
        RecursiveChunker(chunk_size=100, chunk_overlap=100)


# --------------------------------------------------------------------------
# Recursive chunker
# --------------------------------------------------------------------------


def test_recursive_chunker_size_and_coverage():
    text = ("Paragraph one about mitochondria and energy. " * 10 + "\n\n" +
            "Paragraph two about vector databases and indexes. " * 10)
    chunks = RecursiveChunker(chunk_size=200, chunk_overlap=20).chunk(text)
    assert all(len(c.text) <= 200 for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert "mitochondria" in joined and "vector databases" in joined


def test_recursive_chunker_offsets():
    text = "aaaa bbbb cccc dddd eeee"
    chunks = RecursiveChunker(chunk_size=15, chunk_overlap=5).chunk(text)
    for c in chunks:
        assert text[c.start : c.end] == c.text


def test_recursive_chunker_single_char_split():
    text = "x" * 500
    chunks = RecursiveChunker(chunk_size=100, chunk_overlap=0).chunk(text)
    assert all(len(c.text) <= 100 for c in chunks)
    assert "".join(c.text for c in chunks) == text


# --------------------------------------------------------------------------
# Extractors
# --------------------------------------------------------------------------


def test_pdf_extractor_uncompressed(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(make_pdf(["Vector databases store embeddings", "FAISS is a search library"]))
    doc = extractor_for("a.pdf").extract(p.read_bytes(), "a.pdf")
    assert "Vector databases store embeddings" in doc.text
    assert "FAISS is a search library" in doc.text
    assert doc.metadata["backend"] == "raw"
    assert len(doc.blocks) >= 1


def test_pdf_extractor_compressed(tmp_path):
    p = tmp_path / "b.pdf"
    p.write_bytes(make_pdf(["Quantum entanglement is spooky action"], compress=True))
    doc = extractor_for("b.pdf").extract(p.read_bytes(), "b.pdf")
    assert "Quantum entanglement is spooky action" in doc.text


def test_pdf_extractor_robust_to_garbage(tmp_path):
    p = tmp_path / "c.pdf"
    p.write_bytes(b"%PDF-1.4 garbage \x00\x01 no streams here")
    doc = extractor_for("c.pdf").extract(p.read_bytes(), "c.pdf")
    assert isinstance(doc.text, str)


def test_excel_csv(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("name,value\nalpha,1\nbeta,2\n")
    doc = extractor_for("t.csv").extract(p.read_bytes(), "t.csv")
    assert doc.metadata["rows"] == 2
    assert doc.metadata["columns"] == 2
    assert "alpha" in doc.text and "beta" in doc.text


def test_excel_tsv(tmp_path):
    p = tmp_path / "t.tsv"
    p.write_text("a\tb\n1\t2\n")
    doc = extractor_for("t.tsv").extract(p.read_bytes(), "t.tsv")
    assert doc.metadata["columns"] == 2


def test_excel_xlsx_stdlib_reader(tmp_path):
    p = tmp_path / "t.xlsx"
    p.write_bytes(make_xlsx([["name", "value", "note"], ["Answer", "42", "the meaning"],
                             ["Pi", "3.14", "rounded"]]))
    doc = extractor_for("t.xlsx").extract(p.read_bytes(), "t.xlsx")
    assert doc.metadata["rows"] == 2
    assert doc.metadata["columns"] == 3
    assert "Answer" in doc.text and "42" in doc.text and "3.14" in doc.text
    assert doc.blocks[0]["row"] == 1


def test_excel_bad_extension(tmp_path):
    p = tmp_path / "t.xls"
    p.write_bytes(b"OLE garbage")
    with pytest.raises(BackendUnavailableError):
        extractor_for("t.xls").extract(p.read_bytes(), "t.xls")


def test_code_extractor_python_symbols(tmp_path):
    src = '''"""Mod docstring."""
import os

def alpha(x):
    return x * 2

class Beta:
    def method(self):
        return self


def gamma(a, b):
    return a + b
'''
    p = tmp_path / "m.py"
    p.write_text(src)
    doc = extractor_for("m.py").extract(p.read_bytes(), "m.py")
    assert doc.metadata["language"] == "python"
    symbols = [b["symbol"] for b in doc.blocks if b.get("symbol")]
    assert "alpha" in symbols and "Beta" in symbols and "gamma" in symbols
    alpha_block = next(b for b in doc.blocks if b.get("symbol") == "alpha")
    assert "return x * 2" in alpha_block["text"]


def test_code_extractor_other_langs(tmp_path):
    p = tmp_path / "s.sh"
    p.write_text("#!/bin/bash\necho hello\n")
    doc = extractor_for("s.sh").extract(p.read_bytes(), "s.sh")
    assert doc.metadata["language"] == "bash"


def test_text_extractor_notebook(tmp_path):
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n"]},
            {"cell_type": "code", "source": ["print(1)"],
             "outputs": [{"text": ["1\n"]}]},
        ]
    }
    p = tmp_path / "n.ipynb"
    p.write_text(json.dumps(nb))
    doc = extractor_for("n.ipynb").extract(p.read_bytes(), "n.ipynb")
    assert "# Title" in doc.text
    assert "print(1)" in doc.text
    assert "\n1" in doc.text


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------


def test_passthrough_ocr():
    ocr = PassthroughOCR()
    assert ocr.extract(b"\x89PNG fake") == ""


def test_make_ocr_auto_falls_back():
    # docling not installed in CI
    ocr = make_ocr()
    assert ocr.name == "passthrough"


def test_make_ocr_explicit_doctr_missing():
    with pytest.raises(BackendUnavailableError) as ei:
        make_ocr(prefer="doctr")
    assert "pip install" in (ei.value.hint or "")


# --------------------------------------------------------------------------
# Pipeline (end to end)
# --------------------------------------------------------------------------


@pytest.fixture
def pipeline_db(tmp_path):
    db = DBManager(backend="sqlite", path=str(tmp_path / "doc.db"), dim=128)
    yield db
    db.close()


async def test_pipeline_events_in_order(tmp_path, pipeline_db):
    p = tmp_path / "notes.txt"
    p.write_text(TWO_TOPIC)
    pipe = IngestionPipeline(db=pipeline_db)
    events = []
    async for ev in pipe.aprocess(str(p)):
        events.append(ev["stage"])
    assert events == ["extract", "extract", "chunk", "chunk", "embed", "store", "done"]


async def test_pipeline_ingest_then_query(tmp_path, pipeline_db):
    p = tmp_path / "topic.txt"
    p.write_text(TWO_TOPIC)
    pipe = IngestionPipeline(db=pipeline_db)
    result = await pipe.aingest(str(p))
    assert result.n_chunks >= 1
    assert len(result.ids) == result.n_chunks
    hits = await pipe.aquery("vector database", n=2)
    assert hits, "expected at least one hit"
    assert all(h["metadata"]["source"] == "topic.txt" for h in hits)


async def test_pipeline_pdf_end_to_end(tmp_path, pipeline_db):
    p = tmp_path / "paper.pdf"
    p.write_bytes(make_pdf(["FAISS is a library for similarity search",
                            "It supports billion scale indexes"]))
    pipe = IngestionPipeline(db=pipeline_db)
    result = await pipe.aingest(str(p))
    assert result.format == "pdf"
    assert result.n_chunks >= 1
    hits = await pipe.aquery("similarity search library", n=1)
    assert hits[0]["metadata"]["source"] == "paper.pdf"


async def test_pipeline_missing_file(tmp_path, pipeline_db):
    pipe = IngestionPipeline(db=pipeline_db)
    with pytest.raises(NotFoundError) as ei:
        await pipe.aingest(str(tmp_path / "nope.txt"))
    assert ei.value.code == "not_found"


async def test_pipeline_on_progress_callback(tmp_path, pipeline_db):
    p = tmp_path / "x.txt"
    p.write_text(TWO_TOPIC)
    seen: list = []

    async def cb(result):
        seen.append(result.n_chunks)

    pipe = IngestionPipeline(db=pipeline_db)
    await pipe.aingest(str(p), on_progress=cb)
    assert seen and seen[0] == pipe._last_result.n_chunks


async def test_pipeline_without_db_still_chunks(tmp_path):
    p = tmp_path / "y.txt"
    p.write_text(TWO_TOPIC)
    pipe = IngestionPipeline(db=None)
    events = [ev async for ev in pipe.aprocess(str(p))]
    assert events[-1]["stage"] == "done"
    assert events[-1]["result"]["n_chunks"] >= 1
    with pytest.raises(ValidationError):
        await pipe.aquery("anything")


def test_pipeline_load_sync(tmp_path):
    p = tmp_path / "z.csv"
    p.write_text("a,b\n1,2\n")
    pipe = IngestionPipeline(db=None)
    doc_id, chunks, meta = pipe.load(str(p))
    assert doc_id
    assert chunks
    assert meta["kind"] == "spreadsheet"
