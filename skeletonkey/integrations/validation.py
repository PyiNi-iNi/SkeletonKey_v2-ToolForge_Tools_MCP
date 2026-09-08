"""Integration testing & validation framework (P5.5).

Static, side-effect-free validation for every registered integration, so a
broken integration (bad metadata, unmappable tool ids, missing handlers,
invalid risk/tier, absent required deps) is caught before it reaches the
engine. This is the test harness an integration author and the CI run against:

    from skeletonkey.integrations.validation import validate_registry
    for report in validate_registry():
        if not report.valid:
            for issue in report.issues:
                print(issue.severity, issue.code, issue.message)

Checks (each tagged with a stable ``code`` so automation can gate on it):

- ``metadata``      - name/version/title/description/license sanity
- ``tool_mapping``  - every ``get_tools()`` id maps to a callable ``tool_*``
- ``tool_schema``   - input schema shape, valid risk/tier, unique ids
- ``dependencies``  - required deps importable, optional deps reported
- ``security``      - mutating tools are backed by a capability declaration
- ``secrets``       - static scan of the integration's own module source for
                      secret-looking literals (reuses the redaction table;
                      only the unambiguous provider-key patterns, warnings)
- ``compatibility`` - ``metadata["min_engine"]`` (semver) satisfiable by this
                      engine version (numeric-core comparison)
- ``health``        - (opt-in) ``check_health`` returns a sane tuple

Plus ``benchmark_integration`` (get_tools latency) and ``dependency_status``.
The module is intentionally dependency-free: it reasons over the already
imported registry and never imports the integrations' third-party SDKs.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.redact import redact_text
from ..version import __version__
from . import IntegrationConfig, registry
from .bridge import tool_suffix

_VALID_RISKS = {"none", "read", "write", "destructive", "network", "privileged"}
_VALID_TIERS = {"core", "task", "full"}
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-.][0-9A-Za-z-]+)?$")

# The redaction table (core/redact.py) is tuned for *output* text, where a line
# like `password = ...` in a transcript is a leak. In *source* code those
# context-dependent patterns (kv_secret, bearer, conn_str, url_creds, cli_flag,
# auth_header, azure) fire on ordinary assignments and f-strings, so the source
# scan keeps only the labels whose pattern is an unambiguous provider
# credential literal anywhere it appears: a leak wherever it is in source.
_SOURCE_SCAN_LABELS = {
    "aws_key", "gh_token", "github_pat", "slack", "discord", "jwt",
    "stripe", "anthropic", "openai", "google", "hf", "pem",
}


@dataclass
class ValidationIssue:
    """A single finding from the validation framework."""

    code: str
    severity: str  # "error" | "warning"
    message: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        out = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class ValidationReport:
    """Result of validating one integration."""

    name: str
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "valid": self.valid,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
            "checks": self.checks,
        }


def validate_integration(name: str, *, include_health: bool = False) -> ValidationReport:
    """Validate one registered integration by name."""
    info = registry.get_integration_info(name)
    if info is None:
        report = ValidationReport(name=name, valid=False)
        report.issues.append(
            ValidationIssue("not_registered", "error", f"Integration {name!r} is not registered")
        )
        return report

    cls = registry.get_integration_class(name)
    report = ValidationReport(name=name, valid=True, checks={})
    if cls is None:
        report.valid = False
        report.issues.append(
            ValidationIssue("not_registered", "error", f"Integration class for {name!r} missing")
        )
        return report

    # ------------------------------------------------------------------ metadata
    _check_metadata(report, info)

    # ------------------------------------------------------------------ tools
    instance = cls(IntegrationConfig(name=name, integration_id=name))
    try:
        tools = instance.get_tools()
    except Exception as exc:  # a broken get_tools must not crash the harness
        report.valid = False
        report.issues.append(
            ValidationIssue(
                "tool_mapping", "error", "get_tools() raised", detail=f"{type(exc).__name__}: {exc}"
            )
        )
        tools = []
    _check_tools(report, instance, name, tools)

    # ------------------------------------------------------------------ dependencies
    _check_dependencies(report, info)

    # ------------------------------------------------------------------ security
    _check_security(report, info, tools)
    secret_findings = _check_secrets(report, cls)

    # ------------------------------------------------------------------ compatibility
    _check_compatibility(report, info)

    # ------------------------------------------------------------------ health (opt-in)
    if include_health:
        _check_health(report, instance)

    report.checks = {
        "type": info.integration_type.value,
        "category": info.category.value,
        "version": info.version,
        "tools": len(tools),
        "dependencies": _dependency_status(info),
        "secrets": secret_findings,
        "min_engine": (getattr(info, "metadata", None) or {}).get("min_engine"),
    }
    return report


def validate_registry() -> list[ValidationReport]:
    """Validate every registered integration. Deterministic order by name."""
    names = sorted(info.name for info in registry.list_integrations())
    return [validate_integration(name) for name in names]


def dependency_status(info: Any) -> dict[str, Any]:
    """Classify each declared dependency as present/absent, required/optional."""
    return _dependency_status(info)


def benchmark_integration(name: str, *, iterations: int = 100) -> dict[str, Any]:
    """Time ``get_tools()`` on a fresh instance (side-effect free)."""
    cls = registry.get_integration_class(name)
    if cls is None:
        return {"name": name, "error": "not registered"}
    instance = cls(IntegrationConfig(name=name, integration_id=name))
    # Warm-up call (first call may trigger lazy imports/caching).
    instance.get_tools()
    start = time.perf_counter()
    for _ in range(iterations):
        instance.get_tools()
    elapsed = (time.perf_counter() - start) / iterations * 1_000_000
    return {
        "name": name,
        "iterations": iterations,
        "get_tools_mean_us": round(elapsed, 1),
        "tool_count": len(instance.get_tools()),
    }


# --------------------------------------------------------------------------- checks
def _check_metadata(report: ValidationReport, info: Any) -> None:
    if not _NAME_RE.match(info.name):
        report.valid = False
        report.issues.append(
            ValidationIssue(
                "metadata", "error", "integration name must be kebab-case",
                detail=f"got {info.name!r}",
            )
        )
    if not _SEMVER_RE.match(info.version):
        report.valid = False
        report.issues.append(
            ValidationIssue(
                "metadata", "error", "version must be semver", detail=f"got {info.version!r}"
            )
        )
    if not info.title:
        report.issues.append(ValidationIssue("metadata", "warning", "title is empty"))
    if not info.description:
        report.issues.append(ValidationIssue("metadata", "warning", "description is empty"))
    if not info.license:
        report.issues.append(ValidationIssue("metadata", "warning", "license is empty"))


def _check_tools(report: ValidationReport, instance: Any, name: str, tools: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    risks: set[str] = set()
    for tool in tools:
        tool_id = tool.get("id")
        if not tool_id or not isinstance(tool_id, str):
            report.valid = False
            report.issues.append(
                ValidationIssue("tool_schema", "error", "tool is missing a string 'id'")
            )
            continue
        if tool_id in seen:
            report.valid = False
            report.issues.append(
                ValidationIssue("tool_schema", "error", f"duplicate tool id {tool_id!r}")
            )
        seen.add(tool_id)

        suffix = tool_suffix(name, tool_id)
        if suffix is None or not callable(getattr(instance, f"tool_{suffix}", None)):
            report.valid = False
            report.issues.append(
                ValidationIssue(
                    "tool_mapping",
                    "error",
                    f"tool {tool_id!r} has no handler",
                    detail=f"expected method tool_{suffix if suffix else '<?>'}()",
                )
            )

        schema = tool.get("input_schema") or tool.get("parameters")
        if schema is None or not isinstance(schema, dict):
            report.issues.append(
                ValidationIssue(
                    "tool_schema", "warning", f"tool {tool_id!r} has no input schema"
                )
            )
        if not tool.get("description"):
            report.issues.append(
                ValidationIssue("tool_schema", "warning", f"tool {tool_id!r} has no description")
            )

        risk = tool.get("risk")
        if risk not in _VALID_RISKS:
            report.valid = False
            report.issues.append(
                ValidationIssue(
                    "tool_schema", "error", f"tool {tool_id!r} has invalid risk", detail=repr(risk)
                )
            )
        else:
            risks.add(risk)
        if tool.get("tier") not in _VALID_TIERS:
            report.valid = False
            report.issues.append(
                ValidationIssue(
                    "tool_schema",
                    "error",
                    f"tool {tool_id!r} has invalid tier",
                    detail=repr(tool.get("tier")),
                )
            )
    report.checks.setdefault("risks", sorted(risks))


def _check_dependencies(report: ValidationReport, info: Any) -> None:
    for dep in getattr(info, "dependencies", []):
        name = getattr(dep, "name", "")
        optional = bool(getattr(dep, "optional", False))
        if not name:
            continue
        present = importlib.util.find_spec(name) is not None
        if not optional and not present:
            report.valid = False
            report.issues.append(
                ValidationIssue(
                    "dependencies",
                    "error",
                    f"required dependency {name!r} is not importable",
                    detail=f"install with: pip install {name}",
                )
            )


def _check_security(report: ValidationReport, info: Any, tools: list[dict[str, Any]]) -> None:
    risks = {t.get("risk") for t in tools if t.get("risk") in _VALID_RISKS}
    if risks & {"write", "destructive"}:
        capabilities = [getattr(c, "name", "") for c in getattr(info, "capabilities", [])]
        if not capabilities:
            report.issues.append(
                ValidationIssue(
                    "security",
                    "warning",
                    "mutating tools but no capability declaration",
                    detail="declare IntegrationCapability entries for write/destructive access",
                )
            )


def _check_health(report: ValidationReport, instance: Any) -> None:
    try:
        health = instance.check_health()
    except Exception as exc:  # check_health must never raise
        report.valid = False
        report.issues.append(
            ValidationIssue(
                "health", "error", "check_health() raised", detail=f"{type(exc).__name__}: {exc}"
            )
        )
        return
    if not (isinstance(health, tuple) and len(health) == 2 and isinstance(health[0], str)):
        report.valid = False
        report.issues.append(
            ValidationIssue("health", "error", "check_health() must return (status, message)")
        )
        return
    status = health[0]
    if status not in {"healthy", "degraded", "unhealthy", "unknown"}:
        report.issues.append(
            ValidationIssue("health", "warning", f"unusual health status {status!r}")
        )
    report.checks["health"] = {"status": status, "message": health[1]}


def _check_secrets(report: ValidationReport, cls: Any) -> int:
    """Static scan of the integration's own module source for secret literals.

    Reuses the redaction table (via redact_text) so the two cannot drift;
    provider-key labels apply to source (see _SOURCE_SCAN_LABELS). Findings are
    warnings - the harness is static and its job is to flag, and a CI gate can
    hard-fail on code ``secrets``. The report carries the label and line only:
    the matched material is never echoed into the report itself.
    """
    path = inspect.getsourcefile(cls)
    if not path or not isinstance(path, str):
        return 0
    try:
        source = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    findings = 0
    for lineno, line in enumerate(source.splitlines(), start=1):
        _redacted, labels = redact_text(line)
        for label in labels:
            if label not in _SOURCE_SCAN_LABELS:
                continue
            findings += 1
            report.issues.append(
                ValidationIssue(
                    "secrets",
                    "warning",
                    "possible secret literal in integration source",
                    detail=f"{Path(path).name}:{lineno} pattern={label} (value masked by design)",
                )
            )
    return findings


def _check_compatibility(report: ValidationReport, info: Any) -> None:
    """``metadata["min_engine"]`` (semver) must be satisfiable by this engine.

    Numeric-core comparison (prerelease tags are treated as their core
    version): good enough for a floor, not a full semver resolver.
    """
    min_engine = (getattr(info, "metadata", None) or {}).get("min_engine")
    if min_engine is None:
        return
    min_engine = str(min_engine)
    if not _SEMVER_RE.match(min_engine):
        report.issues.append(
            ValidationIssue(
                "compatibility", "warning", "min_engine is not semver",
                detail=repr(min_engine),
            )
        )
        return
    have = _version_core(__version__)
    want = _version_core(min_engine)
    if have < want:
        report.valid = False
        report.issues.append(
            ValidationIssue(
                "compatibility", "error",
                f"requires engine >= {min_engine}, this engine is {__version__}",
            )
        )


def _version_core(version: str) -> tuple[int, int, int]:
    parts = re.split(r"[-+]", version, maxsplit=1)[0].split(".")
    nums = [int(p) for p in parts if p.isdigit()]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _dependency_status(info: Any) -> dict[str, Any]:
    status: dict[str, Any] = {"required": {}, "optional": {}}
    for dep in getattr(info, "dependencies", []):
        name = getattr(dep, "name", "")
        if not name:
            continue
        present = importlib.util.find_spec(name) is not None
        bucket = "optional" if getattr(dep, "optional", False) else "required"
        status[bucket][name] = present
    return status
