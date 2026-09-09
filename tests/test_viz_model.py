"""V0 of the tool visualizer: the model must describe the *whole* toolset, honestly.

The acceptance criteria from `docs/TOOL-VISUALIZER-PLAN.md` §V0, executed:

* every registered tool appears exactly once (not just the advertised ones);
* `advertised` agrees with the advertisement snapshot, tool for tool;
* every withheld tool carries a machine-readable cause and a human reason;
* no secret_args value and no credential-shaped string reaches the document;
* the model is deterministic - same registry state, byte-identical JSON;
* edges only ever join nodes that exist.

Counts are asserted as *relationships* (registered >= advertised, and so on)
rather than hard numbers, so adding a tool does not fail the suite. The one
place exact numbers live is `test_matches_describe`, which cross-checks the
model against `toolkit.describe()` - if those two ever disagree, one of them is
lying about the toolset.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from skeletonkey.core.manifest import Requirement, ToolManifest
from skeletonkey.viz import build_viz_model, model_digest
from skeletonkey.viz.model import EDGE_KINDS, SCHEMA_VERSION

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def model(toolkit):
    return build_viz_model(toolkit.engine.registry)


# --------------------------------------------------------------- completeness

def test_every_registered_tool_is_a_node_exactly_once(toolkit, model):
    registered = [m.id for m in toolkit.engine.registry.all()]
    ids = [n.id for n in model.nodes]
    assert sorted(ids) == sorted(registered)
    assert len(ids) == len(set(ids)), "a tool appeared twice in the model"
    assert model.aggregates["registered"] == len(registered)


def test_advertised_flag_matches_the_snapshot(toolkit, model):
    snap = toolkit.engine.registry.advertise(include_internal=False)
    advertised = {n.id for n in model.nodes if n.advertised}
    assert advertised == set(snap.names)
    assert model.aggregates["advertised"] == len(snap.tools)
    assert model.aggregates["advertised"] + model.aggregates["withheld"] == \
        model.aggregates["registered"]
    assert model.snapshot["digest"] == snap.digest
    assert model.snapshot["tokens"] == snap.tokens


def test_withheld_tools_all_name_a_cause(model):
    withheld = [n for n in model.nodes if not n.advertised]
    for node in withheld:
        assert node.withheld_by, f"{node.id} is withheld with no cause"
        assert node.withheld_by != "unknown", f"{node.id}: cause fell through to 'unknown'"
        reason = (node.gate.get("reasons") or node.gate.get("unmet")
                  or [node.hidden_reason, node.budget_drop])
        assert any(str(r).strip() for r in reason), f"{node.id}: cause has no prose"
    assert sum(model.aggregates["withheld_by"].values()) == len(withheld)


def test_advertised_tools_are_never_marked_withheld(model):
    for node in model.nodes:
        if node.advertised:
            assert node.withheld_by == ""
            assert node.budget_drop == ""


# ---------------------------------------------------------------------- edges

def test_edges_only_join_existing_nodes_and_use_known_kinds(model):
    ids = {n.id for n in model.nodes}
    for edge in model.edges:
        assert edge.kind in EDGE_KINDS
        assert edge.source in ids, f"dangling edge source {edge.source}"
        assert edge.target in ids, f"dangling edge target {edge.target}"
        assert edge.source != edge.target


def test_edges_are_deduplicated_and_sorted(model):
    keys = [e.key() for e in model.edges]
    assert len(keys) == len(set(keys))
    assert keys == sorted(keys)


def test_see_also_edges_mirror_the_manifests(toolkit, model):
    ids = {m.id for m in toolkit.engine.registry.all()}
    expected = {
        (m.id, t) for m in toolkit.engine.registry.all()
        for t in (m.see_also or []) if t in ids and t != m.id
    }
    got = {(e.source, e.target) for e in model.edges_of("see_also")}
    assert got == expected


def test_same_capability_edges_appear_once_per_pair(model):
    for edge in model.edges_of("same_capability"):
        assert edge.source < edge.target, "undirected pair emitted in both directions"
        a, b = model.node(edge.source), model.node(edge.target)
        assert a.capability == b.capability == edge.label


# ------------------------------------------------------------------- rollups

def test_group_and_capability_rollups_add_up(model):
    assert sum(g["tools"] for g in model.groups) == len(model.nodes)
    assert sum(g["advertised"] for g in model.groups) == model.aggregates["advertised"]
    assert len(model.groups) == model.aggregates["groups"]

    covered = sorted(t for c in model.capabilities for t in c["tools"])
    assert covered == sorted(n.id for n in model.nodes)
    assert len(model.capabilities) == model.aggregates["capabilities"]


def test_token_costs_are_reported_per_node_and_in_total(model):
    for node in model.nodes:
        assert node.token_cost > 0
    advertised = sum(n.token_cost for n in model.nodes if n.advertised)
    assert advertised == model.snapshot["tokens"]
    assert model.aggregates["token_cost_registered"] >= advertised


def test_matches_describe(toolkit, model):
    """The model and `toolkit.describe()` must agree, exactly, on the toolset."""
    desc = toolkit.describe()["tools"]
    assert model.aggregates["registered"] == desc["registered"]
    assert model.aggregates["advertised"] == desc["advertised"]
    assert model.snapshot["tokens"] == desc["estimated_tokens"]
    assert model.snapshot["digest"] == desc["digest"]


# --------------------------------------------------------------- determinism

def test_model_is_byte_identical_across_builds(toolkit):
    a = build_viz_model(toolkit.engine.registry)
    b = build_viz_model(toolkit.engine.registry)
    assert json.dumps(a.to_json()["nodes"]) == json.dumps(b.to_json()["nodes"])
    assert json.dumps(a.to_json()["edges"]) == json.dumps(b.to_json()["edges"])
    assert a.digest() == b.digest()


def test_digest_ignores_the_clock_but_notices_a_new_tool(toolkit):
    reg = toolkit.engine.registry
    before = build_viz_model(reg).digest()
    reg.register(ToolManifest(id="viz.fixture", description="A fixture tool.",
                              risk="none"), handler=lambda **kw: None)
    after = build_viz_model(reg)
    assert after.digest() != before
    assert after.node("viz.fixture") is not None
    assert model_digest(after) == after.digest()


def test_schema_version_is_stamped(model):
    assert model.to_json()["schema_version"] == SCHEMA_VERSION


# ------------------------------------------------------------------- secrets

_CREDENTIALS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]


def test_no_secret_arg_or_credential_reaches_the_document(toolkit):
    reg = toolkit.engine.registry
    reg.register(
        ToolManifest(
            id="viz.secretful",
            description="A tool with a credential argument.",
            risk="none",
            secret_args=["token"],
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "token": {"type": "string", "default": "ghp_" + "a" * 36},
                },
                "required": ["url", "token"],
            },
            examples=[{"args": {"url": "https://x", "token": "ghp_" + "b" * 36}}],
        ),
        handler=lambda **kw: None,
    )
    blob = json.dumps(build_viz_model(reg).to_json())
    assert "token" not in json.dumps(
        build_viz_model(reg).node("viz.secretful").input_schema)
    for pattern in _CREDENTIALS:
        assert not pattern.search(blob), f"credential-shaped string leaked: {pattern.pattern}"


# --------------------------------------------------------- gating projections

def test_read_only_projection_withholds_every_mutating_tool(toolkit):
    model = build_viz_model(toolkit.engine.registry, read_only=True)
    assert model.host["read_only"] is True
    for node in model.nodes:
        if node.mutating:
            assert not node.advertised, f"{node.id} is mutating but advertised read-only"
            assert node.withheld_by in ("gate", "internal", "tier")
    assert model.aggregates["advertised"] < len(model.nodes)


def test_tier_projection_names_tier_as_the_cause(toolkit):
    model = build_viz_model(toolkit.engine.registry, tier="core")
    assert model.snapshot["tier"] == "core"
    dropped = [n for n in model.nodes if n.tier in ("task", "full") and not n.advertised]
    assert dropped, "no tool was withheld by the core tier - fixture drifted"
    assert any(n.withheld_by == "tier" for n in dropped)
    for node in model.nodes:
        if node.advertised:
            assert node.tier == "core"


def test_an_unmet_requirement_shows_up_as_a_gate_with_the_unmet_list(toolkit):
    reg = toolkit.engine.registry
    reg.register(
        ToolManifest(id="viz.needs_unicorn", description="Needs a binary nobody has.",
                     risk="none",
                     requirements=[Requirement("binary", "definitely-not-installed-xyz")]),
        handler=lambda **kw: None,
    )
    node = build_viz_model(reg).node("viz.needs_unicorn")
    assert node.advertised is False
    assert node.withheld_by == "gate"
    assert node.gate["available"] is False
    assert node.gate.get("unmet"), "an unmet requirement must be listed on the gate"
    assert node.requires and node.requires[0]["name"] == "definitely-not-installed-xyz"


# --------------------------------------------------------------- purity / CLI

def test_building_a_model_does_not_mutate_the_registry(toolkit):
    reg = toolkit.engine.registry
    before = sorted(m.id for m in reg.all())
    digest_before = reg.advertise().digest
    build_viz_model(reg)
    build_viz_model(reg, read_only=True, tier="core")
    assert sorted(m.id for m in reg.all()) == before
    assert reg.advertise().digest == digest_before


def test_cli_viz_json_is_the_same_document(tmp_path):
    out = tmp_path / "model.json"
    proc = subprocess.run(
        [sys.executable, "-m", "skeletonkey.cli", "viz", "--json", "-o", str(out)],
        capture_output=True, text=True, cwd=str(REPO), timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["model_digest"]
    assert len(doc["nodes"]) == doc["aggregates"]["registered"]
    assert {n["id"] for n in doc["nodes"]} >= {"fs.read", "shell.run", "registry.list"}


def test_no_schemas_and_no_examples_shrink_the_document(toolkit):
    full = build_viz_model(toolkit.engine.registry)
    lean = build_viz_model(toolkit.engine.registry,
                           include_schemas=False, include_examples=False)
    assert len(json.dumps(lean.to_json())) < len(json.dumps(full.to_json()))
    assert all(n.input_schema == {} for n in lean.nodes)
    assert all(n.examples == [] for n in lean.nodes)
    # counts survive even when the payload is dropped
    assert sum(n.example_count for n in lean.nodes) == sum(n.example_count for n in full.nodes)
