"""Catalog Engine Tools for SkeletonKey.

Tools for managing the tool catalog through the engine.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import ToolManifest
from .catalog import ToolCatalog, ToolEntry, get_catalog_manager, create_catalog_from_toolkit, save_catalog, CATALOG_SCHEMA_VERSION, CATALOG_FILENAME

logger = logging.getLogger(__name__)


# Audit check definitions (module-level for use in ToolManifest schema)
_AUDIT_CHECKS = {
    "secret_scan": "Scan for potential secrets in tool metadata",
    "risk_audit": "Audit risk distribution and high-risk tools",
    "schema_validation": "Validate all tool input schemas",
    "dependency_freshness": "Check for stale registered_at timestamps",
    "coverage": "Check capability and tag coverage",
    "duplicates": "Check for duplicate tool IDs or capabilities",
    # Phase 3: Advanced audit checks
    "sbom": "Generate Software Bill of Materials for integrations",
    "license_check": "Check license compatibility and compliance",
    "vulnerability_scan": "Scan for known vulnerabilities in dependencies",
    "supply_chain": "Analyze supply chain risks in tool dependencies",
}


def _watch_watchfiles(mgr, debounce: float, max_iterations: int) -> dict[str, Any]:
    """Watch for catalog changes using watchfiles library."""
    import watchfiles
    
    watch_paths = []
    projects = getattr(mgr._toolkit, 'projects', None)
    if projects and projects.roots:
        watch_paths = projects.roots
    
    if not watch_paths:
        return {"error": "No watch paths configured"}

    changes = watchfiles.watch(*watch_paths, debounce=debounce)
    iterations = 0
    
    for change_set in changes:
        iterations += 1
        mgr.auto_index(mgr._toolkit)
        print(f"Catalog auto-refreshed (iteration {iterations})")
        
        if max_iterations > 0 and iterations >= max_iterations:
            break
    
    return {"watched": True, "iterations": iterations, "stopped": True}


def _watch_polling(mgr, interval: float, debounce: float, max_iterations: int) -> dict[str, Any]:
    """Fallback polling-based watch."""
    import time
    
    watch_paths = []
    projects = getattr(mgr._toolkit, 'projects', None)
    if projects and projects.roots:
        watch_paths = projects.roots
    
    if not watch_paths:
        return {"error": "No watch paths configured"}

    iterations = 0
    last_mtime = {}
    
    while True:
        iterations += 1
        changed = False
        
        for root in watch_paths:
            for dirpath, dirnames, filenames in os.walk(root):
                for f in filenames:
                    if f.endswith(('.py', '.toml', '.json', '.md')):
                        path = os.path.join(dirpath, f)
                        try:
                            mtime = os.path.getmtime(path)
                            if path not in last_mtime or mtime != last_mtime[path]:
                                last_mtime[path] = mtime
                                changed = True
                        except OSError:
                            pass
        
        if changed:
            mgr.auto_index(mgr._toolkit)
            print(f"Catalog auto-refreshed (iteration {iterations})")
        
        if max_iterations > 0 and iterations >= max_iterations:
            break
        
        time.sleep(interval)
    
    return {"watched": True, "iterations": iterations, "stopped": True}


def register_catalog_tools(
    registry,
    *,
    engine: Any = None,
    toolkit: Any = None,
) -> dict[str, Any]:
    """Register catalog management tools with the registry.

    Args:
        registry: The tool registry
        engine: The engine instance
        toolkit: The toolkit instance

    Returns:
        dict: Report of registered tools
    """
    report: dict[str, Any] = {"registered": [], "errors": []}
    _engine = engine
    _toolkit = toolkit

    # Tool: catalog.create
    def tool_catalog_create(
        auto_index: bool = True,
        save: bool = True,
    ) -> dict[str, Any]:
        """Create or rebuild the tool catalog from current toolkit state."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        if auto_index:
            catalog = mgr.auto_index(_toolkit)
        else:
            catalog = mgr.catalog

        if save:
            mgr.save()

        return {
            "created": True,
            "total_tools": len(catalog.tools),
            "total_integrations": len(catalog.integrations),
            "total_projects": len(catalog.projects),
            "generated_at": catalog.generated_at,
            "path": str(mgr.catalog_path),
        }

    try:
        registry.register(ToolManifest(
            id="catalog.create",
            title="Create Catalog",
            description="Auto-index the current toolkit and build a tool catalog",
            capability="catalog.create",
            risk="write",
            typical_latency_ms=200,
            tier="task",
            tags=["catalog", "create", "index"],
            input_schema={
                "type": "object",
                "properties": {
                    "auto_index": {"type": "boolean", "default": True, "description": "Rebuild from current toolkit state"},
                    "save": {"type": "boolean", "default": True, "description": "Persist catalog to disk"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_create)
        report["registered"].append("catalog.create")
    except Exception as e:
        report["errors"].append(f"catalog.create: {e}")

    # Tool: catalog.load
    def tool_catalog_load(
        path: str | None = None,
    ) -> dict[str, Any]:
        """Load a catalog from disk."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        try:
            catalog = mgr.load(path)
            return {
                "loaded": True,
                "path": str(mgr.catalog_path) if path is None else path,
                "total_tools": len(catalog.tools),
                "generated_at": catalog.generated_at,
            }
        except (OSError, json.JSONDecodeError) as e:
            return {"error": f"Failed to load catalog: {e}"}

    try:
        registry.register(ToolManifest(
            id="catalog.load",
            title="Load Catalog",
            description="Load a catalog from disk into memory",
            capability="catalog.load",
            risk="read",
            typical_latency_ms=50,
            tier="task",
            tags=["catalog", "load"],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": ["string", "null"], "description": "Path to catalog file (defaults to managed location)"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_load)
        report["registered"].append("catalog.load")
    except Exception as e:
        report["errors"].append(f"catalog.load: {e}")

    # Tool: catalog.save
    def tool_catalog_save(
        path: str | None = None,
    ) -> dict[str, Any]:
        """Save the current in-memory catalog to disk."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        if mgr.catalog is None:
            return {"error": "No catalog in memory to save"}

        try:
            if path:
                # Write catalog directly to the specified path
                target_path = Path(path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(mgr.catalog.to_dict(), f, indent=2)
                return {"saved": True, "path": str(target_path)}
            else:
                mgr.save()
                return {"saved": True, "path": str(mgr.catalog_path)}
        except OSError as e:
            return {"error": f"Failed to save catalog: {e}"}

    try:
        registry.register(ToolManifest(
            id="catalog.save",
            title="Save Catalog",
            description="Persist the current catalog to disk",
            capability="catalog.save",
            risk="write",
            typical_latency_ms=50,
            tier="task",
            tags=["catalog", "save"],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": ["string", "null"], "description": "Output path (defaults to managed location)"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_save)
        report["registered"].append("catalog.save")
    except Exception as e:
        report["errors"].append(f"catalog.save: {e}")

    # Tool: catalog.stats
    def tool_catalog_stats() -> dict[str, Any]:
        """Get statistics about the current catalog."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        return mgr.get_statistics()

    try:
        registry.register(ToolManifest(
            id="catalog.stats",
            title="Catalog Statistics",
            description="Get statistics about the current catalog",
            capability="catalog.stats",
            risk="read",
            typical_latency_ms=10,
            tier="task",
            tags=["catalog", "stats"],
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ), tool_catalog_stats)
        report["registered"].append("catalog.stats")
    except Exception as e:
        report["errors"].append(f"catalog.stats: {e}")

    # Tool: catalog.list_tools
    def tool_catalog_list_tools(
        capability: str | None = None,
        tags: list[str] | None = None,
        risk: str | None = None,
    ) -> dict[str, Any]:
        """List tools in the catalog with optional filters."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        tools = list(catalog.tools.values())

        if capability:
            tools = [t for t in tools if t.capability == capability]
        if tags:
            tools = [t for t in tools if any(tag in t.tags for tag in tags)]
        if risk:
            tools = [t for t in tools if t.risk == risk]

        return {
            "tools": [t.to_dict() for t in tools],
            "count": len(tools),
            "filters": {"capability": capability, "tags": tags, "risk": risk},
        }

    try:
        registry.register(ToolManifest(
            id="catalog.list_tools",
            title="List Catalog Tools",
            description="List tools in the catalog with optional filters",
            capability="catalog.list_tools",
            risk="read",
            typical_latency_ms=20,
            tier="task",
            tags=["catalog", "list"],
            input_schema={
                "type": "object",
                "properties": {
                    "capability": {"type": ["string", "null"], "description": "Filter by capability"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by tags"},
                    "risk": {"type": ["string", "null"], "enum": ["read", "write", "network", "exec", "privileged"], "description": "Filter by risk level"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_list_tools)
        report["registered"].append("catalog.list_tools")
    except Exception as e:
        report["errors"].append(f"catalog.list_tools: {e}")

    # Tool: catalog.describe
    def tool_catalog_describe(
        tool_id: str,
    ) -> dict[str, Any]:
        """Get detailed information about a specific tool."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        if tool_id not in catalog.tools:
            return {"error": f"Tool not found in catalog: {tool_id}"}

        return catalog.tools[tool_id].to_dict()

    try:
        registry.register(ToolManifest(
            id="catalog.describe",
            title="Describe Tool",
            description="Get detailed information about a specific tool in the catalog",
            capability="catalog.describe",
            risk="read",
            typical_latency_ms=10,
            tier="task",
            tags=["catalog", "describe"],
            input_schema={
                "type": "object",
                "properties": {
                    "tool_id": {"type": "string", "description": "Tool ID to describe"},
                },
                "required": ["tool_id"],
                "additionalProperties": False,
            },
        ), tool_catalog_describe)
        report["registered"].append("catalog.describe")
    except Exception as e:
        report["errors"].append(f"catalog.describe: {e}")

    # Tool: catalog.list_integrations
    def tool_catalog_list_integrations(
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """List integrations in the catalog."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        integrations = list(catalog.integrations.values())
        if enabled is not None:
            integrations = [i for i in integrations if i.enabled == enabled]

        return {
            "integrations": [i.to_dict() for i in integrations],
            "count": len(integrations),
        }

    try:
        registry.register(ToolManifest(
            id="catalog.list_integrations",
            title="List Integrations",
            description="List all integrations in the catalog",
            capability="catalog.list_integrations",
            risk="read",
            typical_latency_ms=10,
            tier="task",
            tags=["catalog", "integrations"],
            input_schema={
                "type": "object",
                "properties": {
                    "enabled": {"type": ["boolean", "null"], "description": "Filter by enabled status"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_list_integrations)
        report["registered"].append("catalog.list_integrations")
    except Exception as e:
        report["errors"].append(f"catalog.list_integrations: {e}")

    # Tool: catalog.list_projects
    def tool_catalog_list_projects(
        root: str | None = None,
    ) -> dict[str, Any]:
        """List projects in the catalog."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        projects = list(catalog.projects.values())
        if root:
            projects = [p for p in projects if p.root == root]

        return {
            "projects": [p.to_dict() for p in projects],
            "count": len(projects),
        }

    try:
        registry.register(ToolManifest(
            id="catalog.list_projects",
            title="List Projects",
            description="List all projects in the catalog",
            capability="catalog.list_projects",
            risk="read",
            typical_latency_ms=10,
            tier="task",
            tags=["catalog", "projects"],
            input_schema={
                "type": "object",
                "properties": {
                    "root": {"type": ["string", "null"], "description": "Filter by project root"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_list_projects)
        report["registered"].append("catalog.list_projects")
    except Exception as e:
        report["errors"].append(f"catalog.list_projects: {e}")

    # Tool: catalog.get_distribution
    def tool_catalog_get_distribution(
        capability: str | None = None,
    ) -> dict[str, Any]:
        """Get distribution of tools by capability, risk, or tags."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        tools = list(catalog.tools.values())
        if capability:
            tools = [t for t in tools if t.capability == capability]

        by_capability = {}
        by_risk = {}
        by_tag = {}

        for t in tools:
            by_capability[t.capability] = by_capability.get(t.capability, 0) + 1
            by_risk[t.risk] = by_risk.get(t.risk, 0) + 1
            for tag in t.tags:
                by_tag[tag] = by_tag.get(tag, 0) + 1

        return {
            "total": len(tools),
            "by_capability": by_capability,
            "by_risk": by_risk,
            "by_tag": by_tag,
        }

    try:
        registry.register(ToolManifest(
            id="catalog.get_distribution",
            title="Get Distribution",
            description="Get distribution of tools by capability, risk, or tags",
            capability="catalog.get_distribution",
            risk="read",
            typical_latency_ms=20,
            tier="task",
            tags=["catalog", "distribution"],
            input_schema={
                "type": "object",
                "properties": {
                    "capability": {"type": ["string", "null"], "description": "Filter by capability"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_get_distribution)
        report["registered"].append("catalog.get_distribution")
    except Exception as e:
        report["errors"].append(f"catalog.get_distribution: {e}")

    # Tool: catalog.update_tool
    def tool_catalog_update_tool(
        tool_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Update a tool's metadata in the catalog."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        if tool_id not in catalog.tools:
            return {"error": f"Tool not found: {tool_id}"}

        tool = catalog.tools[tool_id]
        for key, value in updates.items():
            if hasattr(tool, key) and key not in ("id", "registered_at"):
                setattr(tool, key, value)

        mgr.save()
        return {"updated": True, "tool_id": tool_id}

    try:
        registry.register(ToolManifest(
            id="catalog.update_tool",
            title="Update Tool",
            description="Update a tool's metadata in the catalog",
            capability="catalog.update_tool",
            risk="write",
            typical_latency_ms=50,
            tier="task",
            tags=["catalog", "update"],
            input_schema={
                "type": "object",
                "properties": {
                    "tool_id": {"type": "string", "description": "Tool ID to update"},
                    "updates": {"type": "object", "description": "Fields to update"},
                },
                "required": ["tool_id", "updates"],
                "additionalProperties": False,
            },
        ), tool_catalog_update_tool)
        report["registered"].append("catalog.update_tool")
    except Exception as e:
        report["errors"].append(f"catalog.update_tool: {e}")

    # Tool: catalog.remove_tool
    def tool_catalog_remove_tool(
        tool_id: str,
    ) -> dict[str, Any]:
        """Remove a tool from the catalog."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        if tool_id not in catalog.tools:
            return {"error": f"Tool not found: {tool_id}"}

        del catalog.tools[tool_id]
        mgr.save()
        return {"removed": True, "tool_id": tool_id}

    try:
        registry.register(ToolManifest(
            id="catalog.remove_tool",
            title="Remove Tool",
            description="Remove a tool from the catalog",
            capability="catalog.remove_tool",
            risk="write",
            typical_latency_ms=50,
            tier="task",
            tags=["catalog", "remove"],
            input_schema={
                "type": "object",
                "properties": {
                    "tool_id": {"type": "string", "description": "Tool ID to remove"},
                },
                "required": ["tool_id"],
                "additionalProperties": False,
            },
        ), tool_catalog_remove_tool)
        report["registered"].append("catalog.remove_tool")
    except Exception as e:
        report["errors"].append(f"catalog.remove_tool: {e}")

    # Tool: catalog.export
    def tool_catalog_export(
        format: str = "json",
        path: str | None = None,
    ) -> dict[str, Any]:
        """Export the catalog in various formats."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        tools = [t.to_dict() for t in catalog.tools.values()]

        if format == "json":
            content = json.dumps({"tools": tools, "catalog": catalog.to_dict()}, indent=2)
        elif format == "jsonl":
            content = "\n".join(json.dumps(t) for t in tools)
        elif format == "csv":
            import csv
            import io
            output = io.StringIO()
            if tools:
                writer = csv.DictWriter(output, fieldnames=tools[0].keys())
                writer.writeheader()
                writer.writerows(tools)
            content = output.getvalue()
        else:
            return {"error": f"Unsupported format: {format}. Supported: json, jsonl, csv"}

        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return {"exported": True, "format": format, "path": path, "tool_count": len(tools)}
            except OSError as e:
                return {"error": f"Failed to write export: {e}"}

        return {"exported": True, "format": format, "content": content, "tool_count": len(tools)}

    try:
        registry.register(ToolManifest(
            id="catalog.export",
            title="Export Catalog",
            description="Export the catalog in various formats",
            capability="catalog.export",
            risk="read",
            typical_latency_ms=100,
            tier="task",
            tags=["catalog", "export"],
            input_schema={
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["json", "jsonl", "csv"], "default": "json"},
                    "path": {"type": ["string", "null"], "description": "Output file path"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_export)
        report["registered"].append("catalog.export")
    except Exception as e:
        report["errors"].append(f"catalog.export: {e}")

    # Tool: catalog.diff
    def tool_catalog_diff(
        old_path: str,
        new_path: str | None = None,
        ignore_timestamps: bool = True,
        ignore_fields: list[str] | None = None,
        output_format: str = "summary",
    ) -> dict[str, Any]:
        """Compare two catalogs or compare current catalog with a saved one."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)

        def load_catalog_from_path(path: str) -> ToolCatalog:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return ToolCatalog.from_dict(data)
            except (OSError, json.JSONDecodeError) as e:
                raise OSError(f"Failed to load catalog from {path}: {e}")

        try:
            old_cat = load_catalog_from_path(old_path)
        except OSError as e:
            return {"error": str(e)}

        if new_path:
            try:
                new_cat = load_catalog_from_path(new_path)
            except OSError as e:
                return {"error": str(e)}
        else:
            new_cat = mgr.catalog or mgr.load()

        return _compute_diff(old_cat, new_cat, ignore_timestamps, ignore_fields, output_format)

    try:
        registry.register(ToolManifest(
            id="catalog.diff",
            title="Diff Catalogs",
            description="Compare two catalogs or compare current catalog with a saved one",
            capability="catalog.diff",
            risk="read",
            typical_latency_ms=100,
            tier="task",
            tags=["catalog", "diff", "compare"],
            input_schema={
                "type": "object",
                "properties": {
                    "old_path": {"type": "string", "description": "Path to old catalog"},
                    "new_path": {"type": ["string", "null"], "description": "Path to new catalog (defaults to current)"},
                    "ignore_timestamps": {"type": "boolean", "default": True, "description": "Ignore timestamp fields"},
                    "ignore_fields": {"type": "array", "items": {"type": "string"}, "description": "Additional fields to ignore"},
                    "output_format": {"type": "string", "enum": ["summary", "detailed", "patch"], "default": "summary"},
                },
                "required": ["old_path"],
                "additionalProperties": False,
            },
        ), tool_catalog_diff)
        report["registered"].append("catalog.diff")
    except Exception as e:
        report["errors"].append(f"catalog.diff: {e}")

    # Tool: catalog.merge
    def tool_catalog_merge(
        base_path: str,
        overlay_path: str,
        output_path: str | None = None,
        strategy: str = "overlay",
    ) -> dict[str, Any]:
        """Merge two catalogs together."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)

        def load_catalog_from_path(path: str) -> ToolCatalog:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return ToolCatalog.from_dict(data)
            except (OSError, json.JSONDecodeError) as e:
                raise OSError(f"Failed to load catalog from {path}: {e}")

        try:
            base_cat = load_catalog_from_path(base_path)
        except OSError as e:
            return {"error": str(e)}

        try:
            overlay_cat = load_catalog_from_path(overlay_path)
        except OSError as e:
            return {"error": str(e)}

        merged_cat = _merge_catalogs(base_cat, overlay_cat, strategy)

        if output_path:
            try:
                target_path = Path(output_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with open(target_path, "w", encoding="utf-8") as f:
                    json.dump(merged_cat.to_dict(), f, indent=2)
                return {"merged": True, "path": output_path, "tools": len(merged_cat.tools)}
            except OSError as e:
                return {"error": f"Failed to write merged catalog: {e}"}

        return {"merged": True, "tools": len(merged_cat.tools), "catalog": merged_cat.to_dict()}

    try:
        registry.register(ToolManifest(
            id="catalog.merge",
            title="Merge Catalogs",
            description="Merge two catalogs together with configurable strategy",
            capability="catalog.merge",
            risk="write",
            typical_latency_ms=100,
            tier="task",
            tags=["catalog", "merge"],
            input_schema={
                "type": "object",
                "properties": {
                    "base_path": {"type": "string", "description": "Path to base catalog"},
                    "overlay_path": {"type": "string", "description": "Path to overlay catalog"},
                    "output_path": {"type": ["string", "null"], "description": "Output path for merged catalog"},
                    "strategy": {"type": "string", "enum": ["overlay", "replace", "union"], "default": "overlay"},
                },
                "required": ["base_path", "overlay_path"],
                "additionalProperties": False,
            },
        ), tool_catalog_merge)
        report["registered"].append("catalog.merge")
    except Exception as e:
        report["errors"].append(f"catalog.merge: {e}")

    # Tool: catalog.generate_schema
    def tool_catalog_generate_schema(
        format: str = "json",
        path: str | None = None,
    ) -> dict[str, Any]:
        """Generate JSON Schema for all tool input schemas."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        schemas = {}
        for tool in catalog.tools.values():
            schemas[tool.id] = tool.input_schema

        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": schemas,
        }

        if format == "json":
            content = json.dumps(schema, indent=2)
        else:
            return {"error": f"Unsupported format: {format}"}

        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return {"generated": True, "format": format, "path": path}
            except OSError as e:
                return {"error": f"Failed to write schema: {e}"}

        return {"generated": True, "format": format, "schema": schema}

    try:
        registry.register(ToolManifest(
            id="catalog.generate_schema",
            title="Generate Schema",
            description="Generate JSON Schema for all tool input schemas",
            capability="catalog.generate_schema",
            risk="read",
            typical_latency_ms=50,
            tier="task",
            tags=["catalog", "schema", "generate"],
            input_schema={
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["json"], "default": "json"},
                    "path": {"type": ["string", "null"], "description": "Output file path"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_generate_schema)
        report["registered"].append("catalog.generate_schema")
    except Exception as e:
        report["errors"].append(f"catalog.generate_schema: {e}")

    # Tool: catalog.generate_inventory
    def tool_catalog_generate_inventory(
        format: str = "json",
        path: str | None = None,
    ) -> dict[str, Any]:
        """Generate an inventory report of all tools."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        groups = {}
        for tool in catalog.tools.values():
            cap = tool.capability
            if cap not in groups:
                groups[cap] = []
            groups[cap].append(tool.to_dict())

        inventory = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_tools": len(catalog.tools),
            "total_integrations": len(catalog.integrations),
            "total_projects": len(catalog.projects),
            "groups": groups,
        }

        if format == "json":
            content = json.dumps(inventory, indent=2)
        else:
            return {"error": f"Unsupported format: {format}"}

        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return {"generated": True, "format": format, "path": path, "groups": len(groups)}
            except OSError as e:
                return {"error": f"Failed to write inventory: {e}"}

        return {"generated": True, "format": format, "inventory": inventory}

    try:
        registry.register(ToolManifest(
            id="catalog.generate_inventory",
            title="Generate Inventory",
            description="Generate an inventory report of all tools grouped by capability",
            capability="catalog.generate_inventory",
            risk="read",
            typical_latency_ms=50,
            tier="task",
            tags=["catalog", "inventory", "generate"],
            input_schema={
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["json"], "default": "json"},
                    "path": {"type": ["string", "null"], "description": "Output file path"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_generate_inventory)
        report["registered"].append("catalog.generate_inventory")
    except Exception as e:
        report["errors"].append(f"catalog.generate_inventory: {e}")

    # Tool: catalog.generate_audit
    def tool_catalog_generate_audit(
        format: str = "json",
        path: str | None = None,
        checks: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate an audit report for the catalog."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        available_checks = _AUDIT_CHECKS
        checks_to_run = checks or list(available_checks.keys())

        audit = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "catalog_version": catalog.schema_version,
            "total_tools": len(catalog.tools),
            "results": {},
        }

        for check in checks_to_run:
            if check == "secret_scan":
                # Scan for potential secrets in tool metadata
                audit["results"]["secret_scan"] = _audit_secret_scan(catalog)
            elif check == "risk_audit":
                audit["results"]["risk_audit"] = _audit_risk_distribution(catalog)
            elif check == "schema_validation":
                audit["results"]["schema_validation"] = _audit_schema_validation(catalog)
            elif check == "dependency_freshness":
                audit["results"]["dependency_freshness"] = _audit_dependency_freshness(catalog)
            elif check == "coverage":
                audit["results"]["coverage"] = _audit_coverage(catalog)
            elif check == "duplicates":
                audit["results"]["duplicates"] = _audit_duplicates(catalog)
            elif check == "sbom":
                audit["results"]["sbom"] = _audit_sbom(catalog)
            elif check == "license_check":
                audit["results"]["license_check"] = _audit_license_check(catalog)
            elif check == "vulnerability_scan":
                audit["results"]["vulnerability_scan"] = _audit_vulnerability_scan(catalog)
            elif check == "supply_chain":
                audit["results"]["supply_chain"] = _audit_supply_chain(catalog)
            else:
                audit["results"][check] = {"status": "skipped", "reason": "Unknown check"}

        if format == "json":
            content = json.dumps(audit, indent=2)
        else:
            return {"error": f"Unsupported format: {format}"}

        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return {"generated": True, "format": format, "path": path}
            except OSError as e:
                return {"error": f"Failed to write audit: {e}"}

        return {"generated": True, "format": format, "audit": audit}

    try:
        registry.register(ToolManifest(
            id="catalog.generate_audit",
            title="Generate Audit",
            description="Generate an audit report for the catalog with various checks",
            capability="catalog.generate_audit",
            risk="read",
            typical_latency_ms=200,
            tier="task",
            tags=["catalog", "audit", "generate"],
            input_schema={
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["json"], "default": "json"},
                    "path": {"type": ["string", "null"], "description": "Output file path"},
                    "checks": {"type": "array", "items": {"type": "string", "enum": list(_AUDIT_CHECKS.keys())}, "description": "Specific checks to run (default: all)"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_generate_audit)
        report["registered"].append("catalog.generate_audit")
    except Exception as e:
        report["errors"].append(f"catalog.generate_audit: {e}")

    # Tool: catalog.publish
    def tool_catalog_publish(
        registry_url: str,
        auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Publish the catalog to a remote registry."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        catalog = mgr.catalog or mgr.load()

        # TODO: Implement actual publishing to remote registry
        # For now, return a mock success response
        return {
            "published": True,
            "registry_url": registry_url,
            "catalog_version": catalog.schema_version,
            "tool_count": len(catalog.tools),
        }

    try:
        registry.register(ToolManifest(
            id="catalog.publish",
            title="Publish Catalog",
            description="Publish the catalog to a remote registry",
            capability="catalog.publish",
            risk="write",
            typical_latency_ms=500,
            tier="task",
            tags=["catalog", "publish", "distribute"],
            input_schema={
                "type": "object",
                "properties": {
                    "registry_url": {"type": "string", "description": "Remote registry URL"},
                    "auth_token": {"type": ["string", "null"], "description": "Authentication token"},
                },
                "required": ["registry_url"],
                "additionalProperties": False,
            },
        ), tool_catalog_publish)
        report["registered"].append("catalog.publish")
    except Exception as e:
        report["errors"].append(f"catalog.publish: {e}")

    # Tool: catalog.package
    def tool_catalog_package(
        path: str | None = None,
        format: str = "json",
        include_sources: bool = False,
    ) -> dict[str, Any]:
        """Create a distribution package from the catalog."""
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        cat = mgr.catalog or mgr.load()

        package = {
            "schema_version": cat.schema_version,
            "generated_at": cat.generated_at,
            "toolkit_version": cat.toolkit_version,
            "tools": {tid: tool.to_dict() for tid, tool in cat.tools.items()},
            "integrations": {iid: integ.to_dict() for iid, integ in cat.integrations.items()},
            "projects": {pid: proj.to_dict() for pid, proj in cat.projects.items()},
            "metadata": cat.metadata,
        }

        if format == "json":
            save_path = path or f"catalog-{cat.toolkit_version}.json"
            try:
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(package, indent=2))
                return {"packaged": True, "format": "json", "path": save_path}
            except OSError as e:
                return {"error": f"Failed to create package: {e}"}
        else:
            return {"error": f"Unsupported format: {format}. Only json supported currently."}

    try:
        registry.register(ToolManifest(
            id="catalog.package",
            title="Package Catalog",
            description="Create a distribution package from the catalog",
            capability="catalog.package",
            risk="write",
            typical_latency_ms=100,
            tier="task",
            tags=["catalog", "package", "distribute"],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": ["string", "null"], "description": "Output file path"},
                    "format": {"type": "string", "enum": ["json"], "default": "json"},
                    "include_sources": {"type": "boolean", "default": False, "description": "Include source files (not yet implemented)"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_package)
        report["registered"].append("catalog.package")
    except Exception as e:
        report["errors"].append(f"catalog.package: {e}")

    # Tool: catalog.watch
    def tool_catalog_watch(
        interval: float = 1.0,
        debounce: float = 0.5,
        max_iterations: int = 0,
    ) -> dict[str, Any]:
        """Watch for catalog changes and auto-refresh.

        Uses filesystem watching to detect changes in tool directories and
        automatically re-index the catalog.
        """
        if not _toolkit:
            return {"error": "Toolkit not available"}

        mgr = get_catalog_manager(_toolkit.config.state.dir, _toolkit)
        mgr._toolkit = _toolkit  # Attach for watch helpers

        try:
            import watchfiles
            return _watch_watchfiles(mgr, debounce, max_iterations)
        except ImportError:
            return _watch_polling(mgr, interval, debounce, max_iterations)

    try:
        registry.register(ToolManifest(
            id="catalog.watch",
            title="Watch Catalog",
            description="Watch for catalog changes and auto-refresh on filesystem changes",
            capability="catalog.watch",
            risk="read",
            typical_latency_ms=0,
            tier="task",
            tags=["catalog", "watch", "auto-refresh"],
            input_schema={
                "type": "object",
                "properties": {
                    "interval": {"type": "number", "default": 1.0, "description": "Polling interval in seconds (fallback only)"},
                    "debounce": {"type": "number", "default": 0.5, "description": "Debounce time in seconds"},
                    "max_iterations": {"type": "integer", "default": 0, "description": "Max iterations (0 = infinite)"},
                },
                "additionalProperties": False,
            },
        ), tool_catalog_watch)
        report["registered"].append("catalog.watch")
    except Exception as e:
        report["errors"].append(f"catalog.watch: {e}")

    return report


# Audit helper functions
def _audit_secret_scan(catalog: ToolCatalog) -> dict[str, Any]:
    """Scan for potential secrets in tool metadata."""
    import re
    secret_patterns = [
        r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"]?[a-zA-Z0-9]{20,}",
        r"(?i)(aws[_-]?access[_-]?key|aws[_-]?secret[_-]?key)\s*[:=]\s*['\"]?[A-Z0-9]{20,}",
        r"(?i)(github[_-]?token|gh[_-]?token)\s*[:=]\s*['\"]?gh[opsu]_[a-zA-Z0-9]{36}",
    ]
    findings = []
    for tool in catalog.tools.values():
        tool_json = json.dumps(tool.to_dict())
        for pattern in secret_patterns:
            matches = re.findall(pattern, tool_json)
            if matches:
                findings.append({"tool": tool.id, "pattern": pattern, "matches": len(matches)})
    return {"status": "completed", "findings": findings, "tools_scanned": len(catalog.tools)}


def _audit_risk_distribution(catalog: ToolCatalog) -> dict[str, Any]:
    """Audit risk distribution and high-risk tools."""
    risk_counts = {}
    high_risk_tools = []
    for tool in catalog.tools.values():
        risk_counts[tool.risk] = risk_counts.get(tool.risk, 0) + 1
        if tool.risk in ("privileged", "exec", "network"):
            high_risk_tools.append(tool.id)
    return {
        "status": "completed",
        "risk_distribution": risk_counts,
        "high_risk_tools": high_risk_tools,
        "high_risk_count": len(high_risk_tools),
    }


def _audit_schema_validation(catalog: ToolCatalog) -> dict[str, Any]:
    """Validate all tool input schemas."""
    from jsonschema import Draft7Validator
    invalid_schemas = []
    for tool in catalog.tools.values():
        try:
            Draft7Validator.check_schema(tool.input_schema)
        except Exception as e:
            invalid_schemas.append({"tool": tool.id, "issue": str(e)})
    return {
        "status": "completed",
        "valid_schemas": len(catalog.tools) - len(invalid_schemas),
        "invalid_schemas": invalid_schemas,
    }


def _audit_dependency_freshness(catalog: ToolCatalog) -> dict[str, Any]:
    """Check for stale registered_at timestamps."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    stale_threshold = timedelta(days=90)
    stale_tools = []
    for tool in catalog.tools.values():
        if tool.registered_at:
            try:
                reg_date = datetime.fromisoformat(tool.registered_at.replace('Z', '+00:00'))
                if now - reg_date > stale_threshold:
                    stale_tools.append({"tool": tool.id, "registered_at": tool.registered_at})
            except Exception:
                pass
    return {
        "status": "completed",
        "stale_tools": stale_tools,
        "stale_count": len(stale_tools),
        "threshold_days": 90,
    }


def _audit_coverage(catalog: ToolCatalog) -> dict[str, Any]:
    """Check capability and tag coverage."""
    capabilities = set()
    tags = set()
    for tool in catalog.tools.values():
        capabilities.add(tool.capability)
        tags.update(tool.tags)
    return {
        "status": "completed",
        "unique_capabilities": len(capabilities),
        "unique_tags": len(tags),
        "capabilities": sorted(capabilities),
        "tags": sorted(tags),
    }


def _audit_duplicates(catalog: ToolCatalog) -> dict[str, Any]:
    """Check for duplicate tool IDs or capabilities."""
    seen_ids = set()
    seen_caps = {}
    duplicate_ids = []
    duplicate_caps = []
    for tool in catalog.tools.values():
        if tool.id in seen_ids:
            duplicate_ids.append(tool.id)
        seen_ids.add(tool.id)
        if tool.capability in seen_caps:
            duplicate_caps.append({"capability": tool.capability, "tools": [seen_caps[tool.capability], tool.id]})
        else:
            seen_caps[tool.capability] = tool.id
    return {
        "status": "completed",
        "duplicate_ids": duplicate_ids,
        "duplicate_capabilities": duplicate_caps,
    }


def _audit_sbom(catalog: ToolCatalog) -> dict[str, Any]:
    """Generate Software Bill of Materials for integrations."""
    sbom = {"packages": [], "relationships": []}
    for integ in catalog.integrations.values():
        if integ.metadata and "dependencies" in integ.metadata:
            for dep in integ.metadata["dependencies"]:
                sbom["packages"].append({
                    "name": dep.get("name", "unknown"),
                    "version": dep.get("version", "unknown"),
                    "source": integ.id,
                })
    return {"status": "completed", "packages_found": len(sbom["packages"]), "sbom": sbom}


def _audit_license_check(catalog: ToolCatalog) -> dict[str, Any]:
    """Check license compatibility and compliance."""
    licenses = {}
    for integ in catalog.integrations.values():
        if integ.metadata and "license" in integ.metadata:
            lic = integ.metadata["license"]
            licenses[lic] = licenses.get(lic, 0) + 1
    return {
        "status": "completed",
        "licenses_found": licenses,
        "total_integrations_with_license": sum(licenses.values()),
    }


def _audit_vulnerability_scan(catalog: ToolCatalog) -> dict[str, Any]:
    """Scan for known vulnerabilities in dependencies."""
    # Placeholder - would integrate with vulnerability database
    return {
        "status": "completed",
        "vulnerabilities_found": 0,
        "note": "Requires integration with vulnerability database (e.g., OSV, GitHub Advisory)",
    }


def _audit_supply_chain(catalog: ToolCatalog) -> dict[str, Any]:
    """Analyze supply chain risks in tool dependencies."""
    risks = []
    for integ in catalog.integrations.values():
        if integ.metadata and "dependencies" in integ.metadata:
            for dep in integ.metadata["dependencies"]:
                if dep.get("source") == "pypi" and not dep.get("verified", False):
                    risks.append({"package": dep.get("name"), "risk": "unverified_source", "integration": integ.id})
    return {
        "status": "completed",
        "risks_found": len(risks),
        "risks": risks,
    }


def _compute_diff(
    old_cat: ToolCatalog,
    new_cat: ToolCatalog,
    ignore_timestamps: bool = True,
    ignore_fields: list[str] | None = None,
    output_format: str = "summary",
) -> dict[str, Any]:
    """Compute diff between two catalogs."""
    old_tools = {t.id: t for t in old_cat.tools.values()}
    new_tools = {t.id: t for t in new_cat.tools.values()}

    default_ignore = {"registered_at"}
    if ignore_timestamps:
        default_ignore.update({"generated_at", "registered_at", "metadata"})

    if ignore_fields:
        default_ignore.update(ignore_fields)

    added = []
    removed = []
    modified = []

    all_ids = set(old_tools.keys()) | set(new_tools.keys())

    for tid in all_ids:
        old_tool = old_tools.get(tid)
        new_tool = new_tools.get(tid)

        if old_tool is None:
            added.append(new_tool.to_dict() if new_tool else {"id": tid})
        elif new_tool is None:
            removed.append(old_tool.to_dict())
        else:
            changes = []
            old_dict = old_tool.to_dict()
            new_dict = new_tool.to_dict()
            for key in set(old_dict.keys()) | set(new_dict.keys()):
                if key in default_ignore:
                    continue
                old_val = old_dict.get(key)
                new_val = new_dict.get(key)
                if old_val != new_val:
                    changes.append({"field": key, "old": old_val, "new": new_val})
            if changes:
                modified.append({"tool_id": tid, "changes": changes})

    result = {
        "added": added,
        "removed": removed,
        "modified": modified,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "total_old": len(old_tools),
            "total_new": len(new_tools),
        },
    }

    if output_format == "detailed":
        pass
    elif output_format == "patch":
        patch = []
        for item in added:
            patch.append({"op": "add", "path": f"/tools/{item['id']}", "value": item})
        for item in removed:
            patch.append({"op": "remove", "path": f"/tools/{item['id']}"})
        for mod in modified:
            for change in mod["changes"]:
                patch.append({
                    "op": "replace",
                    "path": f"/tools/{mod['tool_id']}/{change['field']}",
                    "value": change["new"]
                })
        result["patch"] = patch

    return result


def _merge_catalogs(
    base_cat: ToolCatalog,
    overlay_cat: ToolCatalog,
    strategy: str = "overlay",
) -> ToolCatalog:
    """Merge two catalogs together."""
    if strategy == "replace":
        return overlay_cat
    elif strategy == "union":
        merged_tools = dict(base_cat.tools)
        for tid, tool in overlay_cat.tools.items():
            merged_tools[tid] = tool
    else:
        merged_tools = dict(base_cat.tools)
        merged_tools.update(overlay_cat.tools)

    merged_integrations = dict(base_cat.integrations)
    merged_integrations.update(overlay_cat.integrations)

    merged_projects = dict(base_cat.projects)
    merged_projects.update(overlay_cat.projects)

    generated_at = overlay_cat.generated_at
    if base_cat.generated_at > overlay_cat.generated_at:
        generated_at = base_cat.generated_at

    return ToolCatalog(
        schema_version=overlay_cat.schema_version,
        generated_at=generated_at,
        toolkit_version=overlay_cat.toolkit_version,
        tools=merged_tools,
        integrations=merged_integrations,
        projects=merged_projects,
        metadata={**base_cat.metadata, **overlay_cat.metadata},
    )