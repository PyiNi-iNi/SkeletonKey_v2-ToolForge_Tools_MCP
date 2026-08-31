from __future__ import annotations

from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.core.manifest import Requirement, ToolManifest
from skeletonkey.core.registry import Registry
from skeletonkey.core.validate import apply_defaults, check_schema, validate


def test_required_and_type():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}},
              "required": ["a"], "additionalProperties": False}
    assert validate({"a": 1}, schema) == []
    errs = validate({"b": 2}, schema)
    codes = {(e["path"], e["keyword"]) for e in errs}
    assert ("a", "required") in codes and ("b", "type") in codes


def test_enum_pattern_bounds():
    schema = {"type": "object", "properties": {
        "d": {"type": "string", "enum": ["bash", "pwsh"]},
        "t": {"type": "number", "minimum": 0.1, "maximum": 10},
        "p": {"type": "string", "pattern": r"^[a-z]+$"}}}
    assert validate({"d": "bash", "t": 5, "p": "abc"}, schema) == []
    assert validate({"d": "cmd"}, schema)[0]["keyword"] == "enum"
    assert validate({"t": 50}, schema)[0]["keyword"] == "maximum"
    assert validate({"p": "AB1"}, schema)[0]["keyword"] == "pattern"


def test_bool_is_not_integer_and_int_is_number():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}, "f": {"type": "number"}}}
    assert validate({"n": True}, schema), "bool must not satisfy integer"
    assert validate({"n": 1, "f": 1.5}, schema) == []


def test_nested_items_and_anyof():
    schema = {"type": "object", "properties": {
        "edits": {"type": "array", "minItems": 1, "items": {"type": "object",
                "properties": {"old_text": {"type": "string"}}, "required": ["old_text"]}},
        "x": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}
    assert validate({"edits": [{"old_text": "a"}], "x": 3}, schema) == []
    assert validate({"edits": [{}]}, schema)[0]["keyword"] == "required"
    assert validate({"edits": []}, schema)[0]["keyword"] == "minItems"
    assert validate({"edits": [{"old_text": "a"}], "x": 1.5}, schema)[0]["keyword"] == "anyOf"


def test_apply_defaults():
    schema = {"type": "object", "properties": {"a": {"type": "integer", "default": 7},
                                               "b": {"type": "string", "enum": ["x", "y"], "default": "y"}}}
    assert apply_defaults({}, schema) == {"a": 7, "b": "y"}
    assert apply_defaults({"a": 1}, schema) == {"a": 1, "b": "y"}


def test_check_schema_rejects_unsupported_keywords_and_bad_refs():
    assert check_schema({"type": "object", "properties": {"a": {"type": "nonsense"}}})
    assert any("$ref" in p for p in check_schema({"$ref": "#/definitions/x"}))
    assert check_schema({"type": "object", "properties": {"a": {"type": "string"}}}) == []
    assert check_schema({"type": "object", "pattern": "(unclosed"})


def test_manifest_rejects_broken_schema_and_bad_id():
    try:
        ToolManifest(id="fs.bad", input_schema={"type": "object", "properties": {"x": {"type": "wat"}}})
        raise AssertionError("should have raised")
    except SkeletonKeyError as exc:
        assert exc.code == "BAD_ARGS"
        assert "problems" in exc.details
    try:
        ToolManifest(id="Bad ID")
        raise AssertionError("should have raised")
    except SkeletonKeyError as exc:
        assert exc.code == "BAD_ARGS"


def test_manifest_derived_fields():
    m = ToolManifest(id="fs.read", description="d", risk="read",
                     requirements=[Requirement("binary", "rg", min_version="13")], tags=["x"],
                     input_schema={"type": "object", "properties": {"p": {"type": "string"}}})
    assert m.group == "fs" and m.capability == "fs.read"
    assert m.mcp_name == "fs_read"
    assert m.is_mutating is False
    assert m.effective_timeout(None) == m.timeout_s
    assert m.effective_timeout(1e9) == m.timeout_s          # clamped to the manifest cap
    assert m.effective_timeout(1) == 1.0
    d = m.to_dict()
    assert d["requires"] == [{"kind": "binary", "name": "rg", "min_version": "13"}]
    assert "input_schema" in d
    assert "handler" not in d


def test_registry_duplicate_registration_is_a_conflict():
    reg = Registry()
    m = ToolManifest(id="a.one", handler=lambda: {})
    reg.register(m)
    try:
        reg.register(ToolManifest(id="a.one", handler=lambda: {}))
        raise AssertionError("expected CONFLICT")
    except SkeletonKeyError as exc:
        assert exc.code == "CONFLICT"
    reg.register(ToolManifest(id="a.one", handler=lambda: {"ok": True}), replace=True)
    assert reg.get("a.one").description == ""


def test_unknown_tool_error_carries_suggestions():
    reg = Registry()
    reg.register(ToolManifest(id="fs.read", description="read files", handler=lambda: {}))
    try:
        reg.get("fs.red")
        raise AssertionError("expected UNKNOWN_TOOL")
    except SkeletonKeyError as exc:
        assert exc.code == "UNKNOWN_TOOL"
        assert exc.details["suggested"][0]["id"] == "fs.read"

def test_format_keywords_are_enforced_not_crashed():
    """`format` used to reach a missing helper: every tool schema with a format
    would have failed with INTERNAL instead of a usage error."""
    from skeletonkey.core.validate import validate

    errs = validate({"when": "yesterday"}, {"type": "object", "properties": {
        "when": {"type": "string", "format": "date"}}})
    assert [e["keyword"] for e in errs] == ["format"]
    assert not validate({"when": "2026-08-31"}, {"type": "object", "properties": {
        "when": {"type": "string", "format": "date"}}})
    # unknown formats stay advisory, per spec: a future format must not break a tool
    assert not validate({"x": "whatever"}, {"type": "object",
                                            "properties": {"x": {"type": "string", "format": "sku"}}})

