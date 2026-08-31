"""Type coercion for configuration values.

Env vars arrive as strings and hand-written TOML is inconsistent about quoting,
so every layer is coerced against the declared field types before a tool ever
sees it. A limit that is silently the *string* "2000000" is worse than no limit:
comparisons either raise or, worse, behave plausibly-but-wrongly somewhere that
trusts the config to be honest.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Any, get_type_hints


def to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def to_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        pass
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def to_enum(value: Any, annotation: Any, default: Any) -> Any:
    """Match an enum member by value or name, case-insensitively."""
    if isinstance(value, annotation):
        return value
    if value is None:
        return default
    wanted = str(value).strip().lower()
    for member in annotation:  # type: ignore[attr-defined]
        if str(member.value).lower() == wanted or member.name.lower() == wanted:
            return member
    return default


def coerce_value(annotation: Any, value: Any, default: Any = None) -> Any:
    """Coerce ``value`` to ``annotation``; leave anything exotic alone."""
    try:
        if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
            return to_enum(value, annotation, default)
        if annotation is bool:
            return to_bool(value, bool(default))
        if annotation is int:
            return to_int(value, int(default or 0))
        if annotation is float:
            return to_float(value, float(default or 0.0))
        if annotation is str:
            return default if value is None else str(value)
    except Exception:
        return value
    return value


def coerce_into(obj: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a mapping's values against a dataclass's declared field types.

    Unknown keys are dropped: a typo like ``[fs] max_bytes = 5`` should surface
    as an ignored key, not silently retarget a different field.
    """
    cls = obj if isinstance(obj, type) else type(obj)
    try:
        hints = get_type_hints(cls)
    except Exception:
        hints = {}
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name not in raw:
            continue
        default = None if f.default is dataclasses.MISSING else f.default
        out[f.name] = coerce_value(hints.get(f.name), raw[f.name], default)
    return out
