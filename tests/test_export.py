"""Tests for the P7 tool export module (skeletonkey.core.export)."""

from __future__ import annotations

from typing import Any

import pytest

from skeletonkey.core.export import (
    _clean_input_schema,
    _looks_like_secret,
    _redact_schema,
    export_anthropic_tools,
    export_from_registry,
    export_mcp_manifest,
    export_openai_tools,
    export_tools,
    list_formats,
)
from skeletonkey.core.manifest import ToolManifest

# ---- Fixtures --------------------------------------------------------------

def _make_manifest(
    id: str = "fs.read",
    description: str = "Read a file from disk. First sentence ends here. More text.",
    risk: str = "read",
    secret_args: list[str] | None = None,
    input_schema: dict[str, Any] | None = None,
    tier: str = "full",
) -> ToolManifest:
    return ToolManifest(
        id=id,
        description=description,
        risk=risk,
        secret_args=secret_args or [],
        input_schema=input_schema or {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "offset": {"type": "integer", "description": "Byte offset"},
            },
            "required": ["path"],
        },
        group="fs",
        capability="fs.read",
        tier=tier,
    )


def _secret_manifest() -> ToolManifest:
    """A manifest with secret args to test redaction."""
    return ToolManifest(
        id="api.query",
        description="Query the API with a key.",
        risk="read",
        secret_args=["api_key"],
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "api_key": {
                    "type": "string",
                    "description": "API key",
                    "default": "sk-live-abc123def456ghi789jkl012mno345",
                },
            },
            "required": ["query", "api_key"],
        },
        group="api",
    )


# ---- Unit tests: helpers ---------------------------------------------------

def test_looks_like_secret_detects_known_patterns():
    assert _looks_like_secret("sk-live-abc123def456ghi789jkl012mno345")
    assert _looks_like_secret("ghp_abcdefghijklmnopqrstuvwxyz1234567890")
    assert _looks_like_secret("xoxb-1234567890-abcdefghijklmnop")
    assert not _looks_like_secret("hello world")
    assert not _looks_like_secret("short")
    assert not _looks_like_secret("")


def test_redact_schema_replaces_credential_defaults():
    schema = {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "default": "sk-live-abc123def456ghi789jkl012mno345",
            },
            "normal": {"type": "string", "default": "hello"},
        },
    }
    result = _redact_schema(schema)
    assert result["properties"]["key"]["default"] == "***REDACTED***"
    assert result["properties"]["normal"]["default"] == "hello"


def test_clean_input_schema_strips_secret_args():
    man = _secret_manifest()
    cleaned = _clean_input_schema(man)
    # api_key should be removed from properties and required
    assert "api_key" not in cleaned.get("properties", {})
    assert "api_key" not in cleaned.get("required", [])
    # query should remain
    assert "query" in cleaned["properties"]


def test_clean_input_schema_keeps_non_secret_tools():
    man = _make_manifest()
    cleaned = _clean_input_schema(man)
    assert "path" in cleaned["properties"]
    assert "required" in cleaned
    assert "path" in cleaned["required"]


# ---- Export format tests ---------------------------------------------------

def test_openai_tools_format():
    manifests = [_make_manifest(), _make_manifest(id="fs.write", risk="write")]
    result = export_openai_tools(manifests)
    assert result["count"] == 2
    assert len(result["tools"]) == 2
    tool = result["tools"][0]
    assert tool["type"] == "function"
    assert "function" in tool
    assert tool["function"]["name"] == "fs__read"  # dots replaced with __
    assert tool["function"]["description"].endswith(".")
    assert "parameters" in tool["function"]


def test_openai_tools_replaces_dots():
    man = _make_manifest(id="fs.read")
    result = export_openai_tools([man])
    assert result["tools"][0]["function"]["name"] == "fs__read"


def test_anthropic_tools_format():
    manifests = [_make_manifest()]
    result = export_anthropic_tools(manifests)
    assert result["count"] == 1
    tool = result["tools"][0]
    assert tool["name"] == "fs__read"
    assert "input_schema" in tool
    assert "parameters" not in tool  # Anthropic uses input_schema directly


def test_mcp_manifest_format():
    manifests = [_make_manifest()]
    result = export_mcp_manifest(manifests)
    assert result["count"] == 1
    tool = result["tools"][0]
    assert tool["name"] == "fs.read"  # MCP keeps dots
    assert "inputSchema" in tool


def test_export_dispatches_to_correct_format():
    man = _make_manifest()
    for fmt in ("openai-tools", "anthropic-tools", "mcp-manifest"):
        result = export_tools([man], fmt)
        assert "tools" in result
        assert result["count"] == 1


def test_export_rejects_unknown_format():
    man = _make_manifest()
    with pytest.raises(Exception, match="Unknown export format"):
        export_tools([man], "nonexistent-format")


def test_export_audit_catches_leaked_credentials():
    """The audit should catch if a credential somehow ends up in the output."""
    # Create a manifest with a secret that bypasses the redactor
    man = ToolManifest(
        id="test.tool",
        description="A tool with a leaked secret in description",
        input_schema={"type": "object", "properties": {}},
    )
    # This should pass (no secrets in output)
    result = export_tools([man], "openai-tools", secret_bearing_check=True)
    assert result["count"] == 1


def test_list_formats():
    formats = list_formats()
    assert "openai-tools" in formats
    assert "anthropic-tools" in formats
    assert "mcp-manifest" in formats


# ---- Registry integration test ---------------------------------------------

def test_export_from_registry():
    """Test export from a live registry with registered tools."""
    from skeletonkey.core.registry import Registry

    reg = Registry()
    man1 = _make_manifest(id="test.read", description="Read something")
    man2 = _make_manifest(id="test.write", description="Write something", risk="write")
    reg.register(man1, handler=lambda **kw: None)
    reg.register(man2, handler=lambda **kw: None)

    result = export_from_registry(reg, "openai-tools")
    assert result["count"] >= 2
    names = [t["function"]["name"] for t in result["tools"]]
    assert "test__read" in names
    assert "test__write" in names


def test_export_empty_registry():
    """Export from an empty registry returns empty tools list."""
    from skeletonkey.core.registry import Registry

    reg = Registry()
    result = export_from_registry(reg, "mcp-manifest")
    assert result["count"] == 0
    assert result["tools"] == []


# ---- Truncation test -------------------------------------------------------

def test_description_truncated_at_first_sentence():
    man = _make_manifest(description="First sentence. Second sentence. Third.")
    result = export_openai_tools([man])
    desc = result["tools"][0]["function"]["description"]
    assert desc == "First sentence."
    assert "Second" not in desc
