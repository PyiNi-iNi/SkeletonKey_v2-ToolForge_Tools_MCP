"""Integration Tools for SkeletonKey (P5.3).

This module provides tools for managing integrations through the engine.
These tools allow integrations to be managed via the same mechanism as
other SkeletonKey tools.

Tools provided:
- integrations.list - List all available integrations
- integrations.describe - Get details of a specific integration
- integrations.load - Load an integration
- integrations.unload - Unload an integration
- integrations.enable - Enable an integration
- integrations.disable - Disable an integration
- integrations.health - Check health of an integration
- integrations.stats - Get integration statistics
- integrations.config - Get/set integration configuration
- integrations.secrets - Manage integration secrets
"""

from __future__ import annotations

import logging
from typing import Any

from .manifest import ToolManifest

logger = logging.getLogger(__name__)


def register_integration_tools(
    registry,
    *,
    engine: Any = None,
    integrations_manager: Any = None,
) -> dict[str, Any]:
    """Register integration management tools with the registry.
    
    Args:
        registry: The tool registry
        engine: The engine instance
        integrations_manager: The integration manager instance
        
    Returns:
        dict: Report of registered tools
    """
    report: dict[str, Any] = {"registered": [], "errors": []}
    
    # Store references
    _engine = engine
    _manager = integrations_manager
    
    # Tool: integrations.list
    def tool_integrations_list(
        type_filter: str | None = None,
        category_filter: str | None = None,
        platform_filter: str | None = None,
        enabled_only: bool = False,
        disabled_only: bool = False,
        loaded_only: bool = False,
    ) -> dict[str, Any]:
        """List all available integrations with optional filtering."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        from ..integrations import IntegrationType, IntegrationCategory, IntegrationPlatform
        
        # Convert string filters to enums
        type_enum = None
        if type_filter:
            try:
                type_enum = IntegrationType(type_filter)
            except ValueError:
                pass
        
        category_enum = None
        if category_filter:
            try:
                category_enum = IntegrationCategory(category_filter)
            except ValueError:
                pass
        
        platform_enum = None
        if platform_filter:
            try:
                platform_enum = IntegrationPlatform(platform_filter)
            except ValueError:
                pass
        
        integrations = _manager.list_integrations(
            type_filter=type_enum,
            category_filter=category_enum,
            platform_filter=platform_enum,
            enabled_only=enabled_only,
            disabled_only=disabled_only,
        )
        
        # Filter by loaded status
        if loaded_only:
            loaded_names = set(_manager._instances.keys())
            integrations = [i for i in integrations if i.name in loaded_names]
        
        result = {
            "integrations": [i.to_dict() for i in integrations],
            "total": len(integrations),
        }
        
        # Add loaded status
        loaded_names = set(_manager._instances.keys())
        enabled_names = _manager._enabled_integrations
        disabled_names = _manager._disabled_integrations
        
        for i in result["integrations"]:
            i["loaded"] = i["name"] in loaded_names
            i["enabled"] = i["name"] in enabled_names
            i["disabled"] = i["name"] in disabled_names
        
        return result
    
    try:
        registry.register(ToolManifest(
            id="integrations.list",
            title="List Integrations",
            description="List all available integrations with optional filtering by type, category, platform, or status",
            capability="integrations.list",
            risk="none",
            typical_latency_ms=10,
            tier="task",
            tags=["integrations", "list", "discovery"],
            input_schema={
                "type": "object",
                "properties": {
                    "type_filter": {"type": "string", "description": "Filter by integration type"},
                    "category_filter": {"type": "string", "description": "Filter by category"},
                    "platform_filter": {"type": "string", "description": "Filter by platform"},
                    "enabled_only": {"type": "boolean", "description": "Show only enabled integrations"},
                    "disabled_only": {"type": "boolean", "description": "Show only disabled integrations"},
                    "loaded_only": {"type": "boolean", "description": "Show only loaded integrations"},
                },
                "additionalProperties": False,
            },
        ), tool_integrations_list)
        report["registered"].append("integrations.list")
    except Exception as e:
        report["errors"].append(f"integrations.list: {e}")
    
    # Tool: integrations.describe
    def tool_integrations_describe(name: str) -> dict[str, Any]:
        """Get detailed information about a specific integration."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        info = _manager.registry.get_integration_info(name)
        if not info:
            return {"error": f"Integration not found: {name}"}
        
        result = info.to_dict()
        
        # Add status information
        instance = _manager.get_integration(name)
        if instance:
            result["loaded"] = True
            result["status"] = instance.status.value
            result["health"] = instance.health
            result["health_message"] = instance.health_message
        else:
            result["loaded"] = False
            result["status"] = "not-loaded"
            result["health"] = "unknown"
            result["health_message"] = ""
        
        result["enabled"] = name in _manager._enabled_integrations
        result["disabled"] = name in _manager._disabled_integrations
        
        return result
    
    try:
        registry.register(ToolManifest(
            id="integrations.describe",
            title="Describe Integration",
            description="Get detailed information about a specific integration including metadata, configuration, and status",
            capability="integrations.describe",
            risk="none",
            typical_latency_ms=10,
            tier="task",
            tags=["integrations", "describe", "metadata"],
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the integration to describe"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ), tool_integrations_describe)
        report["registered"].append("integrations.describe")
    except Exception as e:
        report["errors"].append(f"integrations.describe: {e}")
    
    # Tool: integrations.load
    def tool_integrations_load(name: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Load and initialize an integration."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        instance = _manager.load_integration(name, config=config)
        if not instance:
            return {"error": f"Failed to load integration: {name}"}
        
        return {
            "name": name,
            "loaded": True,
            "status": instance.status.value,
            "health": instance.health,
            "health_message": instance.health_message,
            "version": instance.info.version,
            "type": instance.info.integration_type.value,
        }
    
    try:
        registry.register(ToolManifest(
            id="integrations.load",
            title="Load Integration",
            description="Load and initialize an integration by name",
            capability="integrations.load",
            risk="write",
            typical_latency_ms=50,
            tier="task",
            tags=["integrations", "load", "initialize"],
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the integration to load"},
                    "config": {"type": "object", "description": "Configuration for the integration"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ), tool_integrations_load)
        report["registered"].append("integrations.load")
    except Exception as e:
        report["errors"].append(f"integrations.load: {e}")
    
    # Tool: integrations.unload
    def tool_integrations_unload(name: str) -> dict[str, Any]:
        """Unload an integration."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        success = _manager.unload_integration(name)
        return {
            "name": name,
            "unloaded": success,
        }
    
    try:
        registry.register(ToolManifest(
            id="integrations.unload",
            title="Unload Integration",
            description="Unload and clean up an integration",
            capability="integrations.unload",
            risk="write",
            typical_latency_ms=20,
            tier="task",
            tags=["integrations", "unload", "cleanup"],
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the integration to unload"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ), tool_integrations_unload)
        report["registered"].append("integrations.unload")
    except Exception as e:
        report["errors"].append(f"integrations.unload: {e}")
    
    # Tool: integrations.enable
    def tool_integrations_enable(name: str) -> dict[str, Any]:
        """Enable an integration."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        success = _manager.enable_integration(name)
        return {
            "name": name,
            "enabled": success,
        }
    
    try:
        registry.register(ToolManifest(
            id="integrations.enable",
            title="Enable Integration",
            description="Enable an integration (loads it if not already loaded)",
            capability="integrations.enable",
            risk="write",
            typical_latency_ms=20,
            tier="task",
            tags=["integrations", "enable", "activate"],
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the integration to enable"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ), tool_integrations_enable)
        report["registered"].append("integrations.enable")
    except Exception as e:
        report["errors"].append(f"integrations.enable: {e}")
    
    # Tool: integrations.disable
    def tool_integrations_disable(name: str) -> dict[str, Any]:
        """Disable an integration."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        success = _manager.disable_integration(name)
        return {
            "name": name,
            "disabled": success,
        }
    
    try:
        registry.register(ToolManifest(
            id="integrations.disable",
            title="Disable Integration",
            description="Disable an integration (unloads it if loaded)",
            capability="integrations.disable",
            risk="write",
            typical_latency_ms=20,
            tier="task",
            tags=["integrations", "disable", "deactivate"],
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the integration to disable"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ), tool_integrations_disable)
        report["registered"].append("integrations.disable")
    except Exception as e:
        report["errors"].append(f"integrations.disable: {e}")
    
    # Tool: integrations.health
    def tool_integrations_health(name: str | None = None) -> dict[str, Any]:
        """Check the health of one or all integrations."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        if name:
            result = _manager.check_health(name)
            return result
        else:
            results = _manager.check_all_health()
            return {
                "results": results,
                "healthy_count": sum(1 for r in results if r["health"] == "healthy"),
                "unhealthy_count": sum(1 for r in results if r["health"] != "healthy"),
            }
    
    try:
        registry.register(ToolManifest(
            id="integrations.health",
            title="Check Integration Health",
            description="Check the health of one or all loaded integrations",
            capability="integrations.health",
            risk="none",
            typical_latency_ms=20,
            tier="task",
            tags=["integrations", "health", "status"],
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of integration to check (None = all)"},
                },
                "additionalProperties": False,
            },
        ), tool_integrations_health)
        report["registered"].append("integrations.health")
    except Exception as e:
        report["errors"].append(f"integrations.health: {e}")
    
    # Tool: integrations.stats
    def tool_integrations_stats() -> dict[str, Any]:
        """Get statistics about integrations."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        return _manager.get_statistics()
    
    try:
        registry.register(ToolManifest(
            id="integrations.stats",
            title="Integration Statistics",
            description="Get statistics about all integrations including counts by type, category, and platform",
            capability="integrations.stats",
            risk="none",
            typical_latency_ms=10,
            tier="task",
            tags=["integrations", "stats", "metrics"],
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ), tool_integrations_stats)
        report["registered"].append("integrations.stats")
    except Exception as e:
        report["errors"].append(f"integrations.stats: {e}")
    
    # Tool: integrations.config
    def tool_integrations_config(
        name: str,
        get: bool = True,
        set: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get or set integration configuration."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        if get:
            config = _manager.get_integration_config(name)
            return {
                "name": name,
                "config": config,
            }
        elif set:
            # Update configuration in the config store
            integrations_config = getattr(_manager.config, 'integrations', None)
            if not integrations_config:
                return {"error": "Integration configuration not available"}
            
            # Update the config (use getattr for dataclass)
            configs = getattr(integrations_config, 'configs', {})
            configs[name] = set
            
            return {
                "name": name,
                "config": set,
                "updated": True,
            }
        else:
            return {"error": "Either get or set must be true"}
    
    try:
        registry.register(ToolManifest(
            id="integrations.config",
            title="Integration Configuration",
            description="Get or set configuration for an integration",
            capability="integrations.config",
            risk="write",
            typical_latency_ms=10,
            tier="task",
            tags=["integrations", "config", "settings"],
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the integration"},
                    "get": {"type": "boolean", "description": "Get configuration", "default": True},
                    "set": {"type": "object", "description": "Set configuration"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        ), tool_integrations_config)
        report["registered"].append("integrations.config")
    except Exception as e:
        report["errors"].append(f"integrations.config: {e}")
    
    # Tool: integrations.secrets
    def tool_integrations_secrets(
        action: str = "list",
        name: str | None = None,
        key: str | None = None,
        value: str | None = None,
    ) -> dict[str, Any]:
        """Manage integration secrets."""
        if not _manager:
            return {"error": "Integration manager not available"}
        
        if action == "list":
            secrets = _manager.list_secrets(integration=name)
            # Convert SecretInfo objects to dicts
            secret_list = []
            for s in secrets:
                secret_dict = s.to_dict()
                secret_list.append(secret_dict)
            return {
                "secrets": secret_list,
                "count": len(secrets),
            }
        elif action == "get":
            if not name or not key:
                return {"error": "name and key are required for get"}
            secret = _manager.get_secret(key, integration=name)
            if secret:
                return {
                    "key": key,
                    "integration": name,
                    "source": secret.source,
                }
            return {"error": f"Secret not found: {key}"}
        elif action == "set":
            if not name or not key or not value:
                return {"error": "name, key, and value are required for set"}
            success = _manager.set_secret(key, value, integration=name)
            return {"success": success}
        elif action == "delete":
            if not name or not key:
                return {"error": "name and key are required for delete"}
            success = _manager.delete_secret(key, integration=name)
            return {"success": success}
        elif action == "has":
            if not name or not key:
                return {"error": "name and key are required for has"}
            has = _manager.has_secret(key, integration=name)
            return {"has": has}
        else:
            return {"error": f"Unknown action: {action}"}
    
    try:
        registry.register(ToolManifest(
            id="integrations.secrets",
            title="Integration Secrets",
            description="Manage secrets for integrations (get, set, delete, list)",
            capability="integrations.secrets",
            risk="write",
            typical_latency_ms=10,
            tier="task",
            tags=["integrations", "secrets", "security"],
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "get", "set", "delete", "has"], "default": "list"},
                    "name": {"type": "string", "description": "Integration name"},
                    "key": {"type": "string", "description": "Secret key"},
                    "value": {"type": "string", "description": "Secret value (for set)"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        ), tool_integrations_secrets)
        report["registered"].append("integrations.secrets")
    except Exception as e:
        report["errors"].append(f"integrations.secrets: {e}")
    
    return report