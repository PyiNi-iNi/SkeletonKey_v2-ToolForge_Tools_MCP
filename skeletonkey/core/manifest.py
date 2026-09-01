"""ToolManifest - the unit of registration for everything the agent can call.

A manifest is more than a name + schema. It carries the metadata an autonomous
loop needs to *decide*: what capability it satisfies, what it risks, what it
requires of the host, and how expensive it is. That is what lets the registry
gate and rank tools adaptively instead of dumping 200 endpoints on the model.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import E, SkeletonKeyError
from .validate import check_schema

Risk = Literal["none", "read", "write", "destructive", "network", "privileged"]
RISK_ORDER = {r: i for i, r in enumerate(
    ["none", "read", "write", "destructive", "network", "privileged"])}

NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")   # a.b_c-d
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")


@dataclass
class Requirement:
    """A precondition checked against the CapabilityProfile at advertise time."""

    kind: Literal["binary", "python", "env", "shell", "capability", "os", "not_os", "filesystem"]
    name: str
    min_version: str | None = None
    optional: bool = False

    def key(self) -> str:
        return f"{self.kind}:{self.name}"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "name": self.name}
        if self.min_version:
            d["min_version"] = self.min_version
        if self.optional:
            d["optional"] = True
        return d


@dataclass
class ToolManifest:
    # identity
    id: str                                  # "fs.read" - globally unique, stable
    version: str = "1"
    title: str = ""
    description: str = ""                    # agent-facing; first sentence = what it does
    group: str = ""                          # "fs" | "shell" | "registry" | skill name

    # contract
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    output_schema: dict[str, Any] | None = None

    # secret args: keys of `args` that must never be persisted or logged in
    # clear text. The engine redacts exactly these in ledger rows; tool
    # handlers additionally must not echo them in results (the manifest
    # declaration is the backstop for the audit trail, not a license to print).
    secret_args: list[str] = field(default_factory=list)

    # adaptive metadata
    capability: str = ""                     # "search.text" - what need it fills
    provides: list[str] = field(default_factory=list)   # capabilities this can satisfy
    provider: str | None = None              # backend identity ("ripgrep", "python")
    priority: int = 50                       # higher wins when several providers exist
    requirements: list[Requirement] = field(default_factory=list)   # all must hold (AND)
    require_any: list[Requirement] = field(default_factory=list)    # at least one (OR)
    platforms: list[str] = field(default_factory=list)  # ["windows","posix"]; [] = any
    tags: list[str] = field(default_factory=list)

    # risk / scheduling
    risk: Risk = "read"
    idempotent: bool = True
    open_world: bool = False                 # touches anything outside fs/exec scope
    parallel_safe: bool = True               # may be run concurrently by the planner
    destructive: bool = False
    reversible: bool = False                 # undo available via journal
    typical_latency_ms: int = 50             # planner cost hint
    typical_output_bytes: int = 2_000
    timeout_s: float = 60.0

    # statefulness (matters for autopilot loops that assume continuity)
    stateful: Literal["none", "session", "host"] = "none"
    session_scope: str | None = None         # e.g. "shell", "fs"

    # exposure
    advertised: bool = True                  # False = engine-only, not listed to hosts
    hidden_reason: str = ""
    approval: Literal["never", "on_write", "always", "policy"] = "policy"
    # discovery tier (P5a): "core" is advertised in every tier, "task" in task+full,
    # "full" only in full. The host-facing default set is "full", so a host that never
    # expands sees everything as before; the autopilot opts into smaller surfaces.
    tier: Literal["core", "task", "full"] = "full"

    # docs for the model
    examples: list[dict[str, Any]] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    see_also: list[str] = field(default_factory=list)

    # provenance
    source: str = "builtin"                  # builtin | dropin | entrypoint | mcp:<server> | skill
    source_path: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    # runtime
    handler: Callable[..., Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.id or not _ID_RE.match(self.id):
            raise SkeletonKeyError(E.BAD_ARGS, f"invalid tool id {self.id!r}",
                                   details={"pattern": _ID_RE.pattern})
        if not self.title:
            self.title = self.id.replace(".", " ").replace("_", " ").title()
        if self.risk not in RISK_ORDER:
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown risk class {self.risk!r}")
        if self.tier not in ("core", "task", "full"):
            raise SkeletonKeyError(E.BAD_ARGS, f"unknown discovery tier {self.tier!r}",
                                   details={"tier": self.tier, "tiers": ["core", "task", "full"],
                                            "hint": "core = every tier, task = task + full, full = full only"})
        if problems := check_schema(self.input_schema):
            raise SkeletonKeyError(
                E.BAD_ARGS,
                f"tool {self.id}: input_schema is not valid for this toolkit",
                details={"problems": problems[:12]},
            )
        if self.output_schema and (problems := check_schema(self.output_schema)):
                raise SkeletonKeyError(E.BAD_ARGS, f"tool {self.id}: output_schema invalid",
                                       details={"problems": problems[:12]})
        if not self.group:
            self.group = self.id.split(".", 1)[0]
        if not self.capability:
            self.capability = self.id

    # ---------------------------------------------------------------- derived
    @property
    def name(self) -> str:
        """MCP-safe name (dots are legal but some hosts dislike them)."""
        return self.id.replace("/", ".")

    @property
    def mcp_name(self) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]", "_", self.id)

    @property
    def is_mutating(self) -> bool:
        return self.risk in ("write", "destructive", "privileged") or self.destructive

    def risk_at_least(self, level: str) -> bool:
        return RISK_ORDER[self.risk] >= RISK_ORDER.get(level, 99)

    def tokens_estimate(self) -> int:
        from .util import estimate_tokens
        return estimate_tokens(self.compact_doc())

    def effective_timeout(self, requested: float | None) -> float:
        cap = float(self.timeout_s)
        if requested is None:
            return cap
        return max(0.1, min(float(requested), cap))

    # ------------------------------------------------------------ renderings
    def input_schema_for_host(self) -> dict[str, Any]:
        """JSON Schema as advertised over MCP (no internal-only keywords)."""
        return {
            "type": "object",
            **{k: v for k, v in self.input_schema.items() if k in
               ("properties", "required", "additionalProperties", "$defs", "anyOf", "oneOf", "description")},
        }

    def to_dict(self, *, include_schema: bool = True, include_handler: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id, "version": self.version, "title": self.title, "description": self.description,
            "group": self.group, "capability": self.capability, "risk": self.risk,
            "tags": self.tags, "source": self.source,
            "idempotent": self.idempotent, "parallel_safe": self.parallel_safe,
            "destructive": self.destructive, "reversible": self.reversible,
            "stateful": self.stateful, "advertised": self.advertised,
            "tier": self.tier,
            "typical_latency_ms": self.typical_latency_ms, "typical_output_bytes": self.typical_output_bytes,
        }
        if self.provider:
            out["provider"] = self.provider
        if self.platforms:
            out["platforms"] = self.platforms
        if self.requirements:
            out["requires"] = [r.to_dict() for r in self.requirements]
        if self.require_any:
            out["requires_any"] = [r.to_dict() for r in self.require_any]
        if self.see_also:
            out["see_also"] = self.see_also
        if self.anti_patterns:
            out["anti_patterns"] = self.anti_patterns
        if include_schema:
            out["input_schema"] = self.input_schema
            if self.output_schema:
                out["output_schema"] = self.output_schema
        if include_handler and self.handler is not None:
            out["handler"] = f"{getattr(self.handler, '__module__', '?')}.{getattr(self.handler, '__name__', '?')}"
        if self.meta:
            out["meta"] = self.meta
        return out

    def compact_doc(self) -> str:
        """One-line, budget-aware description for `registry.search` output."""
        bits = [f"{self.id} [{self.risk}]", self.description.strip().split("\n")[0]]
        if self.anti_patterns:
            bits.append(f"avoid: {self.anti_patterns[0]}")
        return " - ".join(b for b in bits if b)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, handler: Callable[..., Any] | None = None,
                  source: str | None = None, source_path: str | None = None) -> ToolManifest:
        allowed = {f for f in cls.__dataclass_fields__ if f != "handler"}
        unknown = set(raw) - allowed - {"requires", "name", "inputSchema", "outputSchema"}
        data = dict(raw)
        manifest_notes: list[str] = []
        data.pop("name", None)
        # constructor args, but also legal keys inside a manifest file: without this
        # a `source = "skill:x"` line raises "multiple values for keyword argument"
        # and the whole tool silently fails to register.
        reserved: dict[str, Any] = {}
        for _k in ("source", "source_path", "handler"):
            if _k in data:
                reserved[_k] = data.pop(_k)
        if "inputSchema" in data:
            data["input_schema"] = data.pop("inputSchema")
        if "outputSchema" in data:
            data["output_schema"] = data.pop("outputSchema")
        reqs = []
        for r in data.pop("requires", []) or []:
            if isinstance(r, str):
                reqs.append(Requirement("binary", r))
            else:
                reqs.append(Requirement(**{k: v for k, v in r.items() if k in {"kind", "name", "min_version",
                                                                                "optional"}}))
        data["requirements"] = reqs
        any_reqs = []
        for r in data.pop("requires_any", []) or []:
            if isinstance(r, str):
                any_reqs.append(Requirement("capability", r))
            else:
                any_reqs.append(Requirement(**r))
        if any_reqs:
            data["require_any"] = any_reqs
        clean = {k: v for k, v in data.items() if k in allowed}
        # TOML's natural way to write a schema is a multi-line JSON string; accept it
        # rather than forcing every drop-in into deep inline-table nesting.
        for key in ("input_schema", "output_schema"):
            if isinstance(clean.get(key), str):
                import json

                try:
                    parsed = json.loads(clean[key])
                    clean[key] = parsed if isinstance(parsed, dict) else {}
                    if not isinstance(parsed, dict):
                        manifest_notes.append(f"{key} is JSON but not an object; ignored")
                except ValueError as exc:
                    clean[key] = {"type": "object", "properties": {}}
                    manifest_notes.append(f"{key} is not valid JSON ({exc}); using an open schema")
        manifest_notes = sorted(set(manifest_notes))
        manifest = cls(**clean, handler=handler or reserved.get("handler"),
                       source=source or reserved.get("source") or "dropin",
                       source_path=source_path or reserved.get("source_path"))
        if manifest_notes:
            manifest.meta = {**(manifest.meta or {}), "parse_notes": manifest_notes}
        manifest.meta = {**(manifest.meta or {}), **({"unknown_keys": sorted(unknown)} if unknown else {})}
        return manifest


def merge_manifests(base: ToolManifest, override: dict[str, Any]) -> ToolManifest:
    """Skill/drop-in manifests layer over builtins (e.g. change risk, add docs)."""
    from dataclasses import replace

    clean = {k: v for k, v in override.items() if k in ToolManifest.__dataclass_fields__ and k != "handler"}
    if "requires" in override:
        clean["requirements"] = [
            r if isinstance(r, Requirement) else Requirement(**r) for r in override["requires"]
        ]
    merged = replace(base, **clean)
    merged.__post_init__()
    return merged
