"""V3: the graph and the alternative views, rendered to SVG server-side.

Rendering happens in Python for the same reason the layout does: the output is
deterministic text, so a test can assert what a browser will draw without
driving a browser. The page then attaches interaction (edge-kind toggles, hover
neighbourhoods, click-to-drawer) to markup that is already complete and
readable - which is what makes the JS-disabled degradation real rather than
aspirational.

Four views, one model:

  graph    - clustered node-link diagram, colour = group, ring = risk,
             size = token cost, dashed = withheld here
  treemap  - the token budget: who spends the advertisement
  matrix   - group x risk, and capability x provider
  latency  - declared latency on a log scale
"""

from __future__ import annotations

import html
from typing import Any

from .layout import graph_layout, latency_bands, matrix, token_treemap

# One hue per group, cycled. Chosen for contrast on the dark panel and to stay
# distinguishable for the common forms of colour blindness (no red/green pair
# carries meaning on its own - risk is also encoded as a ring and a badge).
_PALETTE = ["#58a6ff", "#3fb950", "#d29922", "#a371f7", "#ff7b72", "#39c5cf",
            "#db6d28", "#bc8cff", "#6ee7b7", "#f2cc60", "#8b949e"]

_RISK_RING = {"none": "#8b949e", "read": "#58a6ff", "write": "#d29922",
              "destructive": "#f85149", "network": "#a371f7", "privileged": "#ff7b72"}

_EDGE_STYLE = {
    "see_also": ("#58a6ff", "0.55", ""),
    "same_capability": ("#d29922", "0.7", "4 3"),
    "provides": ("#3fb950", "0.6", "2 2"),
    "anti_pattern": ("#f85149", "0.5", "1 3"),
}


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def group_colours(model: Any) -> dict[str, str]:
    """Stable group -> colour map. Sorted, so it never shuffles between renders."""
    names = sorted({n.group for n in model.nodes})
    return {name: _PALETTE[i % len(_PALETTE)] for i, name in enumerate(names)}


# ----------------------------------------------------------------- the graph

def render_graph_svg(model: Any) -> str:
    """The clustered node-link view as one SVG element."""
    layout = graph_layout(model)
    colours = group_colours(model)
    by_id = {n.id: n for n in model.nodes}

    parts: list[str] = [
        f'<svg id="graph-svg" viewBox="0 0 {layout["width"]:.0f} {layout["height"]:.0f}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="tool relationship graph">'
    ]

    # group discs first: they are background, and they label the clusters
    parts.append('<g class="discs">')
    for disc in layout["groups"]:
        colour = colours.get(disc["name"], "#8b949e")
        parts.append(
            f'<circle cx="{disc["cx"]}" cy="{disc["cy"]}" r="{disc["radius"]:.1f}" '
            f'fill="{colour}" fill-opacity="0.05" stroke="{colour}" '
            f'stroke-opacity="0.25" stroke-width="1"/>'
            f'<text x="{disc["cx"]}" y="{disc["cy"] - disc["radius"] - 6:.1f}" '
            f'text-anchor="middle" class="disc-label" fill="{colour}">'
            f'{_e(disc["name"])} <tspan class="disc-n">{disc["count"]}</tspan></text>'
        )
    parts.append("</g>")

    # edges, grouped by kind so the toggles are one class flip each
    for kind, (colour, opacity, dash) in _EDGE_STYLE.items():
        rows = [e for e in layout["edges"] if e["kind"] == kind]
        if not rows:
            continue
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(f'<g class="edges edge-{kind}" data-kind="{kind}" '
                     f'stroke="{colour}" stroke-opacity="{opacity}" '
                     f'stroke-width="1"{dash_attr} fill="none">')
        for edge in rows:
            parts.append(
                f'<line x1="{edge["x1"]}" y1="{edge["y1"]}" '
                f'x2="{edge["x2"]}" y2="{edge["y2"]}" '
                f'data-a="{_e(edge["source"])}" data-b="{_e(edge["target"])}"/>'
            )
        parts.append("</g>")

    # nodes last: they take the clicks
    parts.append('<g class="nodes">')
    for placed in layout["nodes"]:
        node = by_id[placed["id"]]
        fill = colours.get(node.group, "#8b949e")
        ring = _RISK_RING.get(node.risk, "#8b949e")
        dashed = ' stroke-dasharray="2 2"' if not node.advertised else ""
        opacity = "0.55" if not node.advertised else "1"
        parts.append(
            f'<circle class="gnode" data-id="{_e(node.id)}" '
            f'data-group="{_e(node.group)}" data-risk="{_e(node.risk)}" '
            f'data-adv="{"1" if node.advertised else "0"}" '
            f'cx="{placed["x"]}" cy="{placed["y"]}" r="{placed["r"]}" '
            f'fill="{fill}" fill-opacity="{opacity}" stroke="{ring}" '
            f'stroke-width="1.6"{dashed} tabindex="0" role="button">'
            f'<title>{_e(node.id)} — {_e(node.risk)}, {node.token_cost} tokens'
            f'{"" if node.advertised else " (withheld here)"}</title>'
            f"</circle>"
        )
        if node.destructive:
            # a mark that survives greyscale printing and colour blindness
            parts.append(
                f'<text class="glyph" x="{placed["x"]}" '
                f'y="{placed["y"] + 3.2:.2f}" text-anchor="middle" '
                f'pointer-events="none">!</text>'
            )
    parts.append("</g></svg>")
    return "".join(parts)


def render_graph_legend(model: Any) -> str:
    colours = group_colours(model)
    swatches = "".join(
        f'<span class="sw"><i style="background:{colours[g]}"></i>{_e(g)}</span>'
        for g in sorted(colours)
    )
    kinds = "".join(
        f'<label class="sw"><input type="checkbox" class="edge-toggle" '
        f'data-kind="{k}"{" checked" if k == "see_also" else ""}>'
        f'<i style="background:{_EDGE_STYLE[k][0]}"></i>{_e(k.replace("_", " "))}</label>'
        for k in _EDGE_STYLE
    )
    return (
        f'<div class="legend"><div class="legend-row">{swatches}</div>'
        f'<div class="legend-row"><span class="dim">relations:</span>{kinds}</div>'
        f'<div class="legend-row dim">ring = risk &middot; size = token cost '
        f'&middot; dashed = withheld here &middot; ! = destructive</div></div>'
    )


# ---------------------------------------------------------------- the treemap

def render_treemap_svg(model: Any) -> str:
    """Token budget as area. The question it answers: what is my context paying for?"""
    tiles = token_treemap(model)
    if not tiles:
        return '<p class="dim">Nothing advertised, so nothing spends tokens.</p>'
    colours = group_colours(model)
    width, height = 1100.0, 460.0
    parts = [f'<svg id="treemap-svg" viewBox="0 0 {width:.0f} {height:.0f}" '
             f'preserveAspectRatio="xMidYMid meet" role="img" '
             f'aria-label="token cost treemap">']
    for tile in tiles:
        colour = colours.get(tile["group"], "#8b949e")
        parts.append(
            f'<g class="tile" data-id="{_e(tile["key"])}" tabindex="0" role="button">'
            f'<rect x="{tile["x"]}" y="{tile["y"]}" width="{tile["w"]}" '
            f'height="{tile["h"]}" fill="{colour}" fill-opacity="0.30" '
            f'stroke="{colour}" stroke-opacity="0.6"/>'
            f'<title>{_e(tile["key"])} — {tile["tokens"]} tokens</title>'
        )
        # only label a tile that can actually hold the text
        if tile["w"] > 54 and tile["h"] > 16:
            label = tile["key"].split(".", 1)[-1]
            parts.append(
                f'<text x="{tile["x"] + 4:.1f}" y="{tile["y"] + 12:.1f}" '
                f'class="tile-label" pointer-events="none">{_e(label)}</text>'
            )
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


# ----------------------------------------------------------------- the matrix

def render_matrix_html(model: Any, rows: str = "group", cols: str = "risk") -> str:
    grid = matrix(model, rows, cols)
    if not grid["rows"]:
        return '<p class="dim">No tools to cross-tabulate.</p>'
    head = "".join(f"<th>{_e(c)}</th>" for c in grid["cols"])
    body = []
    for r in grid["rows"]:
        cells = []
        for c in grid["cols"]:
            n = grid["counts"][r][c]
            if not n:
                cells.append('<td class="zero">·</td>')
                continue
            # opacity carries magnitude; the number carries the fact
            alpha = 0.12 + 0.68 * (n / grid["max"] if grid["max"] else 0)
            ids = " ".join(grid["ids"][r][c][:40])
            cells.append(
                f'<td class="cell" style="background:rgba(88,166,255,{alpha:.2f})" '
                f'data-ids="{_e(ids)}" title="{_e(r)} / {_e(c)}: {n}">{n}</td>'
            )
        body.append(f"<tr><th>{_e(r)}</th>{''.join(cells)}</tr>")
    return (f'<table class="matrix"><thead><tr>'
            f'<th class="corner">{_e(rows)} \\ {_e(cols)}</th>{head}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table>")


# ---------------------------------------------------------------- the latency

def render_latency_html(model: Any) -> str:
    bands = latency_bands(model)
    peak = max((b["count"] for b in bands), default=0) or 1
    rows = []
    for band in bands:
        pct = 100.0 * band["count"] / peak
        rows.append(
            f'<div class="lat-row"><span class="lat-k">{_e(band["label"])}</span>'
            f'<span class="lat-bar"><i style="width:{pct:.1f}%"></i></span>'
            f'<span class="lat-n">{band["count"]}</span></div>'
        )
    return (f'<div class="latency">{"".join(rows)}'
            f'<p class="dim">Declared <code>typical_latency_ms</code>, log-banded. '
            f'V5 will overlay what the ledger actually measured.</p></div>')
