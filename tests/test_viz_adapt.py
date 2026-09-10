"""Adapting foreign tool catalogs (V11), and the snapshot gate (V14).

The measured adoption barrier was a one-liner:

    >>> build_viz_model(my_openai_functions)
    AttributeError: 'list' object has no attribute 'all'

These tests pin the fix, and - more importantly - pin the thing the fix must
never do: invent safety facts. A guessed risk class is worse than a blank,
because the page then *looks* authoritative about a property nobody checked.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from skeletonkey.core.manifest import ToolManifest
from skeletonkey.viz import build_viz_model, render_html, summary_lines
from skeletonkey.viz.adapt import ADAPTED_SOURCE, as_registry, to_manifests

REPO = Path(__file__).resolve().parents[1]

OPENAI = [
    {"type": "function", "function": {
        "name": "db.query", "description": "Run a read-only SQL query",
        "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "db.migrate", "description": "Apply a migration", "risk": "write"}},
]

MCP_LIST = {"tools": [
    {"name": "fs_read", "description": "Read a file",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "fs_write", "description": "Write a file", "risk": "write"},
]}

PLAIN = [
    {"name": "a.one", "description": "First"},
    {"name": "b.two", "description": "Second"},
]


def _sk(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ, PYTHONPATH=str(REPO))
    return subprocess.run([sys.executable, "-m", "skeletonkey.cli", *argv],
                          capture_output=True, text=True, env=env,
                          cwd=str(cwd or REPO), timeout=300)


# --------------------------------------------------------------------------
# the barrier itself
# --------------------------------------------------------------------------

def test_the_exact_snippet_that_used_to_fail():
    model = build_viz_model([
        {"name": "db.query", "description": "Run a read-only SQL query"},
        {"name": "db.migrate", "description": "Apply a migration"},
        {"name": "email.send", "description": "Send an email"},
    ])
    assert len(model.nodes) == 3
    assert {n.id for n in model.nodes} == {"db.query", "db.migrate", "email.send"}


@pytest.mark.parametrize("source,count", [
    (OPENAI, 2), (MCP_LIST, 2), (PLAIN, 2),
    ({"x.one": {"description": "mapping form"}}, 1),
    ([ToolManifest(id="native.tool", handler=lambda: None)], 1),
])
def test_every_accepted_shape_renders(source, count):
    model = build_viz_model(source)
    assert len(model.nodes) == count
    assert len(render_html(model)) > 1000


def test_a_real_registry_passes_straight_through(toolkit):
    reg = toolkit.engine.registry
    assert as_registry(reg) is reg


def test_mixed_manifests_and_dicts():
    model = build_viz_model([
        {"name": "adapted.one"},
        ToolManifest(id="native.two", handler=lambda: None),
    ])
    assert len(model.nodes) == 2


# --------------------------------------------------------------------------
# what it must never do: invent safety facts
# --------------------------------------------------------------------------

def test_undeclared_risk_is_marked_not_guessed():
    """The whole safety argument for this module."""
    model = build_viz_model([{"name": "db.delete_everything",
                              "description": "Drop all tables"}])
    node = model.nodes[0]
    assert node.risk_declared is False, "an undeclared risk must be flagged"


def test_a_scary_name_does_not_become_a_risk_claim():
    """`delete`, `drop`, `rm` in a name are exactly the tempting heuristic.
    Guessing would be wrong in both directions and unfalsifiable.

    The check is that the risk of an undeclared tool does not VARY with its
    name: if it did, the adapter is reading meaning into the string. Asserting
    only `risk_declared is False` was too weak - a name-sniffing adapter can
    set a scary risk and still flag it as undeclared, and the page would then
    show a red badge nobody stood behind."""
    scary = [build_viz_model([{"name": n}]).nodes[0]
             for n in ("db.delete_all", "fs.rm_rf", "prod.drop_database")]
    calm = build_viz_model([{"name": "docs.read_page"}]).nodes[0]

    for node in scary:
        assert node.risk_declared is False
        assert node.destructive is False, f"invented destructive=True for {node.id}"
        assert node.risk == calm.risk, (
            f"{node.id} got risk={node.risk!r} but the calm tool got "
            f"{calm.risk!r} - the adapter is guessing from the name")


def test_declared_risk_is_honoured():
    node = build_viz_model([{"name": "db.migrate", "risk": "write"}]).nodes[0]
    assert node.risk == "write" and node.risk_declared is True


def test_an_unrecognised_risk_word_is_not_silently_accepted():
    mans = to_manifests([{"name": "x.y", "risk": "kinda-dangerous"}])
    meta = mans[0].meta
    assert meta["unknown_risk"] is True
    assert meta["declared_risk_unrecognised"] == "kinda-dangerous"


def test_the_page_says_not_declared():
    page = render_html(build_viz_model([{"name": "a.b", "description": "x"}]))
    assert "risk not declared" in page
    page2 = render_html(build_viz_model([{"name": "a.b", "risk": "write"}]))
    assert "risk not declared" not in page2


def test_the_summary_agrees_with_the_page():
    """A snapshot that prints `read` while the page says `not declared` would
    let a reviewer draw the opposite conclusion from the same model."""
    text = summary_lines(build_viz_model([{"name": "a.b"}]))
    assert "undeclared" in text
    assert " read " not in text


def test_adapted_tools_are_labelled_as_adapted():
    assert to_manifests(PLAIN)[0].source == ADAPTED_SOURCE


def test_an_adapted_tool_cannot_be_called():
    """It is documentation, not an implementation. Calling must fail loudly
    rather than return something a caller might trust."""
    man = to_manifests([{"name": "a.b"}])[0]
    with pytest.raises(RuntimeError, match="visualisation only"):
        man.handler()


# --------------------------------------------------------------------------
# conversion details
# --------------------------------------------------------------------------

def test_group_is_inferred_from_a_dotted_id():
    model = build_viz_model([{"name": "db.query"}, {"name": "db.migrate"},
                             {"name": "email.send"}])
    assert {n.group for n in model.nodes} == {"db", "email"}


def test_an_undotted_id_gets_a_neutral_group():
    assert build_viz_model([{"name": "search"}]).nodes[0].group == "tools"


def test_schemas_survive_under_every_vendor_key():
    for key in ("parameters", "inputSchema", "input_schema", "schema"):
        man = to_manifests([{"name": "a.b", key: {"type": "object",
                                                  "properties": {"q": {}}}}])[0]
        assert man.input_schema.get("properties", {}).get("q") is not None, key


def test_a_tool_with_no_name_is_a_clear_error():
    with pytest.raises(ValueError, match="no id/name"):
        to_manifests([{"description": "nameless"}])


def test_a_non_dict_entry_is_a_clear_error():
    with pytest.raises(TypeError, match="cannot adapt"):
        to_manifests(["just a string"])


def test_duplicate_ids_do_not_explode():
    model = build_viz_model([{"name": "a.b", "description": "first"},
                             {"name": "a.b", "description": "second"}])
    assert len(model.nodes) == 1
    assert model.nodes[0].description == "second"      # last wins


def test_adapted_tools_go_through_the_real_gate(toolkit):
    """Adapted tools must not be a weaker second code path: they get the same
    advertise() evaluation, so explain works on them too."""
    from skeletonkey.viz.explain import explain_model

    reg = as_registry(PLAIN)
    chains = explain_model(reg)
    assert set(chains) == {"a.one", "b.two"}
    for chain in chains.values():
        assert chain.to_json()["steps"]


def test_empty_input_is_an_empty_model():
    assert build_viz_model([]).nodes == []


# --------------------------------------------------------------------------
# the CLI surfaces
# --------------------------------------------------------------------------

def test_viz_from_renders_a_foreign_catalog(tmp_path):
    src = tmp_path / "tools.json"
    src.write_text(json.dumps(OPENAI), encoding="utf-8")
    out = tmp_path / "page.html"
    proc = _sk("viz", "--from", str(src), "-o", str(out))
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "db.query" in out.read_text(encoding="utf-8")


def test_viz_from_works_with_summary(tmp_path):
    src = tmp_path / "tools.json"
    src.write_text(json.dumps(MCP_LIST), encoding="utf-8")
    proc = _sk("viz", "--from", str(src), "--summary")
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "fs_read" in proc.stdout and "registered      2" in proc.stdout


def test_viz_from_reports_a_bad_file_clearly(tmp_path):
    bad = tmp_path / "nope.json"
    bad.write_text("{not json", encoding="utf-8")
    proc = _sk("viz", "--from", str(bad), "--summary")
    assert proc.returncode == 2
    assert "cannot read" in proc.stderr

    missing = _sk("viz", "--from", str(tmp_path / "absent.json"), "--summary")
    assert missing.returncode == 2
    assert "cannot read" in missing.stderr


def test_viz_from_reports_unadaptable_content(tmp_path):
    src = tmp_path / "tools.json"
    src.write_text(json.dumps(["a string", "another"]), encoding="utf-8")
    proc = _sk("viz", "--from", str(src), "--summary")
    assert proc.returncode == 2
    assert "cannot adapt" in proc.stderr


# --------------------------------------------------------------------------
# V14: the snapshot as a CI gate
# --------------------------------------------------------------------------

def test_check_passes_on_a_current_snapshot(tmp_path):
    snap = tmp_path / "TOOLSET.txt"
    assert _sk("viz", "--summary", "-o", str(snap)).returncode == 0
    proc = _sk("viz", "--summary", "--check", "-o", str(snap))
    assert proc.returncode == 0
    assert "current" in proc.stderr


def test_check_fails_with_a_readable_diff_when_stale(tmp_path):
    snap = tmp_path / "TOOLSET.txt"
    _sk("viz", "--summary", "-o", str(snap))
    snap.write_text(snap.read_text(encoding="utf-8").replace(
        "registered ", "registered      999 #"), encoding="utf-8")

    proc = _sk("viz", "--summary", "--check", "-o", str(snap))
    assert proc.returncode == 1
    assert "---" in proc.stdout and "+++" in proc.stdout      # a real diff
    assert "sk viz --summary -o" in proc.stderr               # and the fix


def test_check_does_not_rewrite_the_file(tmp_path):
    """A gate that silently fixes the thing it is gating is not a gate."""
    snap = tmp_path / "TOOLSET.txt"
    _sk("viz", "--summary", "-o", str(snap))
    stale = snap.read_text(encoding="utf-8").replace("withheld  ", "withheld 9")
    snap.write_text(stale, encoding="utf-8")

    _sk("viz", "--summary", "--check", "-o", str(snap))
    assert snap.read_text(encoding="utf-8") == stale, "--check rewrote the snapshot"


def test_check_on_a_missing_file_explains_itself(tmp_path):
    proc = _sk("viz", "--summary", "--check", "-o", str(tmp_path / "none.txt"))
    assert proc.returncode == 1
    assert "does not exist" in proc.stderr


def test_check_without_output_is_a_usage_error():
    proc = _sk("viz", "--summary", "--check")
    assert proc.returncode == 2
    assert "needs -o" in proc.stderr


def test_the_committed_snapshot_passes_its_own_gate():
    """The gate CI will run, run here."""
    proc = _sk("viz", "--summary", "--check", "-o", "docs/TOOLSET.txt")
    assert proc.returncode == 0, proc.stdout[-2000:]
