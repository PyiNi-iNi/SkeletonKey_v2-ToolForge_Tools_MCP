"""Tool Catalog Engine for SkeletonKey.

Auto-indexing, persistent tool inventory with mutation API, diff/compare,
and generator hooks. Integrates with the toolkit build lifecycle.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import E, SkeletonKeyError
from .manifest import ToolManifest
from ..version import __version__

CATALOG_SCHEMA_VERSION = "1.0"
CATALOG_FILENAME = "catalog.json"


@dataclass
class ToolEntry:
    """A single tool entry in the catalog."""
    id: str
    title: str
    description: str
    risk: str
    tier: str
    capability: str
    group: str | None = None
    provider: str | None = None
    tags: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    secret_args: list[str] = field(default_factory=list)
    typical_latency_ms: int = 0
    source: str = "builtin"
    integration: str | None = None
    integration_version: str | None = None
    integration_type: str | None = None
    registered_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_manifest(cls, man: ToolManifest, integration_name: str | None = None) -> "ToolEntry":
        now = datetime.utcnow().isoformat() + "Z"
        return cls(
            id=man.id,
            title=man.title,
            description=man.description,
            risk=man.risk,
            tier=man.tier,
            capability=man.capability,
            group=man.group,
            provider=man.provider,
            tags=list(man.tags),
            input_schema=man.input_schema,
            secret_args=list(man.secret_args),
            typical_latency_ms=man.typical_latency_ms,
            source=man.source or "builtin",
            integration=integration_name or man.group,
            integration_version=man.metadata.get("integration_version") if hasattr(man, "metadata") else None,
            integration_type=man.metadata.get("integration_type") if hasattr(man, "metadata") else None,
            registered_at=now,
        )


@dataclass
class IntegrationSummary:
    """Summary of an integration's tools in the catalog."""
    name: str
    version: str
    type: str
    category: str
    platform: str | None
    tool_count: int
    tool_ids: list[str]
    enabled: bool
    loaded: bool
    health: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectSummary:
    """Summary of a discovered project."""
    name: str
    path: str
    type: str
    tool_count: int
    enabled: bool
    health: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolCatalog:
    """Complete tool catalog snapshot."""
    schema_version: str = CATALOG_SCHEMA_VERSION
    generated_at: str = ""
    toolkit_version: str = __version__
    tools: dict[str, ToolEntry] = field(default_factory=dict)
    integrations: dict[str, IntegrationSummary] = field(default_factory=dict)
    projects: dict[str, ProjectSummary] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = datetime.utcnow().isoformat() + "Z"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "toolkit_version": self.toolkit_version,
            "tools": {k: v.to_dict() for k, v in self.tools.items()},
            "integrations": {k: v.to_dict() for k, v in self.integrations.items()},
            "projects": {k: v.to_dict() for k, v in self.projects.items()},
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCatalog":
        tools = {k: ToolEntry(**v) for k, v in data.get("tools", {}).items()}
        integrations = {k: IntegrationSummary(**v) for k, v in data.get("integrations", {}).items()}
        projects = {k: ProjectSummary(**v) for k, v in data.get("projects", {}).items()}
        return cls(
            schema_version=data.get("schema_version", CATALOG_SCHEMA_VERSION),
            generated_at=data.get("generated_at", ""),
            toolkit_version=data.get("toolkit_version", __version__),
            tools=tools,
            integrations=integrations,
            projects=projects,
            metadata=data.get("metadata", {}),
        )

    def get_tool(self, tool_id: str) -> ToolEntry | None:
        return self.tools.get(tool_id)

    def get_tools_by_risk(self, risk: str) -> list[ToolEntry]:
        return [t for t in self.tools.values() if t.risk == risk]

    def get_tools_by_tier(self, tier: str) -> list[ToolEntry]:
        return [t for t in self.tools.values() if t.tier == tier]

    def get_tools_by_integration(self, integration: str) -> list[ToolEntry]:
        return [t for t in self.tools.values() if t.integration == integration]

    def get_tools_by_tag(self, tag: str) -> list[ToolEntry]:
        return [t for t in self.tools.values() if tag in t.tags]

    def get_risk_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for t in self.tools.values():
            dist[t.risk] = dist.get(t.risk, 0) + 1
        return dist

    def get_tier_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for t in self.tools.values():
            dist[t.tier] = dist.get(t.tier, 0) + 1
        return dist

    def get_integration_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for t in self.tools.values():
            key = t.integration or "builtin"
            dist[key] = dist.get(key, 0) + 1
        return dist


class CatalogManager:
    """Manages the tool catalog lifecycle."""

    def __init__(self, state_dir: str, toolkit: Any = None):
        self.state_dir = Path(state_dir)
        self.catalog_path = self.state_dir / CATALOG_FILENAME
        self._catalog: ToolCatalog | None = None
        self._toolkit = toolkit
        self._dirty = False

    @property
    def catalog(self) -> ToolCatalog:
        if self._catalog is None:
            self._catalog = self.load()
        return self._catalog

    def load(self) -> ToolCatalog:
        """Load catalog from disk."""
        if self.catalog_path.exists():
            try:
                with open(self.catalog_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return ToolCatalog.from_dict(data)
            except (json.JSONDecodeError, OSError) as e:
                # Corrupted or missing - start fresh
                pass
        return ToolCatalog()

    def save(self) -> bool:
        """Persist catalog to disk."""
        if self._catalog is None:
            return False
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with open(self.catalog_path, "w", encoding="utf-8") as f:
                json.dump(self._catalog.to_dict(), f, indent=2)
            self._dirty = False
            return True
        except OSError:
            return False

    def auto_index(self, toolkit: Any) -> ToolCatalog:
        """Build catalog from current toolkit state."""
        engine = toolkit.engine
        registry = engine.registry
        integrations_mgr = getattr(toolkit, "integrations", None)
        projects_mgr = getattr(toolkit, "projects", None)

        # Advertised tools (respects tier)
        advertised = registry.advertise(tier="full", read_only=False)

        catalog = ToolCatalog(
            toolkit_version=__version__,
            metadata={
                "tier": advertised.tier,
                "digest": advertised.digest,
                "tokens": advertised.tokens,
                "budget_drops": advertised.budget_drops,
            },
        )

        # Index all advertised tools
        for man in advertised.tools:
            # Determine integration from tool metadata
            integration_name = man.group if man.group != "builtin" else None
            entry = ToolEntry.from_manifest(man, integration_name)
            catalog.tools[man.id] = entry

        # Index integrations
        if integrations_mgr:
            for name, inst in integrations_mgr._instances.items():
                info = inst.info
                catalog.integrations[name] = IntegrationSummary(
                    name=info.name,
                    version=info.version,
                    type=info.integration_type.value,
                    category=info.category.value,
                    platform=info.platform.value if info.platform else None,
                    tool_count=len(inst.instance.get_tools()),
                    tool_ids=[t.get("id", "") for t in inst.instance.get_tools()],
                    enabled=name in integrations_mgr._enabled_integrations,
                    loaded=name in integrations_mgr._instances,
                    health=inst.health,
                )

        # Index projects
        if projects_mgr:
            for proj in projects_mgr.list_projects():
                catalog.projects[proj.name] = ProjectSummary(
                    name=proj.name,
                    path=proj.path,
                    type=proj.type,
                    tool_count=len(proj.tools),
                    enabled=proj.enabled,
                    health=proj.health,
                )

        self._catalog = catalog
        self._dirty = True
        return catalog

    def mark_dirty(self):
        self._dirty = True

    def add_tool(self, entry: ToolEntry) -> bool:
        """Add or update a tool entry."""
        if self._catalog is None:
            self._catalog = self.load()
        self._catalog.tools[entry.id] = entry
        self._dirty = True
        return True

    def remove_tool(self, tool_id: str) -> bool:
        """Remove a tool entry."""
        if self._catalog is None:
            return False
        if tool_id in self._catalog.tools:
            del self._catalog.tools[tool_id]
            self._dirty = True
            return True
        return False

    def update_tool(self, tool_id: str, **kwargs) -> bool:
        """Update fields of a tool entry."""
        if self._catalog is None or tool_id not in self._catalog.tools:
            return False
        entry = self._catalog.tools[tool_id]
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        self._dirty = True
        return True

    def get_statistics(self) -> dict[str, Any]:
        """Get catalog statistics."""
        cat = self.catalog
        return {
            "total_tools": len(cat.tools),
            "total_integrations": len(cat.integrations),
            "total_projects": len(cat.projects),
            "by_risk": cat.get_risk_distribution(),
            "by_tier": cat.get_tier_distribution(),
            "by_integration": cat.get_integration_distribution(),
            "generated_at": cat.generated_at,
            "toolkit_version": cat.toolkit_version,
            "dirty": self._dirty,
        }

    def get_catalog(self) -> ToolCatalog:
        return self.catalog


# Global manager instance
_catalog_manager: CatalogManager | None = None


def get_catalog_manager(state_dir: str, toolkit: Any = None) -> CatalogManager:
    global _catalog_manager
    if _catalog_manager is None:
        _catalog_manager = CatalogManager(state_dir, toolkit)
    return _catalog_manager


def create_catalog_from_toolkit(toolkit: Any) -> ToolCatalog:
    """Convenience function to create catalog from toolkit."""
    mgr = CatalogManager(toolkit.config.state.dir)
    return mgr.auto_index(toolkit)


def load_catalog(state_dir: str) -> ToolCatalog:
    """Load existing catalog from state dir."""
    mgr = CatalogManager(state_dir)
    return mgr.load()


def save_catalog(catalog: ToolCatalog, state_dir: str) -> bool:
    """Save catalog to state dir."""
    mgr = CatalogManager(state_dir)
    mgr._catalog = catalog
    return mgr.save()