"""A deliberate subset of JSON Schema 2020-12, implemented without deps.

Why not `jsonschema`? ADR-0001: the core has to import inside arbitrary agent
sandboxes where installing/pinning third-party code is a liability. We support
exactly the keywords tool authors need - and we *validate the schema itself* at
registration time so a bad manifest fails loudly at load, not mid-task.

Supported: type, properties, required, additionalProperties, items, enum,
const, anyOf/oneOf/allOf/not, min/max(imum|Length), pattern, minLength,
format(date-time is structural only), description/default, nullable-free
style (use type arrays), uniqueItems, minItems/maxItems.
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import re
from typing import Any

_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}

_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}

MAX_DEPTH = 12


class SchemaError(ValueError):
    """The schema itself is malformed."""


class ValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"{e['path'] or '<root>'}: {e['message']}" for e in errors) or "invalid")
        self.errors = errors


def _re(pattern: str) -> re.Pattern[str]:
    rx = _PATTERN_CACHE.get(pattern)
    if rx is None:
        rx = re.compile(pattern)
        _PATTERN_CACHE[pattern] = rx
    return rx


# ------------------------------------------------------------------ validation


def validate(instance: Any, schema: dict[str, Any], *, path: str = "") -> list[dict[str, Any]]:
    """Return a list of {path, message, keyword} dicts; empty list == valid."""
    errors: list[dict[str, Any]] = []
    _validate_into(instance, schema, path, errors, 0)
    return errors


def _err(errors: list, path: str, message: str, keyword: str, **extra: Any) -> None:
    errors.append({"path": path, "message": message, "keyword": keyword, **extra})


def _type_ok(value: Any, tname: str) -> bool:
    if tname == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if tname == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if tname == "boolean":
        return isinstance(value, bool)
    types = _TYPE_MAP.get(tname)
    return types is not None and isinstance(value, types)


def _validate_into(value: Any, schema: Any, path: str, errors: list, depth: int) -> None:
    if depth > MAX_DEPTH:
        _err(errors, path, "schema nesting exceeds MAX_DEPTH", "depth")
        return
    if schema is True or schema == {}:
        return
    if schema is False:
        _err(errors, path, "schema forbids any value here", "false")
        return
    if not isinstance(schema, dict):
        raise SchemaError(f"schema at {path or '<root>'} must be an object or boolean")

    t = schema.get("type")
    if t is not None:
        names = [t] if isinstance(t, str) else list(t)
        for bad in names:
            if bad not in _TYPE_MAP:
                raise SchemaError(f"unknown type {bad!r} at {path or '<root>'}")
        if not any(_type_ok(value, n) for n in names):
            _err(errors, path, f"expected type {'|'.join(names)}, got {type(value).__name__}", "type",
                 expected=names)
            return  # further keyword checks would be noise

    if "const" in schema and value != schema["const"]:
        _err(errors, path, f"must equal {schema['const']!r}", "const")
    if "enum" in schema:
        allowed = schema["enum"]
        if not any(value == a or _loose_eq(value, a) for a in allowed):
            _err(errors, path, f"must be one of {allowed!r}", "enum", allowed=allowed)

    if isinstance(value, dict):
        props: dict[str, Any] = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                _err(errors, _join(path, key), f"required property {key!r} is missing", "required",
                     missing=key)
        extra_keys = [k for k in value if k not in props]
        addl = schema.get("additionalProperties", True)
        if addl is False and extra_keys:
            _err(errors, path, f"additional properties not allowed: {sorted(extra_keys)!r}",
                 "additionalProperties", unexpected=sorted(extra_keys))
        elif isinstance(addl, dict):
            for k in extra_keys:
                _validate_into(value[k], addl, _join(path, k), errors, depth + 1)
        for k, sub in props.items():
            if k in value:
                _validate_into(value[k], sub, _join(path, k), errors, depth + 1)
        if (mn := schema.get("minProperties")) is not None and len(value) < mn:
            _err(errors, path, f"needs >= {mn} properties", "minProperties")
        if (mx := schema.get("maxProperties")) is not None and len(value) > mx:
            _err(errors, path, f"needs <= {mx} properties", "maxProperties")

    if isinstance(value, (list, tuple)):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                _validate_into(item, items, f"{path}[{i}]", errors, depth + 1)
        elif isinstance(items, list):
            for i, item in enumerate(value):
                if i < len(items):
                    _validate_into(item, items[i], f"{path}[{i}]", errors, depth + 1)
        if (mn := schema.get("minItems")) is not None and len(value) < mn:
            _err(errors, path, f"needs >= {mn} items", "minItems")
        if (mx := schema.get("maxItems")) is not None and len(value) > mx:
            _err(errors, path, f"needs <= {mx} items", "maxItems")
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for item in value:
                if any(item == s for s in seen):
                    _err(errors, path, "items must be unique", "uniqueItems")
                    break
                seen.append(item)

    if isinstance(value, str):
        if (mn := schema.get("minLength")) is not None and len(value) < mn:
            _err(errors, path, f"string shorter than minLength {mn}", "minLength")
        if (mx := schema.get("maxLength")) is not None and len(value) > mx:
            _err(errors, path, f"string longer than maxLength {mx}", "maxLength")
        if (p := schema.get("pattern")) is not None and not _re(p).search(value):
            _err(errors, path, f"does not match pattern {p!r}", "pattern")
        if (fmt := schema.get("format")):
            _check_format(value, fmt, path, errors)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for key, fn in (("minimum", lambda a, b: a < b), ("exclusiveMinimum", lambda a, b: a <= b),
                        ("maximum", lambda a, b: a > b), ("exclusiveMaximum", lambda a, b: a >= b)):
            if (lim := schema.get(key)) is not None and fn(value, lim):
                _err(errors, path, f"{value} violates {key}={lim}", key)

    for combinator in ("allOf", "anyOf", "oneOf"):
        subs = schema.get(combinator)
        if not subs:
            continue
        matches = 0
        for i, sub in enumerate(subs):
            sub_errors: list[dict[str, Any]] = []
            _validate_into(value, sub, path, sub_errors, depth + 1)
            if not sub_errors:
                matches += 1
            if combinator == "allOf" and sub_errors:
                _err(errors, path, f"failed allOf[{i}]: {sub_errors[0]['message']}", "allOf")
        if combinator == "anyOf" and matches == 0:
            _err(errors, path, "does not match any allowed variant", "anyOf")
        if combinator == "oneOf" and matches != 1:
            _err(errors, path, f"matches {matches} oneOf variants, need exactly 1", "oneOf")

    if (neg := schema.get("not")) is not None:
        sub_errors = []
        _validate_into(value, neg, path, sub_errors, depth + 1)
        if not sub_errors:
            _err(errors, path, "value is explicitly disallowed by 'not'", "not")


def _loose_eq(a: Any, b: Any) -> bool:
    return type(a) is type(b) and a == b


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _check_format(value: str, fmt: str, path: str, errors: list[dict[str, Any]]) -> None:
    """`format` is advisory in JSON Schema, but not here: an agent that passes a
    malformed timestamp or a bad regex to a tool would otherwise only find out when
    the handler crashed. Unknown formats are ignored, per spec."""
    fn = _FORMAT_CHECKS.get(fmt)
    if fn is None:
        return
    try:
        ok = bool(fn(value))
    except Exception:
        return
    if not ok:
        _err(errors, path, f"not a valid {fmt}", "format", format=fmt)


_FORMAT_CHECKS = {
    "date-time": lambda s: _parse_dt(s),
    "date": lambda s: _parse_date(s),
    "uuid": lambda s: bool(_re(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$").match(s)),
    "ipv4": lambda s: _ip_ok(s, 4),
    "ipv6": lambda s: _ip_ok(s, 6),
    "uri": lambda s: bool(_re(r"^[a-zA-Z][a-zA-Z0-9+.-]*:.*", ).match(s)),
    "regex": lambda s: _regex_ok(s),
}


def _parse_dt(s: str) -> bool:
    try:
        _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _parse_date(s: str) -> bool:
    try:
        _dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _ip_ok(s: str, version: int) -> bool:
    try:
        return ipaddress.ip_address(s).version == version
    except ValueError:
        return False


def _regex_ok(s: str) -> bool:
    try:
        re.compile(s)
        return True
    except re.error:
        return False


# ------------------------------------------------------------------- schema meta


def check_schema(schema: dict[str, Any], *, path: str = "") -> list[str]:
    """Validate that a schema is one we can actually honor. Returns problems."""
    problems: list[str] = []
    here = path or "<root>"
    if not isinstance(schema, dict):
        return [f"{here}: schema must be an object"]
    known = {"type", "properties", "required", "additionalProperties", "items", "enum", "const",
             "anyOf", "oneOf", "allOf", "not", "minimum", "maximum", "exclusiveMinimum",
             "exclusiveMaximum", "minLength", "maxLength", "pattern", "format", "description",
             "default", "title", "examples", "minItems", "maxItems", "uniqueItems", "minProperties",
             "maxProperties", "propertyNames", "$defs", "$ref", "nullable", "readOnly", "writeOnly"}
    for key in schema:
        if key not in known:
            problems.append(f"{here}: unsupported keyword {key!r} (would be silently ignored)")
    if "$ref" in schema:
        problems.append(f"{here}: $ref is not supported at runtime; inline the subschema")
    if (t := schema.get("type")) is not None:
        names = [t] if isinstance(t, str) else t
        if not isinstance(names, list) or any(n not in _TYPE_MAP for n in names):
            problems.append(f"{here}: bad type {t!r}")
    if (p := schema.get("pattern")) is not None:
        try:
            re.compile(p)
        except re.error as exc:
            problems.append(f"{here}: invalid pattern: {exc}")
    for k, sub in (schema.get("properties") or {}).items():
        problems += check_schema(sub, path=f"{here}.properties.{k}")
    if isinstance(schema.get("items"), dict):
        problems += check_schema(schema["items"], path=f"{here}.items")
    if isinstance(schema.get("additionalProperties"), dict):
        problems += check_schema(schema["additionalProperties"], path=f"{here}.additionalProperties")
    for comb in ("anyOf", "oneOf", "allOf"):
        for i, sub in enumerate(schema.get(comb) or []):
            problems += check_schema(sub, path=f"{here}.{comb}[{i}]")
    return problems


def apply_defaults(instance: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Fill declared defaults for absent optional properties (mutates a copy)."""
    out = dict(instance)
    for key, sub in (schema.get("properties") or {}).items():
        if key not in out and isinstance(sub, dict) and "default" in sub:
            out[key] = sub["default"]
    return out


def annotate_required(schema: dict[str, Any], names: set[str]) -> dict[str, Any]:
    """Make explicit a *logical* requirement inferred from other args (helper)."""
    req = set(schema.get("required", [])) | names
    return {**schema, "required": sorted(req)}
