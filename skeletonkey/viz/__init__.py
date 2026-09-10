"""Tool visualizer (V0: the data layer).

The visualizer is a *projection* of what the registry already knows: it never
holds a second copy of the toolset, never mutates anything, and never invents a
tool. `build_viz_model(...)` folds `registry.all()` (everything registered) and
`registry.advertise()` (what this host is actually allowed to see) into one
JSON-able document that a page, a diff, or a CI check can consume.

Phase plan: `docs/TOOL-VISUALIZER-PLAN.md`. V0 is headless on purpose - the
model is testable without a browser, and `sk viz --json` prints it.
"""

from __future__ import annotations

from .explain import Chain, Step, diff_models, explain_model, explain_tool, project
from .model import (
    VizEdge,
    VizModel,
    VizNode,
    build_viz_model,
    model_digest,
    summary_lines,
)
from .render import render_html
from .runtime import ToolRuntime, attach_runtime, find_drift, read_runtime
from .server import VizServer, serve

__all__ = [
    "Chain",
    "Step",
    "ToolRuntime",
    "VizEdge",
    "VizModel",
    "VizNode",
    "VizServer",
    "attach_runtime",
    "build_viz_model",
    "diff_models",
    "explain_model",
    "explain_tool",
    "find_drift",
    "model_digest",
    "project",
    "read_runtime",
    "render_html",
    "serve",
    "summary_lines",
]
