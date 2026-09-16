"""Environment configuration for features.

All feature settings are read from environment variables with the ``SK_``
prefix (e.g. ``SK_DB_BACKEND=faiss``). Values are typed at read time;
no global state is mutated at import, so tests can monkeypatch freely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PREFIX",
    "env_str",
    "env_bool",
    "env_int",
    "env_float",
    "env_list",
    "FeatureConfig",
]

PREFIX = "SK_"


def env_str(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val


def env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "y"}


def env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val in (None, ""):
        return default
    try:
        return int(val)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val in (None, ""):
        return default
    try:
        return float(val)
    except ValueError:
        return default


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    val = os.environ.get(name)
    if val in (None, ""):
        return list(default) if default is not None else []
    return [item.strip() for item in val.split(",") if item.strip()]


@dataclass
class FeatureConfig:
    """Typed settings bag for one feature.

    ``get`` resolves in this order: explicit override dict > environment
    (with the feature's prefix) > the dataclass default.
    """

    prefix: str = PREFIX
    overrides: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.prefix.endswith("_"):
            self.prefix = self.prefix + "_"

    def _env_name(self, key: str) -> str:
        return self.prefix + key.upper()

    def get_str(self, key: str, default: str | None = None) -> str | None:
        if key in self.overrides:
            return str(self.overrides[key])
        return env_str(self._env_name(key), default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        if key in self.overrides:
            return bool(self.overrides[key])
        return env_bool(self._env_name(key), default)

    def get_int(self, key: str, default: int) -> int:
        if key in self.overrides:
            return int(self.overrides[key])
        return env_int(self._env_name(key), default)

    def get_float(self, key: str, default: float) -> float:
        if key in self.overrides:
            return float(self.overrides[key])
        return env_float(self._env_name(key), default)

    def get_list(self, key: str, default: list[str] | None = None) -> list[str]:
        if key in self.overrides:
            value = self.overrides[key]
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
            return [str(value)]
        return env_list(self._env_name(key), default)

    def as_dict(self, keys: list[str]) -> dict[str, Any]:
        """Snapshot of the given keys with their resolved values."""
        return {k: getattr(self, f"get_{k.split(':')[0]}")(k.split(":")[1]) for k in keys}
