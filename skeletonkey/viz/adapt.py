"""Draw *your* tools, whatever shape they are in.

The visualizer projects a `core.registry.Registry`. Everyone else has a list
of dicts - OpenAI function schemas, an MCP `tools/list` response, a hand-rolled
catalog. The measured first experience of an outsider was:

    >>> build_viz_model(my_tools)
    AttributeError: 'list' object has no attribute 'all'

That is a discovery problem, not an architecture problem: `ToolManifest`
requires only `id` of its 38 fields, so the conversion was always cheap - you
just had to read the source to find that out. This module does it for you.

    from skeletonkey.viz import build_viz_model, render_html

    model = build_viz_model(my_openai_functions)     # just works
    open("tools.html", "w").write(render_html(model))

Accepted shapes:

* anything with `.all()` and `.advertise()` - a real Registry, passed through
  untouched
* OpenAI / Anthropic style: ``{"name": ..., "description": ..., "parameters"
  | "input_schema": {...}}``, optionally wrapped as ``{"type": "function",
  "function": {...}}``
* MCP `tools/list`: ``{"name", "description", "inputSchema"}``, or the whole
  ``{"tools": [...]}`` envelope
* a bare ``{"tool_id": {...}}`` mapping
* `ToolManifest` objects, mixed in freely

## What this deliberately does NOT do

It does not guess. Risk, destructiveness and reversibility are safety-relevant
claims, and a plausible-looking guess is worse than a blank: it makes the page
*look* authoritative about a property nobody actually checked. A converted
tool with no declared risk is marked `unknown_risk` in `meta`, and the page can
say "not declared" rather than quietly printing "read".

The one inference made is `group`, from a dotted id (`db.query` -> `db`),
because a group is a display grouping with no safety meaning at all.
"""

from __future__ import annotations

from typing import Any

from skeletonkey.core.manifest import RISK_ORDER, ToolManifest

__all__ = ["ADAPTED_SOURCE", "as_registry", "to_manifests"]

#: `source` stamped on every converted manifest, so a model built from foreign
#: input is distinguishable from one built off a live registry.
ADAPTED_SOURCE = "adapted"


def _unusable_handler(*_a: Any, **_kw: Any) -> Any:
    """Stand-in handler for a tool we can only *describe*.

    The registry requires a handler because a registered tool is normally
    callable. An adapted catalog is documentation: there is nothing to call,
    and pretending otherwise would let a mistake reach a caller. So this
    raises rather than returning a benign value.
    """
    raise RuntimeError(
        "this tool came from an adapted catalog and exists for visualisation "
        "only - it has no implementation to call")


def _first_str(d: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def _schema_of(d: dict[str, Any]) -> dict[str, Any]:
    for k in ("input_schema", "inputSchema", "parameters", "schema"):
        v = d.get(k)
        if isinstance(v, dict):
            return v
    return {}


def _unwrap(entry: Any) -> dict[str, Any] | None:
    """Peel the wrappers vendors put around a function schema."""
    if not isinstance(entry, dict):
        return None
    # {"type": "function", "function": {...}}
    inner = entry.get("function")
    if isinstance(inner, dict) and ("name" in inner or "id" in inner):
        return inner
    return entry


def _group_of(tool_id: str, explicit: str) -> str:
    """Infer a display group from a dotted id.

    Safe to infer precisely because it carries no safety meaning - it decides
    which colour the node is, nothing more.
    """
    if explicit:
        return explicit
    for sep in (".", "/", "__", "-"):
        if sep in tool_id:
            head = tool_id.split(sep, 1)[0]
            if head:
                return head
    return "tools"


def _manifest_from_dict(raw: dict[str, Any]) -> ToolManifest:
    tool_id = _first_str(raw, "id", "name", "tool", "tool_name")
    if not tool_id:
        raise ValueError(f"tool entry has no id/name: {sorted(raw)[:6]}")

    declared_risk = _first_str(raw, "risk")
    known_risk = declared_risk in RISK_ORDER

    meta = dict(raw.get("meta") or {})
    meta["adapted_from"] = "dict"
    if not known_risk:
        # Say so, loudly and in the data, rather than defaulting to something
        # reassuring. The page can then render "not declared".
        meta["unknown_risk"] = True
        if declared_risk:
            meta["declared_risk_unrecognised"] = declared_risk

    kwargs: dict[str, Any] = {
        "id": tool_id,
        "title": _first_str(raw, "title", "display_name", default=tool_id),
        "description": _first_str(raw, "description", "summary", "doc"),
        "group": _group_of(tool_id, _first_str(raw, "group", "category", "namespace")),
        "input_schema": _schema_of(raw),
        "capability": _first_str(raw, "capability", default=tool_id),
        "source": ADAPTED_SOURCE,
        "handler": _unusable_handler,
        "meta": meta,
    }
    if known_risk:
        kwargs["risk"] = declared_risk

    # Booleans are only carried across when the source actually stated them.
    for key in ("destructive", "reversible", "idempotent"):
        if isinstance(raw.get(key), bool):
            kwargs[key] = raw[key]
    for key in ("tier", "version", "provider"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            kwargs[key] = v.strip()
    for key in ("tags", "examples", "see_also", "anti_patterns"):
        v = raw.get(key)
        if isinstance(v, list):
            kwargs[key] = v

    return ToolManifest(**kwargs)


def to_manifests(source: Any) -> list[ToolManifest]:
    """Convert any accepted shape into a list of `ToolManifest`."""
    if source is None:
        return []

    # {"tools": [...]} - the MCP tools/list envelope
    if isinstance(source, dict) and isinstance(source.get("tools"), list):
        source = source["tools"]
    # {"id": {...}} - a mapping of tools
    elif isinstance(source, dict):
        rebuilt = []
        for key, value in source.items():
            if isinstance(value, dict):
                entry = dict(value)
                entry.setdefault("id", key)
                rebuilt.append(entry)
            elif isinstance(value, ToolManifest):
                rebuilt.append(value)
        source = rebuilt

    if isinstance(source, ToolManifest):
        source = [source]

    out: list[ToolManifest] = []
    for entry in source:
        if isinstance(entry, ToolManifest):
            out.append(entry)
            continue
        unwrapped = _unwrap(entry)
        if unwrapped is None:
            raise TypeError(f"cannot adapt a {type(entry).__name__} into a tool")
        out.append(_manifest_from_dict(unwrapped))
    return out


def as_registry(source: Any) -> Any:
    """Return something `build_viz_model` can project.

    A real registry passes straight through, so this is safe to call
    unconditionally. Anything else is converted and loaded into a fresh
    `Registry`, which means adapted tools get the same gate evaluation,
    dedupe and budget logic as native ones - the page is not a second,
    weaker code path.
    """
    if hasattr(source, "all") and hasattr(source, "advertise"):
        return source

    from skeletonkey.core.registry import Registry

    registry = Registry()
    seen: set[str] = set()
    for man in to_manifests(source):
        if man.id in seen:          # last one wins, like a dict literal would
            registry.register(man, replace=True)
        else:
            registry.register(man)
            seen.add(man.id)
    return registry
