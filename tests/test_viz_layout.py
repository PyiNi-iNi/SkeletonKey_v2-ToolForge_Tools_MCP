"""V3 of the tool visualizer: layout maths and the alternative views.

Layout lives in Python precisely so it can be tested like this - without a
browser, and as arithmetic rather than as pixels. The properties asserted here
are the ones that decide whether the graph is *readable*:

* nothing overlaps (nodes, and the group discs they live in);
* everything stays on the canvas;
* clusters are sized by membership, so the eye reads "how much is here";
* it is deterministic - the same model lays out identically every time, on
  every machine, which is what makes a snapshot meaningful;
* it degenerates gracefully: 0, 1 and 2 tools, one group, a thousand tools.

The disc-packing case has teeth: the first implementation sized clusters by
angular share alone and the big ones overlapped by most of their radius, so the
whole graph read as a single blob. `test_group_discs_do_not_overlap` is what
caught it and what keeps it fixed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import pytest

from skeletonkey.viz import build_viz_model
from skeletonkey.viz.layout import (
    graph_layout,
    latency_bands,
    matrix,
    token_treemap,
    treemap,
)
from skeletonkey.viz.views import (
    group_colours,
    render_graph_svg,
    render_latency_html,
    render_matrix_html,
    render_treemap_svg,
)

# ---- a tiny stand-in model, so layout tests do not depend on the real toolset

@dataclass
class FakeNode:
    id: str
    group: str = "g"
    token_cost: int = 50
    risk: str = "read"
    tier: str = "full"
    advertised: bool = True
    destructive: bool = False
    typical_latency_ms: int = 20
    capability: str = ""
    provider: str | None = None


@dataclass
class FakeEdge:
    source: str
    target: str
    kind: str = "see_also"
    label: str = ""


@dataclass
class FakeModel:
    nodes: list[FakeNode] = field(default_factory=list)
    edges: list[FakeEdge] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=lambda: {"tokens": 100})


def fake(counts: dict[str, int], *, costs: dict[str, int] | None = None) -> FakeModel:
    nodes = []
    for group, n in counts.items():
        for i in range(n):
            tid = f"{group}.t{i}"
            nodes.append(FakeNode(id=tid, group=group,
                                  token_cost=(costs or {}).get(tid, 40 + i)))
    return FakeModel(nodes=nodes)


def overlaps(placed: list[dict[str, Any]], slack: float = 0.5) -> list[tuple[str, str]]:
    out = []
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            d = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if d < a["r"] + b["r"] - slack:
                out.append((a["id"], b["id"]))
    return out


@pytest.fixture
def model(toolkit):
    return build_viz_model(toolkit.engine.registry)


# ------------------------------------------------------------------- geometry

def test_no_two_nodes_overlap(model):
    layout = graph_layout(model)
    assert overlaps(layout["nodes"]) == []


def test_group_discs_do_not_overlap(model):
    """Clusters must stay visually distinct - the blob regression."""
    discs = graph_layout(model)["groups"]
    clashes = []
    for i, a in enumerate(discs):
        for b in discs[i + 1:]:
            d = math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"])
            if d < a["radius"] + b["radius"] - 0.5:
                clashes.append((a["name"], b["name"],
                                round(a["radius"] + b["radius"] - d, 1)))
    assert clashes == [], f"group discs overlap: {clashes}"


def test_everything_stays_on_the_canvas(model):
    layout = graph_layout(model)
    for node in layout["nodes"]:
        assert node["r"] <= node["x"] <= layout["width"] - node["r"]
        assert node["r"] <= node["y"] <= layout["height"] - node["r"]


def test_every_node_is_placed_exactly_once(model):
    layout = graph_layout(model)
    ids = [n["id"] for n in layout["nodes"]]
    assert sorted(ids) == sorted(n.id for n in model.nodes)
    assert len(ids) == len(set(ids))


def test_nodes_sit_inside_their_own_group_disc(model):
    layout = graph_layout(model)
    discs = {d["name"]: d for d in layout["groups"]}
    for node in layout["nodes"]:
        disc = discs[node["group"]]
        d = math.hypot(node["x"] - disc["cx"], node["y"] - disc["cy"])
        assert d <= disc["radius"] + 0.5, f"{node['id']} escaped its cluster"


def test_disc_size_tracks_membership(model):
    discs = graph_layout(model)["groups"]
    ordered = sorted(discs, key=lambda d: d["count"])
    for a, b in pairwise(ordered):
        if b["count"] > a["count"]:
            assert b["radius"] >= a["radius"], "a bigger group drew a smaller disc"


def test_node_radius_tracks_token_cost():
    model = fake({"g": 3}, costs={"g.t0": 10, "g.t1": 500, "g.t2": 250})
    placed = {n["id"]: n for n in graph_layout(model)["nodes"]}
    assert placed["g.t0"]["r"] < placed["g.t2"]["r"] < placed["g.t1"]["r"]


def test_edges_carry_endpoint_coordinates(model):
    layout = graph_layout(model)
    pos = {n["id"]: n for n in layout["nodes"]}
    assert len(layout["edges"]) == len(model.edges)
    for edge in layout["edges"]:
        assert (edge["x1"], edge["y1"]) == (pos[edge["source"]]["x"], pos[edge["source"]]["y"])
        assert (edge["x2"], edge["y2"]) == (pos[edge["target"]]["x"], pos[edge["target"]]["y"])


# --------------------------------------------------------------- determinism

def test_layout_is_deterministic(model):
    assert graph_layout(model) == graph_layout(model)


def test_layout_does_not_depend_on_node_ordering(model):
    a = graph_layout(model)
    shuffled = FakeModel(
        nodes=list(reversed([FakeNode(id=n.id, group=n.group, token_cost=n.token_cost)
                             for n in model.nodes])),
        edges=[],
    )
    b = graph_layout(shuffled)
    assert {n["id"]: (n["x"], n["y"]) for n in a["nodes"]} == \
           {n["id"]: (n["x"], n["y"]) for n in b["nodes"]}


# ------------------------------------------------------------- degenerate cases

@pytest.mark.parametrize("counts", [
    {},                        # nothing at all
    {"solo": 1},               # one tool, one group
    {"a": 1, "b": 1},          # two singleton groups
    {"a": 2},
    {"big": 200},              # one crowded cluster
    {"a": 50, "b": 1, "c": 1},  # wildly uneven
])
def test_degenerate_shapes_still_lay_out_cleanly(counts):
    layout = graph_layout(fake(counts))
    assert len(layout["nodes"]) == sum(counts.values())
    assert overlaps(layout["nodes"]) == []
    for node in layout["nodes"]:
        assert 0 <= node["x"] <= layout["width"]
        assert 0 <= node["y"] <= layout["height"]


def test_a_single_group_is_centred():
    layout = graph_layout(fake({"only": 6}))
    disc = layout["groups"][0]
    assert disc["cx"] == pytest.approx(layout["width"] / 2)
    assert disc["cy"] == pytest.approx(layout["height"] / 2)


def test_a_thousand_tools_lay_out_in_reasonable_time():
    import time

    model = fake({f"g{i}": 100 for i in range(10)})
    start = time.monotonic()
    layout = graph_layout(model, relax_passes=4)
    elapsed = time.monotonic() - start
    assert len(layout["nodes"]) == 1000
    assert elapsed < 20.0, f"layout took {elapsed:.1f}s"


# -------------------------------------------------------------------- treemap

def test_treemap_tiles_fit_the_box_and_never_go_negative():
    tiles = treemap([("a", 50.0), ("b", 30.0), ("c", 20.0), ("d", 1.0)],
                    width=400.0, height=200.0)
    assert len(tiles) == 4
    for tile in tiles:
        assert tile["w"] >= 0 and tile["h"] >= 0
        assert tile["x"] >= -0.01 and tile["y"] >= -0.01
        assert tile["x"] + tile["w"] <= 400.5
        assert tile["y"] + tile["h"] <= 200.5


def test_treemap_area_is_proportional_to_value():
    tiles = {t["key"]: t for t in treemap([("big", 75.0), ("small", 25.0)],
                                          width=400.0, height=200.0, pad=0.0)}
    ratio = (tiles["big"]["w"] * tiles["big"]["h"]) / (tiles["small"]["w"] * tiles["small"]["h"])
    assert ratio == pytest.approx(3.0, rel=0.05)


def test_treemap_handles_empty_and_zero_input():
    assert treemap([], width=100, height=100) == []
    assert treemap([("a", 0.0)], width=100, height=100) == []
    assert treemap([("a", 1.0)], width=0, height=100) == []


def test_token_treemap_covers_exactly_the_advertised_tools(model):
    tiles = token_treemap(model)
    assert sorted(t["key"] for t in tiles) == \
        sorted(n.id for n in model.nodes if n.advertised)
    for tile in tiles:
        assert tile["tokens"] > 0 and tile["group"] and tile["risk"]


# --------------------------------------------------------------------- matrix

def test_matrix_counts_every_tool_once(model):
    grid = matrix(model, "group", "risk")
    total = sum(v for row in grid["counts"].values() for v in row.values())
    assert total == len(model.nodes)
    assert grid["max"] == max(v for row in grid["counts"].values() for v in row.values())


def test_matrix_cells_list_the_tools_they_count(model):
    grid = matrix(model, "group", "risk")
    for r in grid["rows"]:
        for c in grid["cols"]:
            assert len(grid["ids"][r][c]) == grid["counts"][r][c]


def test_matrix_works_on_any_two_attributes(model):
    grid = matrix(model, "tier", "risk")
    assert set(grid["rows"]) <= {"core", "task", "full"}


# -------------------------------------------------------------------- latency

def test_latency_bands_partition_the_toolset(model):
    bands = latency_bands(model)
    assert sum(b["count"] for b in bands) == len(model.nodes)
    seen = [i for b in bands for i in b["ids"]]
    assert sorted(seen) == sorted(n.id for n in model.nodes)


# ----------------------------------------------------------------------- SVG

def test_graph_svg_draws_every_node_and_edge(model):
    svg = render_graph_svg(model)
    assert svg.count('class="gnode"') == len(model.nodes)
    assert svg.count("<line ") == len(model.edges)
    for node in model.nodes[:20]:
        assert f'data-id="{node.id}"' in svg


def test_graph_svg_encodes_withheld_and_destructive_visually(model):
    svg = render_graph_svg(model)
    withheld = [n for n in model.nodes if not n.advertised]
    if withheld:
        assert 'data-adv="0"' in svg
        assert "stroke-dasharray" in svg
    # destructive tools get a glyph, so the meaning survives greyscale
    assert svg.count('class="glyph"') == sum(1 for n in model.nodes if n.destructive)


def test_graph_svg_groups_edges_by_kind_for_the_toggles(model):
    svg = render_graph_svg(model)
    for kind in {e.kind for e in model.edges}:
        assert f'data-kind="{kind}"' in svg


def test_svg_is_well_formed_xml(model):
    """A malformed SVG silently renders as nothing, so parse it."""
    import xml.etree.ElementTree as ET

    for svg in (render_graph_svg(model), render_treemap_svg(model)):
        wrapped = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
        root = ET.fromstring(wrapped)
        assert root.tag.endswith("svg")


def test_view_renderers_escape_hostile_identifiers():
    nasty = 'x"><script>alert(1)</script>'
    model = FakeModel(nodes=[FakeNode(id=nasty, group=nasty)],
                      edges=[])
    for html_out in (render_graph_svg(model), render_treemap_svg(model),
                     render_matrix_html(model), render_latency_html(model)):
        assert "<script>alert(1)</script>" not in html_out


def test_group_colours_are_stable_and_cover_every_group(model):
    colours = group_colours(model)
    assert set(colours) == {n.group for n in model.nodes}
    assert colours == group_colours(model)
    for value in colours.values():
        assert value.startswith("#")


def test_matrix_and_latency_render_to_markup(model):
    assert "<table" in render_matrix_html(model)
    assert "lat-row" in render_latency_html(model)


def test_treemap_svg_labels_only_tiles_that_fit(model):
    svg = render_treemap_svg(model)
    labels = svg.count('class="tile-label"')
    tiles = svg.count('class="tile"')
    assert 0 < labels <= tiles, "labels must not exceed tiles"
