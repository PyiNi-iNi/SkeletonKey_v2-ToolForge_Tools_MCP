"""Tool export for external agent formats (P7 exploratory).

Exports the live registry's advertised tools into schemas that foreign
agent frameworks can consume. Each exporter is a pure function of the
registry state: no network calls, no secret-bearing metadata.

Supported formats:

* ``openai-tools`` — OpenAI function-calling tool list (``tools`` array
  for the Chat Completions API).
* ``anthropic-tools`` — Anthropic tool-use format (``tools`` array for
  the Messages API).
* ``mcp-manifest`` — A minimal MCP tool listing (``tools`` array with
  inputSchema, matching the MCP ``tools/list`` response shape).
* ``viz-html`` — The standalone tool-visualizer page (``skeletonkey.viz``):
  a single self-contained HTML file describing the whole toolset. Unlike the
  other three it is a projection of the *registry*, not of a manifest list,
  so it is produced in ``export_from_registry`` rather than by an entry in
  ``_EXPORTERS``.

Security: the exporter strips secret_args, redacts credential-shaped
default values in schemas, and never emits approval-only or
non-advertised tools. The output is a snapshot, not a live binding.
"""

from __future__ import annotations

import json
import re
from typing import Any

from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.core.manifest import ToolManifest

# Patterns that look like credentials in default values. If a schema property's
# default matches one of these, it is replaced with a placeholder in the export
# so the exported spec never contains a real secret.
_CREDENTIAL_PATTERNS = [
    re.compile(r"^(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}"),      # GitHub PATs
    re.compile(r"^sk-(live|test|proj)-[A-Za-z0-9]{20,}"),         # OpenAI / Stripe keys
    re.compile(r"^sk-[A-Za-z0-9]{20,}"),                           # OpenAI / Stripe (short)
    re.compile(r"^xox[baprs]-[A-Za-z0-9-]+"),                     # Slack tokens
    re.compile(r"^AKIA[0-9A-Z]{16}$"),                             # AWS access key
    re.compile(r"^eyJ[A-Za-z0-9_-]+\."),                           # JWT
    re.compile(r"^[A-Za-z0-9/+]{40}=$"),                           # base64 secret
]

_PLACEHOLDER = "***REDACTED***"

# Export format identifiers
FORMATS = ("openai-tools", "anthropic-tools", "mcp-manifest", "viz-html")

# The one format that needs the registry rather than a manifest list: the
# visualizer renders gate receipts and withheld tools, which a list of
# advertised manifests cannot describe.
REGISTRY_FORMATS = ("viz-html",)


def _redact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Walk a JSON Schema and replace credential-shaped default values."""
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "default" and isinstance(value, str) and _looks_like_secret(value):
            out[key] = _PLACEHOLDER
        elif isinstance(value, dict):
            out[key] = _redact_schema(value)
        elif isinstance(value, list):
            out[key] = [_redact_schema(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    return out


def _looks_like_secret(value: str) -> bool:
    """Check if a string looks like a credential."""
    if not value or len(value) < 8:
        return False
    return any(pat.match(value) for pat in _CREDENTIAL_PATTERNS)


def _clean_input_schema(man: ToolManifest) -> dict[str, Any]:
    """Produce an export-safe input schema.

    Strips internal keywords, redacts credential-shaped defaults, and
    removes secret_args from required lists (they should never be passed
    by an external agent).
    """
    schema = man.input_schema_for_host()
    cleaned = _redact_schema(schema)

    # Remove secret args from required
    if man.secret_args and "required" in cleaned:
        cleaned["required"] = [
            r for r in cleaned["required"] if r not in man.secret_args
        ]
        if not cleaned["required"]:
            del cleaned["required"]

    # Remove secret arg properties entirely
    if man.secret_args and "properties" in cleaned:
        for key in man.secret_args:
            cleaned["properties"].pop(key, None)

    return cleaned


def _description_for(man: ToolManifest) -> str:
    """First sentence of the description; foreign agents benefit from brevity."""
    desc = man.description.strip()
    if not desc:
        desc = man.title or man.id
    # Truncate at the first period + space or newline for a clean first sentence
    for sep in (". ", "\n"):
        idx = desc.find(sep)
        if idx > 0:
            desc = desc[:idx + 1]
            break
    return desc


# ---- OpenAI tools format ---------------------------------------------------

def _to_openai_function(man: ToolManifest) -> dict[str, Any]:
    """Convert one manifest to an OpenAI function-calling tool entry."""
    return {
        "type": "function",
        "function": {
            "name": man.id.replace(".", "__"),  # OpenAI disallows dots
            "description": _description_for(man),
            "parameters": _clean_input_schema(man),
        },
    }


def export_openai_tools(manifests: list[ToolManifest]) -> dict[str, Any]:
    """Export manifests to the OpenAI function-calling tools array.

    Returns ``{"tools": [...]}`` ready to embed in a Chat Completions request.
    """
    tools = [_to_openai_function(m) for m in manifests]
    return {"tools": tools, "count": len(tools)}


# ---- Anthropic tools format ------------------------------------------------

def _to_anthropic_tool(man: ToolManifest) -> dict[str, Any]:
    """Convert one manifest to an Anthropic tool-use entry."""
    schema = _clean_input_schema(man)
    # Anthropic uses input_schema directly (no "parameters" wrapper)
    return {
        "name": man.id.replace(".", "__"),
        "description": _description_for(man),
        "input_schema": schema,
    }


def export_anthropic_tools(manifests: list[ToolManifest]) -> dict[str, Any]:
    """Export manifests to the Anthropic tool-use tools array.

    Returns ``{"tools": [...]}`` ready to embed in a Messages API request.
    """
    tools = [_to_anthropic_tool(m) for m in manifests]
    return {"tools": tools, "count": len(tools)}


# ---- MCP manifest format ---------------------------------------------------

def _to_mcp_tool(man: ToolManifest) -> dict[str, Any]:
    """Convert one manifest to an MCP tools/list entry."""
    return {
        "name": man.id,
        "description": _description_for(man),
        "inputSchema": _clean_input_schema(man),
    }


def export_mcp_manifest(manifests: list[ToolManifest]) -> dict[str, Any]:
    """Export manifests to the MCP tools/list response shape.

    Returns ``{"tools": [...]}`` matching the MCP specification.
    """
    tools = [_to_mcp_tool(m) for m in manifests]
    return {"tools": tools, "count": len(tools)}


# ---- Dispatcher ------------------------------------------------------------

_EXPORTERS = {
    "openai-tools": export_openai_tools,
    "anthropic-tools": export_anthropic_tools,
    "mcp-manifest": export_mcp_manifest,
}


def export_tools(
    manifests: list[ToolManifest],
    fmt: str,
    *,
    secret_bearing_check: bool = True,
) -> dict[str, Any]:
    """Export a list of manifests to the requested format.

    Parameters
    ----------
    manifests:
        The tool manifests to export. Should be pre-filtered to advertised tools.
    fmt:
        One of ``openai-tools``, ``anthropic-tools``, ``mcp-manifest``.
    secret_bearing_check:
        If True (default), validate that no exported schema contains
        secret-bearing metadata. Raises if a leak is detected.

    Returns
    -------
    dict with ``tools`` (list) and ``count`` (int).
    """
    if fmt not in _EXPORTERS:
        raise SkeletonKeyError(
            E.BAD_ARGS,
            f"Unknown export format {fmt!r}",
            details={"available": list(_EXPORTERS)},
        )

    result = _EXPORTERS[fmt](manifests)

    if secret_bearing_check:
        _audit_export(result)

    return result


def _audit_export(result: dict[str, Any]) -> None:
    """Walk the exported JSON and verify no credential-shaped strings leaked."""
    serialized = json.dumps(result)
    for pattern in _CREDENTIAL_PATTERNS:
        matches = pattern.findall(serialized)
        if matches:
            raise SkeletonKeyError(
                E.INTERNAL,
                "Export audit detected credential-shaped strings in output",
                details={"pattern": pattern.pattern, "count": len(matches)},
            )


def export_from_registry(
    registry: Any,
    fmt: str,
    *,
    tier: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    """Export the registry's advertised tools to the requested format.

    This is the top-level entry point for CLI and API use.
    """
    if fmt in REGISTRY_FORMATS:
        return _export_viz_html(registry, tier=tier, read_only=read_only)
    ad = registry.advertise(
        tier=tier,
        read_only=read_only,
        include_internal=False,
    )
    return export_tools(ad.tools, fmt)


def _export_viz_html(registry: Any, *, tier: str | None,
                     read_only: bool) -> dict[str, Any]:
    """The visualizer page as an export artifact.

    Kept out of ``_EXPORTERS`` because it takes the registry: the page shows
    *withheld* tools and the receipts that withheld them, and a list of
    advertised manifests has already thrown that information away.
    """
    from skeletonkey.viz import build_viz_model, render_html

    model = build_viz_model(registry, tier=tier, read_only=read_only)
    html = render_html(model)
    result = {
        "format": "viz-html",
        "html": html,
        "count": model.aggregates["advertised"],
        "registered": model.aggregates["registered"],
        "digest": model.digest(),
    }
    _audit_export({"html": html})
    return result


def list_formats() -> list[str]:
    """Return the available export format identifiers."""
    return [*_EXPORTERS, *REGISTRY_FORMATS]
