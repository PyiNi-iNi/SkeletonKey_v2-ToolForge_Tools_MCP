"""V3: deterministic layout maths for the graph and the alternative views.

Layout lives in Python, not in the page, for three reasons: it is testable
without a browser, it is deterministic (the same model lays out identically on
every machine, so a snapshot test is meaningful), and it keeps ADR-0001 honest -
no vendored force-simulation library, no npm.

The plan (`docs/TOOL-VISUALIZER-PLAN.md` §V3) called for a grouped/radial layout
first and force relaxation *only if it reads badly*. It read badly: with 11
groups of wildly different size (17 integrations vs 1 policy), pure radial puts
big and small clusters at the same radius and the big ones collide. So the
layout here is:

  1. **Group discs.** Groups are placed on a circle, each given an angular slice
     proportional to sqrt(member count) - sqrt because area, not arc, is what
     the eye reads as "how much stuff is here".
  2. **Sunflower packing inside each disc.** Members are placed on a
     Vogel/phyllotaxis spiral, which fills a disc evenly with no simulation and
     no collisions, at any count.
  3. **A few relaxation passes.** Cheap pairwise repulsion, seeded and
     iteration-capped, purely to nudge apart the handful of near-touching pairs
     the spiral leaves at disc boundaries.

Everything is pure and deterministic: no RNG, no clock, no set iteration order.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Canvas the graph is laid out in. The page scales it with a viewBox, so these
# are arbitrary units chosen to make the numbers readable in tests.
WIDTH = 1200.0
HEIGHT = 820.0

GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))

# Node radius by token cost, clamped so a cheap tool is still clickable and an
# expensive one does not eat its neighbours.
MIN_R = 5.0
MAX_R = 15.0


@dataclass
class Placed:
    """A node with a position. `r` is the drawn radius, `group` its cluster."""

    id: str
    x: float
    y: float
    r: float
    group: str

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "x": round(self.x, 2), "y": round(self.y, 2),
                "r": round(self.r, 2), "group": self.group}


@dataclass
class GroupDisc:
    name: str
    cx: float
    cy: float
    radius: float
    count: int

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "cx": round(self.cx, 2), "cy": round(self.cy, 2),
                "radius": round(self.radius, 2), "count": self.count}


def _node_radius(token_cost: int, lo: int, hi: int) -> float:
    """Area-proportional radius: doubling the tokens doubles the ink, not the width."""
    if hi <= lo:
        return (MIN_R + MAX_R) / 2.0
    t = (token_cost - lo) / float(hi - lo)
    # sqrt so the *area* tracks the cost
    return MIN_R + (MAX_R - MIN_R) * math.sqrt(max(0.0, min(1.0, t)))


def _sunflower(n: int, radius: float) -> list[tuple[float, float]]:
    """`n` points filling a disc of `radius` evenly (Vogel's phyllotaxis spiral).

    Deterministic, collision-free at any n, and it looks organic rather than
    gridded - which matters, because a grid implies an ordering that the tools
    do not actually have.
    """
    if n <= 0:
        return []
    if n == 1:
        return [(0.0, 0.0)]
    out: list[tuple[float, float]] = []
    for i in range(n):
        # the 0.5 offset keeps the innermost point off the exact centre
        frac = math.sqrt((i + 0.5) / n)
        angle = i * GOLDEN_ANGLE
        out.append((radius * frac * math.cos(angle), radius * frac * math.sin(angle)))
    return out


def _relax(placed: list[Placed], discs: dict[str, GroupDisc], *,
           passes: int = 24, padding: float = 2.0) -> None:
    """Nudge overlapping pairs apart, in place. Deterministic and bounded.

    This is not a force simulation: there is no velocity, no cooling schedule
    and no randomness. It is a fixed number of passes of "if these two circles
    intersect, push each half the overlap away", with nodes kept inside their
    group disc. Sorted iteration keeps it reproducible.
    """
    if len(placed) < 2:
        return
    order = sorted(range(len(placed)), key=lambda i: placed[i].id)
    for _ in range(passes):
        moved = False
        for ai in range(len(order)):
            for bi in range(ai + 1, len(order)):
                a, b = placed[order[ai]], placed[order[bi]]
                dx, dy = b.x - a.x, b.y - a.y
                dist = math.hypot(dx, dy)
                want = a.r + b.r + padding
                if dist >= want:
                    continue
                if dist < 1e-6:
                    # exactly coincident: separate along a stable axis
                    dx, dy, dist = 1.0, 0.0, 1.0
                push = (want - dist) / 2.0
                ux, uy = dx / dist, dy / dist
                a.x -= ux * push
                a.y -= uy * push
                b.x += ux * push
                b.y += uy * push
                moved = True
        # keep everyone inside their own disc, so clusters stay legible
        for node in placed:
            disc = discs.get(node.group)
            if disc is None:
                continue
            dx, dy = node.x - disc.cx, node.y - disc.cy
            limit = max(0.0, disc.radius - node.r)
            dist = math.hypot(dx, dy)
            if dist > limit and dist > 1e-6:
                node.x = disc.cx + dx / dist * limit
                node.y = disc.cy + dy / dist * limit
        if not moved:
            break


def graph_layout(model: Any, *, width: float = WIDTH, height: float = HEIGHT,
                 relax_passes: int = 24) -> dict[str, Any]:
    """Lay the whole toolset out as clustered discs. Pure and deterministic."""
    nodes = sorted(model.nodes, key=lambda n: (n.group, n.id))
    if not nodes:
        return {"width": width, "height": height, "nodes": [], "groups": [],
                "edges": []}

    costs = [n.token_cost for n in nodes]
    lo, hi = min(costs), max(costs)

    by_group: dict[str, list[Any]] = {}
    for node in nodes:
        by_group.setdefault(node.group, []).append(node)
    group_names = sorted(by_group, key=lambda g: (-len(by_group[g]), g))

    cx, cy = width / 2.0, height / 2.0
    # Each group's disc radius scales with sqrt(count) so area tracks membership.
    weights = {g: math.sqrt(len(by_group[g])) for g in group_names}
    discs: dict[str, GroupDisc] = {}

    if len(group_names) == 1:
        # one group orbits nothing: put it in the middle at a sensible size
        only = group_names[0]
        discs[only] = GroupDisc(only, cx, cy, 0.42 * min(width, height),
                                len(by_group[only]))
    else:
        # Pack the discs *around* a ring instead of merely spacing their centres.
        # A disc of radius r whose centre sits on a ring of radius R subtends
        # 2*asin(r/R); the discs are tangent, not overlapping, exactly when those
        # angles sum to 2*pi. Sizing by angular share alone (the obvious first
        # cut) let the big clusters overlap by most of their radius and the
        # graph read as one blob, so solve for the scale that actually fits.
        ring = 0.30 * min(width, height)
        gap = 0.94              # leave a sliver of air between neighbours

        def spans_for(unit: float) -> float:
            total = 0.0
            for name in group_names:
                r = min(unit * weights[name], ring * 0.99)
                total += 2.0 * math.asin(max(0.0, min(1.0, r / ring)))
            return total

        lo, hi = 0.0, ring
        for _ in range(48):     # bisection: monotone in `unit`, so this converges
            mid = (lo + hi) / 2.0
            if spans_for(mid) > 2.0 * math.pi * gap:
                hi = mid
            else:
                lo = mid
        unit = lo

        # Screens are wide and the ring is sized by the short edge, so stretch x
        # afterwards. Scaling one axis *up* can only increase separation, so the
        # tangency solved for above still holds.
        stretch = max(1.0, min(1.6, (width * 0.44) / (ring + unit * max(weights.values()))))

        angle_at = -math.pi / 2.0    # start at 12 o'clock: stable and readable
        for name in group_names:
            r = max(24.0, min(unit * weights[name], ring * 0.99))
            span = 2.0 * math.asin(max(0.0, min(1.0, r / ring)))
            centre = angle_at + span / 2.0
            angle_at += span
            gx = cx + ring * math.cos(centre) * stretch
            gy = cy + ring * math.sin(centre)
            discs[name] = GroupDisc(name, gx, gy, r, len(by_group[name]))

    placed: list[Placed] = []
    for name in group_names:
        disc = discs[name]
        members = sorted(by_group[name], key=lambda n: n.id)
        # leave a rim so nodes do not straddle the disc outline
        spots = _sunflower(len(members), max(1.0, disc.radius - MAX_R - 2.0))
        for node, (ox, oy) in zip(members, spots, strict=True):
            placed.append(Placed(id=node.id, x=disc.cx + ox, y=disc.cy + oy,
                                 r=_node_radius(node.token_cost, lo, hi),
                                 group=node.group))

    _relax(placed, discs, passes=relax_passes)

    pos = {p.id: p for p in placed}
    edges = [{"source": e.source, "target": e.target, "kind": e.kind,
              "x1": round(pos[e.source].x, 2), "y1": round(pos[e.source].y, 2),
              "x2": round(pos[e.target].x, 2), "y2": round(pos[e.target].y, 2)}
             for e in model.edges if e.source in pos and e.target in pos]

    return {
        "width": width, "height": height,
        "nodes": [p.to_json() for p in sorted(placed, key=lambda p: p.id)],
        "groups": [discs[g].to_json() for g in sorted(discs)],
        "edges": edges,
    }


# ------------------------------------------------------------------- treemap

def treemap(items: list[tuple[str, float]], *, width: float, height: float,
            pad: float = 1.0) -> list[dict[str, Any]]:
    """Squarified-ish treemap: who spends the token budget.

    A plain slice-and-dice treemap produces slivers that cannot be read or
    clicked. This is the standard squarify approach (fill a strip while the
    worst aspect ratio improves, then start a new strip), which keeps tiles
    close to square. Deterministic: input order decides ties.
    """
    total = sum(max(0.0, v) for _k, v in items)
    if not items or total <= 0 or width <= 0 or height <= 0:
        return []

    # largest first is what makes squarify work
    ordered = sorted(items, key=lambda kv: (-kv[1], kv[0]))
    scale = (width * height) / total
    areas = [(k, max(0.0, v) * scale) for k, v in ordered]

    out: list[dict[str, Any]] = []
    x, y, w, h = 0.0, 0.0, width, height
    row: list[tuple[str, float]] = []

    def worst(row_areas: list[float], side: float) -> float:
        if not row_areas or side <= 0:
            return math.inf
        s = sum(row_areas)
        if s <= 0:
            return math.inf
        mx, mn = max(row_areas), min(row_areas)
        return max((side * side * mx) / (s * s), (s * s) / (side * side * mn))

    def flush(row_items: list[tuple[str, float]], x0: float, y0: float,
              w0: float, h0: float) -> tuple[float, float, float, float]:
        s = sum(a for _k, a in row_items)
        if s <= 0:
            return x0, y0, w0, h0
        if w0 >= h0:
            strip = s / h0
            off = y0
            for key, area in row_items:
                seg = area / strip if strip else 0.0
                out.append({"key": key, "x": round(x0 + pad / 2, 2),
                            "y": round(off + pad / 2, 2),
                            "w": round(max(0.0, strip - pad), 2),
                            "h": round(max(0.0, seg - pad), 2), "value": area})
                off += seg
            return x0 + strip, y0, w0 - strip, h0
        strip = s / w0
        off = x0
        for key, area in row_items:
            seg = area / strip if strip else 0.0
            out.append({"key": key, "x": round(off + pad / 2, 2),
                        "y": round(y0 + pad / 2, 2),
                        "w": round(max(0.0, seg - pad), 2),
                        "h": round(max(0.0, strip - pad), 2), "value": area})
            off += seg
        return x0, y0 + strip, w0, h0 - strip

    for key, area in areas:
        side = min(w, h)
        current = [a for _k, a in row]
        if row and worst(current, side) < worst([*current, area], side):
            x, y, w, h = flush(row, x, y, w, h)
            row = []
        row.append((key, area))
    if row:
        flush(row, x, y, w, h)

    return out


def token_treemap(model: Any, *, width: float = 1100.0,
                  height: float = 460.0) -> list[dict[str, Any]]:
    """Treemap tiles for advertised tools, sized by token cost."""
    items = [(n.id, float(n.token_cost)) for n in model.nodes if n.advertised]
    tiles = treemap(items, width=width, height=height)
    by_id = {n.id: n for n in model.nodes}
    for tile in tiles:
        node = by_id[tile["key"]]
        tile["group"] = node.group
        tile["risk"] = node.risk
        tile["tokens"] = node.token_cost
    return tiles


# -------------------------------------------------------------------- matrix

def matrix(model: Any, rows: str = "group", cols: str = "risk") -> dict[str, Any]:
    """A counted cross-tab of two node attributes, for the matrix view."""
    row_vals = sorted({getattr(n, rows) for n in model.nodes})
    col_vals = sorted({getattr(n, cols) for n in model.nodes})
    counts: dict[str, dict[str, int]] = {r: dict.fromkeys(col_vals, 0) for r in row_vals}
    ids: dict[str, dict[str, list[str]]] = {r: {c: [] for c in col_vals} for r in row_vals}
    for node in sorted(model.nodes, key=lambda n: n.id):
        r, c = getattr(node, rows), getattr(node, cols)
        counts[r][c] += 1
        ids[r][c].append(node.id)
    return {"rows": row_vals, "cols": col_vals, "counts": counts, "ids": ids,
            "row_key": rows, "col_key": cols,
            "max": max((v for row in counts.values() for v in row.values()), default=0)}


# ------------------------------------------------------------------- latency

def latency_bands(model: Any) -> list[dict[str, Any]]:
    """Bucket tools by declared latency on a log scale.

    Log scale because the declared values span 1 ms to 30 s: a linear axis puts
    everything in one bar and tells you nothing.
    """
    bands = [(0, 10, "≤10 ms"), (10, 100, "10–100 ms"), (100, 1_000, "0.1–1 s"),
             (1_000, 10_000, "1–10 s"), (10_000, math.inf, ">10 s")]
    out = []
    for lo, hi, label in bands:
        members = sorted(n.id for n in model.nodes
                         if lo <= n.typical_latency_ms < hi)
        out.append({"label": label, "lo": lo, "count": len(members), "ids": members})
    return out
