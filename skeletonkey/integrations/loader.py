"""Dynamic Integration Loader for SkeletonKey (P5.3).

This module provides dynamic loading of integrations at runtime, supporting:
- Lazy loading (load on first use)
- Eager loading (load all at startup)
- Dependency resolution
- Conflict detection
- Loading validation
- Error handling and recovery

To avoid circular imports, this module uses lazy imports for Integration-related types.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Type checking imports
if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _dep_name(dep: Any) -> str:
    """Read a dependency/conflict name from a dataclass or a dict."""
    if isinstance(dep, dict):
        return str(dep.get("name", ""))
    return str(getattr(dep, "name", dep) or "")


def _dep_optional(dep: Any) -> bool:
    """Read the ``optional`` flag from a dependency dataclass or dict."""
    if isinstance(dep, dict):
        return bool(dep.get("optional", False))
    return bool(getattr(dep, "optional", False))


def _get_integration_class():
    """Lazy import of Integration base class."""
    from . import Integration
    return Integration


def _get_integration_config_class():
    """Lazy import of IntegrationConfig."""
    from . import IntegrationConfig
    return IntegrationConfig


def _get_integration_info_class():
    """Lazy import of IntegrationInfo."""
    from . import IntegrationInfo
    return IntegrationInfo


def _get_integration_instance_class():
    """Lazy import of IntegrationInstance."""
    from . import IntegrationInstance
    return IntegrationInstance


def _get_integration_status_class():
    """Lazy import of IntegrationStatus."""
    from . import IntegrationStatus
    return IntegrationStatus


def _get_registry():
    """Lazy import of registry."""
    from . import registry
    return registry


@dataclass
class LoadResult:
    """Result of a load operation."""
    success: bool
    instance: Any = None  # IntegrationInstance
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dependencies_loaded: list[str] = field(default_factory=list)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "instance": self.instance.info.to_dict() if self.instance else None,
            "errors": self.errors,
            "warnings": self.warnings,
            "dependencies_loaded": self.dependencies_loaded,
        }


@dataclass
class LoadError:
    """Represents an error that occurred during loading."""
    integration_name: str
    error_type: str
    message: str
    traceback: str | None = None
    retryable: bool = False
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "integration_name": self.integration_name,
            "error_type": self.error_type,
            "message": self.message,
            "retryable": self.retryable,
        }


class IntegrationLoader:
    """Loads integrations dynamically at runtime."""
    
    def __init__(
        self,
        registry: Any,
        config: Any = None,
        discovery: Any = None,
    ):
        """Initialize the integration loader.
        
        Args:
            registry: Integration registry
            config: SkeletonKey configuration
            discovery: Integration discovery instance (optional)
        """
        self.registry = registry
        self.config = config
        self._discovery = discovery
        self._loaded: dict[str, Any] = {}  # dict[str, IntegrationInstance]
        self._loading: set[str] = set()
        self._load_errors: dict[str, LoadError] = {}
    
    def load(self, name: str, config: dict[str, Any] | None = None) -> LoadResult:
        """Load an integration by name.
        
        Args:
            name: Integration name
            config: Optional configuration for the integration
            
        Returns:
            LoadResult: Result of the load operation
        """
        IntegrationInstance = _get_integration_instance_class()
        IntegrationInfo = _get_integration_info_class()
        
        # Check if already loaded
        if name in self._loaded:
            return LoadResult(
                success=True,
                instance=self._loaded[name],
                warnings=[f"Integration {name} already loaded"],
            )
        
        # Check if currently loading (circular dependency)
        if name in self._loading:
            return LoadResult(
                success=False,
                errors=[f"Circular dependency detected: {name} is already being loaded"],
            )
        
        # Mark as loading
        self._loading.add(name)
        
        try:
            # Get integration info
            info = self._get_integration_info(name)
            if not info:
                return LoadResult(
                    success=False,
                    errors=[f"Integration {name} not found"],
                )
            
            # Check dependencies first
            deps_loaded, dep_errors = self._load_dependencies(name, info)
            if dep_errors:
                return LoadResult(
                    success=False,
                    errors=dep_errors,
                    dependencies_loaded=deps_loaded,
                )
            
            # Check for conflicts
            conflict_errors = self._check_conflicts(name, info)
            if conflict_errors:
                return LoadResult(
                    success=False,
                    errors=conflict_errors,
                    dependencies_loaded=deps_loaded,
                )
            
            # Create config
            integration_config = self._create_config(name, info, config)
            
            # Create instance
            integration_class = self.registry.get_integration_class(name)
            if not integration_class:
                return LoadResult(
                    success=False,
                    errors=[f"Integration class for {name} not found"],
                    dependencies_loaded=deps_loaded,
                )
            
            # Instantiate the integration
            instance = integration_class(integration_config)
            
            # Initialize the instance
            try:
                initialized = instance.initialize()
            except Exception as e:
                return LoadResult(
                    success=False,
                    errors=[f"Failed to initialize {name}: {e}"],
                    dependencies_loaded=deps_loaded,
                )
            if not initialized:
                return LoadResult(
                    success=False,
                    errors=[f"Integration {name} reported initialization failure"],
                    dependencies_loaded=deps_loaded,
                )
            
            # Create integration instance wrapper
            integration_instance = IntegrationInstance(
                info=info,
                instance=instance,
                config=integration_config,
                status=_get_integration_status_class().LOADED,
            )
            
            # Store the loaded instance
            self._loaded[name] = integration_instance
            self._load_errors.pop(name, None)
            
            # Run health check
            try:
                integration_instance.health, integration_instance.health_message = \
                    instance.check_health()
            except Exception as e:
                integration_instance.health = "unhealthy"
                integration_instance.health_message = str(e)
            
            # Log success
            logger.info(f"Loaded integration {name} v{info.version}")
            
            return LoadResult(
                success=True,
                instance=integration_instance,
                dependencies_loaded=deps_loaded,
            )
            
        except Exception as e:
            error = LoadError(
                integration_name=name,
                error_type=type(e).__name__,
                message=str(e),
                traceback=str(e.__traceback__) if hasattr(e, '__traceback__') else None,
            )
            self._load_errors[name] = error
            return LoadResult(
                success=False,
                errors=[str(e)],
                dependencies_loaded=deps_loaded if 'deps_loaded' in locals() else [],
            )
        finally:
            self._loading.discard(name)
    
    def _get_integration_info(self, name: str) -> Any:
        """Get integration info, trying multiple sources."""
        _get_integration_info_class()
        
        # Try registry first
        info = self.registry.get_integration_info(name)
        if info:
            return info
        
        # Try discovery
        if self._discovery:
            info = self._discovery.get_integration_info(name)
            if info:
                return info
        
        return None
    
    def _create_config(
        self,
        name: str,
        info: Any,
        override_config: dict[str, Any] | None = None,
    ) -> Any:
        """Create an IntegrationConfig for an integration."""
        IntegrationConfig = _get_integration_config_class()
        IntegrationInfo = _get_integration_info_class()
        
        # Get config from various sources
        config = {}
        
        # 1. Default config from integration info
        if isinstance(info, IntegrationInfo) and info.default_config:
            config.update(info.default_config)
        
        # 2. Config from discovery (if available)
        if self._discovery:
            discovered_info = self._discovery.get_integration_info(name)
            if discovered_info and discovered_info.metadata.get("config"):
                config.update(discovered_info.metadata["config"])
        
        # 3. Config from integrations config
        if self.config:
            try:
                integrations_config = getattr(self.config, 'integrations', None)
                if integrations_config:
                    configs = getattr(integrations_config, 'configs', {})
                    if isinstance(configs, dict) and name in configs:
                        config.update(configs[name])
            except Exception:
                pass
        
        # 4. Override config
        if override_config:
            config.update(override_config)
        
        # Create IntegrationConfig
        return IntegrationConfig(
            name=name,
            integration_id=name,
            enabled=True,
            config=config,
        )
    
    def _load_dependencies(self, name: str, info: Any) -> tuple[list[str], list[str]]:
        """Verify the integration's declared dependencies are importable.

        Dependencies are third-party *packages* (``requests``, ``psycopg2``,
        ``stripe``...), not SkeletonKey integrations, so they are checked for
        importability rather than loaded through this loader. Optional
        dependencies are recorded when present and ignored when absent.
        """
        loaded: list[str] = []
        errors: list[str] = []
        
        for dep in getattr(info, 'dependencies', []):
            dep_name = _dep_name(dep)
            if not dep_name:
                continue
            if importlib.util.find_spec(dep_name) is not None:
                loaded.append(dep_name)
            elif not _dep_optional(dep):
                errors.append(f"Missing required dependency {dep_name} for {name}")
        
        return loaded, errors
    
    def _check_conflicts(self, name: str, info: Any) -> list[str]:
        """Check for conflicts with already loaded integrations."""
        errors = []
        own_conflicts = {_dep_name(c) for c in getattr(info, 'conflicts', [])}
        
        for loaded_name, loaded_instance in self._loaded.items():
            loaded_info = loaded_instance.info
            
            # Check for conflicts
            if loaded_name in own_conflicts:
                errors.append(
                    f"Conflict: {name} conflicts with already loaded integration {loaded_name}"
                )
            
            # Check reverse conflicts
            for conflict in getattr(loaded_info, 'conflicts', []):
                if _dep_name(conflict) == name:
                    errors.append(
                        f"Conflict: {loaded_name} conflicts with {name}"
                    )
        
        return errors
    
    def unload(self, name: str) -> bool:
        """Unload an integration.
        
        Args:
            name: Integration name
            
        Returns:
            bool: True if unloaded successfully
        """
        if name not in self._loaded:
            return False
        
        instance = self._loaded[name]
        
        try:
            # Clean up the integration instance
            instance.instance.cleanup()
            
            # Remove from loaded
            del self._loaded[name]
            
            # Log success
            logger.info(f"Unloaded integration {name}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to unload {name}: {e}")
            return False
    
    def reload(self, name: str, config: dict[str, Any] | None = None) -> LoadResult:
        """Reload an integration.
        
        Args:
            name: Integration name
            config: Optional configuration for the integration
            
        Returns:
            LoadResult: Result of the reload operation
        """
        # Unload first
        if name in self._loaded:
            self.unload(name)
        
        # Load again
        return self.load(name, config)
    
    def is_loaded(self, name: str) -> bool:
        """Check if an integration is loaded."""
        return name in self._loaded
    
    def is_loading(self, name: str) -> bool:
        """Check if an integration is currently loading."""
        return name in self._loading
    
    def get_loaded(self, name: str) -> Any:
        """Get a loaded integration instance."""
        return self._loaded.get(name)
    
    def get_all_loaded(self) -> dict[str, Any]:
        """Get all loaded integration instances."""
        return dict(self._loaded)
    
    def get_load_errors(self, name: str | None = None) -> list[LoadError]:
        """Get load errors for an integration or all integrations."""
        if name:
            error = self._load_errors.get(name)
            return [error] if error else []
        return list(self._load_errors.values())
    
    def clear_load_errors(self, name: str | None = None) -> None:
        """Clear load errors."""
        if name:
            self._load_errors.pop(name, None)
        else:
            self._load_errors.clear()
    
    def load_from_module(self, module_name: str) -> LoadResult:
        """Load an integration from a Python module."""
        try:
            # Import the module
            module = importlib.import_module(module_name)
            
            # Look for integration classes
            Integration = _get_integration_class()
            IntegrationInfo = _get_integration_info_class()
            
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Integration):
                    # Get integration info
                    if hasattr(attr, 'get_info'):
                        info = attr.get_info()
                    elif hasattr(attr, 'INFO'):
                        info = attr.INFO
                    else:
                        try:
                            instance = attr()
                            info = instance.info
                        except Exception:
                            continue
                    
                    if isinstance(info, IntegrationInfo):
                        # Register with registry if not already registered
                        if not self.registry.has_integration(info.name):
                            self.registry.register_integration(attr, info)
                        
                        # Load the integration
                        return self.load(info.name)
            
            return LoadResult(
                success=False,
                errors=[f"No integration found in module {module_name}"],
            )
            
        except Exception as e:
            return LoadResult(
                success=False,
                errors=[f"Failed to import module {module_name}: {e}"],
            )
    
    def load_from_path(self, path: str | Path) -> LoadResult:
        """Load an integration from a filesystem path."""
        path = Path(path)
        
        if path.is_dir():
            # Try to find integration files in directory
            for pattern in ["*.py", "*_integration.py", "integration.py"]:
                for file_path in path.glob(pattern):
                    if file_path.is_file():
                        result = self._load_from_file(file_path)
                        if result.success:
                            return result
            
            return LoadResult(
                success=False,
                errors=[f"No integration found in directory {path}"],
            )
        if path.is_file():
            return self._load_from_file(path)
        return LoadResult(
            success=False,
            errors=[f"Path does not exist: {path}"],
        )
    
    def _load_from_file(self, path: Path) -> LoadResult:
        """Load an integration from a Python file."""
        Integration = _get_integration_class()
        IntegrationInfo = _get_integration_info_class()
        
        try:
            # Get module name
            module_name = path.stem
            if module_name.endswith("_integration"):
                module_name = module_name[:-12]
            
            # Add parent directory to sys.path temporarily
            parent_dir = str(path.parent.absolute())
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            try:
                # Import the module
                spec = importlib.util.spec_from_file_location(module_name, str(path))
                if not spec or not spec.loader:
                    return LoadResult(
                        success=False,
                        errors=[f"Cannot create spec for {path}"],
                    )
                
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                # Look for integration classes
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, Integration):
                        # Get integration info
                        if hasattr(attr, 'get_info'):
                            info = attr.get_info()
                        elif hasattr(attr, 'INFO'):
                            info = attr.INFO
                        else:
                            try:
                                instance = attr()
                                info = instance.info
                            except Exception:
                                continue
                        
                        if isinstance(info, IntegrationInfo):
                            # Register with registry if not already registered
                            if not self.registry.has_integration(info.name):
                                self.registry.register_integration(attr, info)
                            
                            # Load the integration
                            return self.load(info.name)
                
                return LoadResult(
                    success=False,
                    errors=[f"No integration class found in {path}"],
                )
                
            finally:
                # Clean up sys.path
                if parent_dir in sys.path:
                    sys.path.remove(parent_dir)
                    
        except Exception as e:
            return LoadResult(
                success=False,
                errors=[f"Failed to load from {path}: {e}"],
            )
    
    def get_statistics(self) -> dict[str, Any]:
        """Get loader statistics."""
        by_type = {}
        by_category = {}
        by_platform = {}
        
        for _name, instance in self._loaded.items():
            info = instance.info
            
            # Count by type
            type_name = info.integration_type.value
            by_type[type_name] = by_type.get(type_name, 0) + 1
            
            # Count by category
            if info.category:
                cat_name = info.category.value
                by_category[cat_name] = by_category.get(cat_name, 0) + 1
            
            # Count by platform
            if info.platform:
                plat_name = info.platform.value
                by_platform[plat_name] = by_platform.get(plat_name, 0) + 1
        
        return {
            "loaded_count": len(self._loaded),
            "loading_count": len(self._loading),
            "error_count": len(self._load_errors),
            "by_type": by_type,
            "by_category": by_category,
            "by_platform": by_platform,
            "loaded_names": list(self._loaded.keys()),
        }


# Global loader instance
_loader: IntegrationLoader | None = None


def get_loader(registry: Any, config: Any = None, discovery: Any = None) -> IntegrationLoader:
    """Get the global loader instance."""
    global _loader
    
    if _loader is None:
        _loader = IntegrationLoader(registry, config, discovery)
    
    return _loader
