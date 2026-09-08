"""Shared helpers for the P5.3 loader and P5.5 validation tests.

Builds a *disposable* integration: a real file on disk containing a real
``Integration`` subclass (one read-only tool, ``<name>.echo``), plus register /
cleanup callables. The dynamic load path (``load_from_path`` /
``load_from_module`` / discovery) and the static validation both operate on
files and on the global integration registry, so every handle removes its
registry entry and its ``sys.modules`` entry on cleanup - the tests exercise
the real path without polluting the rest of the suite.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from skeletonkey.integrations import (
    Integration,
    IntegrationInfo,
    registry,
)

_counter = 0

_MODULE_TEMPLATE = '''\
from skeletonkey.integrations import (
    Integration,
    IntegrationCategory,
    IntegrationDependency,
    IntegrationInfo,
    IntegrationType,
)

{extra_source}
class {class_name}(Integration):
    INFO = IntegrationInfo(
        name={name!r},
        title="Temp {{name}}",
        version="1.2.3",
        description="A disposable integration for loader and validation tests.",
        integration_type=IntegrationType.SDK,
        category=IntegrationCategory.DEVTOOLS,
        dependencies=[
{dep_lines}        ],
        conflicts={conflicts!r},
        metadata={metadata!r},
    )

    def initialize(self) -> bool:
        return True

    def cleanup(self) -> None:
        pass

    def get_tools(self) -> list:
        return [{{
            "id": f"{{self.name}}.echo",
            "title": "Echo",
            "description": "Echo text back.",
            "risk": "none",
            "tier": "core",
            "input_schema": {{
                "type": "object",
                "properties": {{"text": {{"type": "string"}}}},
                "required": ["text"],
                "additionalProperties": False,
            }},
        }}]

    def tool_echo(self, text: str) -> dict:
        return {{"echo": text, "version": self.version}}
'''


def _next_id() -> int:
    global _counter
    _counter += 1
    return _counter


def make_temp_integration(
    tmp_path: Path,
    *,
    name: str | None = None,
    deps: tuple[tuple[str, bool], ...] = (),
    conflicts: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
    extra_source: str = "",
) -> dict[str, Any]:
    """Write a single-file integration module into ``tmp_path``.

    Args:
        tmp_path: Destination directory (must contain only this test's files -
            discovery scans it).
        name: Kebab-case integration name (unique by default).
        deps: ``(package_name, optional)`` pairs rendered into
            ``IntegrationDependency`` entries.
        conflicts: Integration names this one conflicts with.
        metadata: Extra ``IntegrationInfo.metadata`` (e.g. ``min_engine``).
        extra_source: Verbatim lines prepended to the module body (e.g. a
            ``SECRET = "..."`` literal for the security-scan tests).

    Returns a handle: ``name``, ``stem`` (module name), ``file``,
    ``register()`` (import the file, register the class, return it) and
    ``cleanup()`` (drop the registry entry and the ``sys.modules`` entry).
    """
    n = _next_id()
    if name is None:
        name = f"tmp-int-{n}"
    stem = f"tmp_int_mod_{n}"
    file_path = tmp_path / f"{stem}.py"
    dep_lines = "\n".join(
        f'            IntegrationDependency(name="{dep}", optional={bool(opt)}),'
        for dep, opt in deps
    )
    file_path.write_text(
        _MODULE_TEMPLATE.format(
            class_name=f"TmpIntMod{n}",
            name=name,
            dep_lines=dep_lines,
            conflicts=list(conflicts),
            metadata=dict(metadata) if metadata is not None else {},
            extra_source=extra_source,
        ),
        encoding="utf-8",
    )

    def _register() -> type[Integration]:
        sys.modules.pop(stem, None)
        spec = importlib.util.spec_from_file_location(stem, str(file_path))
        assert spec and spec.loader, f"no importable spec for {file_path}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[stem] = module
        spec.loader.exec_module(module)
        cls = getattr(module, f"TmpIntMod{n}")
        info = getattr(cls, "INFO", None)
        assert isinstance(info, IntegrationInfo) and info.name == name
        if not registry.has_integration(name):
            registry.register_integration(cls, info)
        return cls

    def _cleanup() -> None:
        registry._integrations.pop(name, None)
        sys.modules.pop(stem, None)

    return {
        "name": name,
        "stem": stem,
        "file": file_path,
        "register": _register,
        "cleanup": _cleanup,
    }
