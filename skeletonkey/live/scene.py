"""The scene: a retained-mode scene graph that renders to SVG.

This is the preview panel's framebuffer. A live program draws into its
`canvas` (an injected `Scene`) - rect/circle/line/text in 2D, or `cube3d` /
`poly3d` / `point3d` which are orbit-projected onto the same SVG surface.
Agents can also drive it directly through the `live.scene` tool.

Zero dependencies by design (ADR-0001): the whole renderer is string
interpolation with deterministic output, so tests compare the SVG text and a
browser renders it without a build step. 3D is an honest orthographic orbit
projection - enough to be a live shader-editor-style playground, honest about
not being WebGL.
"""

from __future__ import annotations

import math
from typing import Any
from xml.sax.saxutils import escape

# style keys we pass straight through to SVG attributes
_PAINT = ("fill", "stroke", "stroke_width", "opacity", "fill_opacity", "stroke_opacity",
          "dash", "font_size", "anchor", "weight")


def _num(value: Any) -> float:
    return float(value)


def _fmt(value: float) -> str:
    """Stable, compact float formatting: diffable output for tests, small SVG."""
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _norm3(x: float, y: float, z: float) -> tuple[float, float, float]:
    mag = math.sqrt(x * x + y * y + z * z) or 1.0
    return (x / mag, y / mag, z / mag)


def _face_normal(a: tuple[float, float, float], b: tuple[float, float, float],
                 c: tuple[float, float, float]) -> tuple[float, float, float]:
    ux, uy, uz = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    vx, vy, vz = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    return (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)


def _shade(hex_color: str, intensity: float) -> str:
    """Scale an #rrggbb colour by a light intensity; non-hex colours pass
    through unshaded (named colours are valid SVG too)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return hex_color
    return (f"#{int(r * intensity):02x}{int(g * intensity):02x}"
            f"{int(b * intensity):02x}")


class Scene:
    """An ordered list of named nodes plus a camera. Every mutation bumps
    `version`; the preview panel polls the version and re-pulls the frame,
    which is what makes a REPL assignment feel like an HMR update."""

    def __init__(self, width: int = 420, height: int = 320, background: str = "#0d1117",
                 camera: dict[str, Any] | None = None) -> None:
        self.width = int(width)
        self.height = int(height)
        self.background = background
        self.camera: dict[str, Any] = {
            "theta": 0.65,     # orbit around Y
            "phi": 0.62,       # elevation
            "scale": 1.0,
            "cx": self.width / 2,
            "cy": self.height / 2,
            **(camera or {}),
        }
        self._nodes: list[dict[str, Any]] = []
        self._auto = 0
        self.version = 0

    # ------------------------------------------------------------- mutation
    def _claim_id(self, id_: str | None) -> str:
        if id_:
            return str(id_)
        self._auto += 1
        return f"n{self._auto}"

    def upsert(self, kind: str, id: str | None = None, **props: Any) -> dict[str, Any]:
        """Insert or replace a node; returns it. Node identity by id is what
        makes `live.scene {op: upsert, id: "hero", ...}` feel like editing a
        component: stable id, new props, no flicker."""
        nid = self._claim_id(id)
        node = {"id": nid, "type": kind, **props}
        for i, existing in enumerate(self._nodes):
            if existing["id"] == nid:
                self._nodes[i] = node
                self.version += 1
                return node
        self._nodes.append(node)
        self.version += 1
        return node

    def remove(self, id: str) -> bool:
        before = len(self._nodes)
        self._nodes = [n for n in self._nodes if n["id"] != id]
        changed = len(self._nodes) != before
        if changed:
            self.version += 1
        return changed

    def clear(self) -> None:
        if self._nodes:
            self.version += 1
        self._nodes = []

    def orbit(self, theta: float | None = None, phi: float | None = None,
              scale: float | None = None) -> dict[str, Any]:
        """Move the 3D camera (radians). REPL: `canvas.orbit(theta=canvas.camera['theta']+0.2)`."""
        if theta is not None:
            self.camera["theta"] = float(theta)
        if phi is not None:
            self.camera["phi"] = float(phi)
        if scale is not None:
            self.camera["scale"] = float(scale)
        self.version += 1
        return dict(self.camera)

    # ------------------------------------------------------------- 2D sugar
    def rect(self, x: float, y: float, w: float, h: float, id: str | None = None,
             **style: Any) -> dict[str, Any]:
        return self.upsert("rect", id, x=_num(x), y=_num(y), w=_num(w), h=_num(h),
                           **self._style(style))

    def circle(self, cx: float, cy: float, r: float, id: str | None = None,
               **style: Any) -> dict[str, Any]:
        return self.upsert("circle", id, cx=_num(cx), cy=_num(cy), r=_num(r),
                           **self._style(style))

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, id: str | None = None,
                **style: Any) -> dict[str, Any]:
        return self.upsert("ellipse", id, cx=_num(cx), cy=_num(cy), rx=_num(rx), ry=_num(ry),
                           **self._style(style))

    def line(self, x1: float, y1: float, x2: float, y2: float, id: str | None = None,
             **style: Any) -> dict[str, Any]:
        return self.upsert("line", id, x1=_num(x1), y1=_num(y1), x2=_num(x2), y2=_num(y2),
                           **self._style(style))

    def text(self, x: float, y: float, text: Any, id: str | None = None,
             **style: Any) -> dict[str, Any]:
        style = {"fill": "#e6edf3", "font_size": 14, "anchor": "middle", **style}
        return self.upsert("text", id, x=_num(x), y=_num(y), text=str(text), **self._style(style))

    # ------------------------------------------------------------- 3D sugar
    def poly3d(self, points: list[tuple[float, float, float] | list[float]],
               id: str | None = None, close: bool = False, **style: Any) -> dict[str, Any]:
        style = {"stroke": "#58a6ff", "stroke_width": 1.5, **style}
        pts = [[_num(p[0]), _num(p[1]), _num(p[2])] for p in points]
        return self.upsert("poly3d", id, points=pts, close=bool(close), **self._style(style))

    def cube3d(self, cx: float, cy: float, cz: float, size: float, id: str | None = None,
               spin: float = 0.0, **style: Any) -> dict[str, Any]:
        """A wireframe cube spinning on its own Y axis (degrees)."""
        style = {"stroke": "#58a6ff", "stroke_width": 1.5, **style}
        return self.upsert("cube3d", id, cx=_num(cx), cy=_num(cy), cz=_num(cz),
                           size=_num(size), spin=float(spin), **self._style(style))

    def point3d(self, x: float, y: float, z: float, r: float = 3.0, id: str | None = None,
                **style: Any) -> dict[str, Any]:
        style = {"fill": "#f2cc60", **style}
        return self.upsert("point3d", id, x=_num(x), y=_num(y), z=_num(z), r=_num(r),
                           **self._style(style))

    def mesh3d(self, vertices: list[tuple[float, float, float] | list[float]],
               faces: list[list[int]], id: str | None = None, **style: Any) -> dict[str, Any]:
        """A general triangle/quad mesh. The SVG renderer paints faces
        painter-sorted with a fixed-key-light lambert shade; the /view3d panel
        renders the same node with a perspective camera. `faces` index into
        `vertices`; pass windings counter-clockwise for outward normals."""
        style = {"fill": "#58a6ff", "stroke_width": 0.8, **style}
        verts = [[_num(p[0]), _num(p[1]), _num(p[2])] for p in vertices]
        if any(i < 0 or i >= len(verts) for face in faces for i in face):
            raise ValueError("mesh3d face index out of range")
        return self.upsert("mesh3d", id, vertices=verts,
                           faces=[[int(i) for i in f] for f in faces], **self._style(style))

    # ------------------------------------------------------------- internals
    @staticmethod
    def _style(style: dict[str, Any]) -> dict[str, Any]:
        """Style kwargs pass through; pure-python callers naturally write
        snake_case (stroke_width) and the serializer maps to SVG attributes."""
        return {str(k): v for k, v in style.items()}

    def _rotate(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """Camera-space coordinates after orbit-about-Y + tilt-about-X. The
        third component is view depth: painter's sort needs it, orthographic
        projection just drops it."""
        th, ph = float(self.camera["theta"]), float(self.camera["phi"])
        xr = x * math.cos(th) + z * math.sin(th)
        zr = -x * math.sin(th) + z * math.cos(th)
        yr = y * math.cos(ph) - zr * math.sin(ph)
        zv = y * math.sin(ph) + zr * math.cos(ph)
        return xr, yr, zv

    def _project(self, x: float, y: float, z: float) -> tuple[float, float]:
        """Orthographic orbit camera: rotate, then drop the depth."""
        xr, yr, _zv = self._rotate(x, y, z)
        s = float(self.camera["scale"])
        return (float(self.camera["cx"]) + xr * s, float(self.camera["cy"]) + yr * s)

    @classmethod
    def _paint(cls, attrs_or_style: dict[str, Any]) -> str:
        bits: list[str] = []
        mapping = {"fill": "fill", "stroke": "stroke", "stroke_width": "stroke-width",
                   "opacity": "opacity", "fill_opacity": "fill-opacity",
                   "stroke_opacity": "stroke-opacity", "dash": "stroke-dasharray"}
        for key in _PAINT:
            if key in attrs_or_style and key in mapping and attrs_or_style[key] is not None:
                bits.append(f'{mapping[key]}="{escape(str(attrs_or_style[key]))}"')
        return (" " + " ".join(bits)) if bits else ""

    # ---------------------------------------------------------------- output
    def to_dict(self) -> dict[str, Any]:
        """The scene as JSON - this is the hook for a heavier frontend
        (react-three-fiber-style) to consume the same graph the SVG shows."""
        return {"width": self.width, "height": self.height, "background": self.background,
                "camera": dict(self.camera), "version": self.version,
                "nodes": [dict(n) for n in self._nodes]}

    def nodes(self) -> list[dict[str, Any]]:
        return [dict(n) for n in self._nodes]

    def to_svg(self) -> str:
        """Deterministic SVG text. Node order is paint order (no depth sort -
        that is the honest orthographic-wireframe look, and it stays diffable)."""
        parts: list[str] = []
        for node in self._nodes:
            parts.append(self._render_node(node))
        body = "".join(p for p in parts if p)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}">'
            f'<rect x="0" y="0" width="{self.width}" height="{self.height}" '
            f'fill="{escape(self.background)}"/>'
            f"{body}</svg>"
        )

    def _render_node(self, node: dict[str, Any]) -> str:
        kind = node.get("type")
        paint = self._paint(node)
        try:
            if kind == "rect":
                rx = f' rx="{_fmt(_num(node["rx"]))}"' if node.get("rx") else ""
                return (f'<rect x="{_fmt(node["x"])}" y="{_fmt(node["y"])}" '
                        f'width="{_fmt(node["w"])}" height="{_fmt(node["h"])}"{rx}{paint}/>')
            if kind == "circle":
                return (f'<circle cx="{_fmt(node["cx"])}" cy="{_fmt(node["cy"])}" '
                        f'r="{_fmt(node["r"])}"{paint}/>')
            if kind == "ellipse":
                return (f'<ellipse cx="{_fmt(node["cx"])}" cy="{_fmt(node["cy"])}" '
                        f'rx="{_fmt(node["rx"])}" ry="{_fmt(node["ry"])}"{paint}/>')
            if kind == "line":
                return (f'<line x1="{_fmt(node["x1"])}" y1="{_fmt(node["y1"])}" '
                        f'x2="{_fmt(node["x2"])}" y2="{_fmt(node["y2"])}"{paint}/>')
            if kind == "text":
                size = node.get("font_size", 14)
                anchor = node.get("anchor", "middle")
                weight = f' font-weight="{escape(str(node["weight"]))}"' if node.get("weight") else ""
                fill = node.get("fill", "#e6edf3")
                return (f'<text x="{_fmt(node["x"])}" y="{_fmt(node["y"])}" '
                        f'font-size="{_fmt(_num(size))}" text-anchor="{escape(str(anchor))}"'
                        f'{weight} fill="{escape(str(fill))}">{escape(str(node.get("text", "")))}</text>')
            if kind == "point3d":
                sx, sy = self._project(node["x"], node["y"], node["z"])
                return f'<circle cx="{_fmt(sx)}" cy="{_fmt(sy)}" r="{_fmt(node.get("r", 3.0))}"{paint}/>'
            if kind == "poly3d":
                pts = [self._project(px, py, pz) for px, py, pz in node.get("points", [])]
                if node.get("close") and pts:
                    pts = [*pts, pts[0]]
                path = "M" + " L".join(f"{_fmt(sx)} {_fmt(sy)}" for sx, sy in pts)
                return f'<path d="{path}" fill="none"{paint}/>'
            if kind == "mesh3d":
                return self._render_mesh(node)
            if kind == "cube3d":
                return self._render_cube(node)
        except (KeyError, TypeError, ValueError):
            return f'<!-- bad-node:{escape(str(node.get("id", "?")))} -->'
        return f'<!-- unknown-node:{escape(str(kind))} -->'

    def _render_mesh(self, node: dict[str, Any]) -> str:
        """Painter's-algorithm flat shading: faces sorted far-to-near by mean
        view depth, each filled by fill * lambert(normal, key-light)."""
        verts = node.get("vertices", [])
        faces = node.get("faces", [])
        fill = str(node.get("fill", "#58a6ff"))
        stroke = node.get("stroke")
        sw = node.get("stroke_width")
        shade = bool(node.get("shade", True))
        opacity = node.get("opacity")

        # rotate all vertices once; keep view depth per vertex
        view = [self._rotate(*v) for v in verts]
        light = _norm3(-0.4, -0.7, -0.6)     # key light, view space-ish
        rows = []
        for fi, face in enumerate(faces):
            if len(face) < 2:
                continue
            pts_view = [view[i] for i in face]
            depth = sum(p[2] for p in pts_view) / len(pts_view)
            normal = (0.0, 0.0, -1.0)
            if shade and len(face) >= 3:
                normal = _norm3(*_face_normal(view[face[0]], view[face[1]], view[face[2]]))
            inten = max(0.18, min(1.0, -(normal[0] * light[0] + normal[1] * light[1]
                                         + normal[2] * light[2])))
            rows.append((depth, fi, inten))
        rows.sort(key=lambda r: r[0])                      # far first
        parts: list[str] = []
        for _depth, fi, inten in rows:
            face = faces[fi]
            pts = [self._project(*verts[i]) for i in face]
            d = "M" + " L".join(f"{_fmt(sx)} {_fmt(sy)}" for sx, sy in pts) + " Z"
            color = _shade(fill, inten) if shade else fill
            stroke_attr = f' stroke="{escape(str(stroke))}"' if stroke else ""
            if sw is not None and stroke:
                stroke_attr += f' stroke-width="{_fmt(_num(sw))}"'
            op = f' opacity="{escape(str(opacity))}"' if opacity is not None else ""
            parts.append(f'<path d="{d}" fill="{escape(color)}"{stroke_attr}{op}/>')
        return "".join(parts)

    def _render_cube(self, node: dict[str, Any]) -> str:
        cx, cy, cz, size = node["cx"], node["cy"], node["cz"], node["size"]
        spin = math.radians(float(node.get("spin", 0.0)))
        h = size / 2.0
        corners: list[list[float]] = []
        for dx in (-h, h):
            for dy in (-h, h):
                for dz in (-h, h):
                    # per-cube spin about its own Y axis
                    rx = dx * math.cos(spin) + dz * math.sin(spin)
                    rz = -dx * math.sin(spin) + dz * math.cos(spin)
                    corners.append([cx + rx, cy + dy, cz + rz])
        faces = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6],
                 [0, 2, 6, 4], [1, 5, 7, 3]]
        parts: list[str] = []
        if node.get("fill") not in (None, "", "none"):
            # solid cube: faces painted first (shaded), edges on top
            face_node = {**node, "vertices": [tuple(c) for c in corners], "faces": faces,
                         "stroke": None, "stroke_width": None}
            parts.append(self._render_mesh(face_node))
        edges = [(0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3),
                 (2, 6), (3, 7), (4, 5), (4, 6), (5, 7), (6, 7)]
        paint = self._paint({k: v for k, v in node.items() if k != "fill"}) or ' stroke="#58a6ff"'
        for a, b in edges:
            x1, y1 = self._project(*corners[a])
            x2, y2 = self._project(*corners[b])
            parts.append(f'<line x1="{_fmt(x1)}" y1="{_fmt(y1)}" x2="{_fmt(x2)}" y2="{_fmt(y2)}"{paint}/>')
        return "".join(parts)
