"""Integration Discovery System for SkeletonKey (P5.3).

This module provides automatic discovery of integrations from various sources:
- Python entry points (skeletonkey.integrations)
- Filesystem directories
- Installed Python packages
- Configuration files

The discovery system supports both eager (at startup) and lazy (on-demand) discovery,
with caching for performance optimization.

To avoid circular imports, this module uses lazy imports for IntegrationInfo and
other types from the parent module.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

# Type checking imports - these are only used for type hints
if TYPE_CHECKING:
    # These imports are safe because they're only evaluated during type checking
    from . import IntegrationInfo, IntegrationType

logger = logging.getLogger(__name__)


def _get_integration_info_class():
    """Lazy import of IntegrationInfo to avoid circular imports."""
    from . import IntegrationInfo
    return IntegrationInfo


def _get_integration_type_class():
    """Lazy import of IntegrationType to avoid circular imports."""
    from . import IntegrationType
    return IntegrationType


@dataclass
class DiscoveryResult:
    """Result of a discovery operation."""
    integrations: list  # list of IntegrationInfo objects
    errors: list[str]
    warnings: list[str]
    source: str
    
    @property
    def count(self) -> int:
        """Get the count of discovered integrations."""
        return len(self.integrations)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary, importing IntegrationInfo only when needed."""
        IntegrationInfo = _get_integration_info_class()
        return {
            "integrations": [i.to_dict() for i in self.integrations],
            "errors": self.errors,
            "warnings": self.warnings,
            "source": self.source,
            "count": len(self.integrations),
        }


@dataclass
class DiscoverySource(ABC):
    """Abstract base class for discovery sources."""
    name: str
    priority: int = 0
    enabled: bool = True
    
    @abstractmethod
    def discover(self) -> DiscoveryResult:
        """Discover integrations from this source."""
        pass
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, priority={self.priority}, enabled={self.enabled})"


@dataclass
class EntryPointDiscoverySource(DiscoverySource):
    """Discovers integrations from Python entry points."""
    entry_point_group: str = "skeletonkey.integrations"
    
    def discover(self) -> DiscoveryResult:
        """Discover integrations from Python entry points."""
        integrations = []
        errors = []
        warnings = []
        
        try:
            # Try importlib.metadata first (Python 3.8+)
            try:
                from importlib.metadata import entry_points
                # Python 3.10+ returns a dict-like object, earlier versions return a list
                eps = entry_points()
                if hasattr(eps, 'select'):
                    # Python 3.10+
                    group_eps = eps.select(group=self.entry_point_group)
                else:
                    # Python 3.8-3.9
                    group_eps = eps.get(self.entry_point_group, [])
            except ImportError:
                # Fallback for older Python versions
                try:
                    from importlib_metadata import entry_points
                    group_eps = entry_points().get(self.entry_point_group, [])
                except ImportError:
                    errors.append("importlib_metadata not available")
                    return DiscoveryResult([], errors, warnings, self.name)
            
            for ep in group_eps:
                try:
                    # Load the entry point
                    integration_class = ep.load()
                    
                    # Get integration info
                    IntegrationInfo = _get_integration_info_class()
                    if hasattr(integration_class, 'get_info'):
                        info = integration_class.get_info()
                    elif hasattr(integration_class, 'INFO'):
                        info = integration_class.INFO
                    else:
                        # Try to instantiate and get info
                        try:
                            instance = integration_class()
                            info = instance.info
                        except Exception as e:
                            errors.append(f"Failed to get info from {ep.name}: {e}")
                            continue
                    
                    if isinstance(info, IntegrationInfo):
                        integrations.append(info)
                    else:
                        warnings.append(f"Entry point {ep.name} returned non-IntegrationInfo")
                        
                except Exception as e:
                    errors.append(f"Failed to load entry point {ep.name}: {e}")
                    logger.warning(f"Failed to load entry point {ep.name}: {e}")
                    
        except Exception as e:
            errors.append(f"Entry point discovery failed: {e}")
            logger.error(f"Entry point discovery failed: {e}")
        
        return DiscoveryResult(integrations, errors, warnings, self.name)


@dataclass
class FilesystemDiscoverySource(DiscoverySource):
    """Discovers integrations from filesystem directories."""
    paths: list[str] = field(default_factory=lambda: ["integrations", "skeletonkey/integrations"])
    recursive: bool = True
    pattern: str = "*.py"  # Look for all Python files, we'll filter by content
    
    def discover(self) -> DiscoveryResult:
        """Discover integrations from filesystem directories."""
        integrations = []
        errors = []
        warnings = []
        
        IntegrationInfo = _get_integration_info_class()
        IntegrationType = _get_integration_type_class()
        
        for base_path in self.paths:
            path = Path(base_path)
            if not path.exists():
                warnings.append(f"Path does not exist: {base_path}")
                continue
            
            if path.is_file():
                # Single file
                try:
                    self._load_from_file(path, integrations, errors, warnings)
                except Exception as e:
                    errors.append(f"Failed to load {path}: {e}")
            elif path.is_dir():
                # Directory - search for integration files
                if self.recursive:
                    files = path.rglob(self.pattern)
                else:
                    files = path.glob(self.pattern)
                
                for file_path in files:
                    if file_path.is_file():
                        try:
                            self._load_from_file(file_path, integrations, errors, warnings)
                        except Exception as e:
                            errors.append(f"Failed to load {file_path}: {e}")
        
        return DiscoveryResult(integrations, errors, warnings, self.name)
    
    def _load_from_file(
        self,
        path: Path,
        integrations: list,
        errors: list[str],
        warnings: list[str],
    ) -> None:
        """Load an integration from a Python file."""
        IntegrationInfo = _get_integration_info_class()
        
        # Get module name from path
        module_name = path.stem
        if module_name.endswith("_integration"):
            module_name = module_name[:-12]  # Remove _integration suffix
        
        # Add parent directory to sys.path temporarily
        parent_dir = str(path.parent.absolute())
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
        
        try:
            # Import the module
            spec = importlib.util.spec_from_file_location(module_name, str(path))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                # Look for integration classes
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if self._is_integration_class(attr):
                        try:
                            if hasattr(attr, 'get_info'):
                                info = attr.get_info()
                            elif hasattr(attr, 'INFO'):
                                info = attr.INFO
                            else:
                                # Try to instantiate
                                try:
                                    instance = attr()
                                    info = instance.info
                                except Exception:
                                    continue
                            
                            if isinstance(info, IntegrationInfo):
                                integrations.append(info)
                        except Exception as e:
                            errors.append(f"Failed to get info from {attr_name} in {path}: {e}")
        except Exception as e:
            errors.append(f"Failed to import {path}: {e}")
        finally:
            # Clean up sys.path
            if parent_dir in sys.path:
                sys.path.remove(parent_dir)
    
    def _is_integration_class(self, obj: Any) -> bool:
        """Check if an object is an integration class."""
        from . import Integration
        
        # Check if it's a class
        if not isinstance(obj, type):
            return False
        
        # Check if it's a subclass of Integration
        try:
            return issubclass(obj, Integration)
        except TypeError:
            return False


@dataclass
class PackageDiscoverySource(DiscoverySource):
    """Discovers integrations from installed Python packages."""
    package_prefix: str = "skeletonkey-integration-"
    
    def discover(self) -> DiscoveryResult:
        """Discover integrations from installed Python packages."""
        integrations = []
        errors = []
        warnings = []
        
        IntegrationInfo = _get_integration_info_class()
        
        try:
            import pkg_resources
            
            # Find all installed packages
            for dist in pkg_resources.working_set:
                name = dist.key
                
                # Check if it's a skeletonkey integration package
                if name.startswith(self.package_prefix):
                    integration_name = name[len(self.package_prefix):]
                    
                    try:
                        # Import the package
                        module = __import__(name)
                        
                        # Look for integration classes
                        for attr_name in dir(module):
                            attr = getattr(module, attr_name)
                            if self._is_integration_class(attr):
                                try:
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
                                        info.metadata["source"] = "package"
                                        info.metadata["package"] = name
                                        integrations.append(info)
                                except Exception as e:
                                    errors.append(f"Failed to get info from {attr_name} in {name}: {e}")
                    except Exception as e:
                        errors.append(f"Failed to import package {name}: {e}")
                        
        except ImportError:
            warnings.append("pkg_resources not available, skipping package discovery")
        except Exception as e:
            errors.append(f"Package discovery failed: {e}")
        
        return DiscoveryResult(integrations, errors, warnings, self.name)
    
    def _is_integration_class(self, obj: Any) -> bool:
        """Check if an object is an integration class."""
        from . import Integration
        
        if not isinstance(obj, type):
            return False
        
        try:
            return issubclass(obj, Integration)
        except TypeError:
            return False


@dataclass
class RegistryDiscoverySource(DiscoverySource):
    """Discovers integrations from the registry."""
    
    def discover(self) -> DiscoveryResult:
        """Discover integrations from the registry."""
        integrations = []
        errors = []
        warnings = []
        
        IntegrationInfo = _get_integration_info_class()
        
        try:
            # Import registry here to avoid circular imports
            from . import registry
            
            # Get all registered integrations
            infos = registry.list_integrations()
            integrations.extend(infos)
                
        except Exception as e:
            errors.append(f"Registry discovery failed: {e}")
        
        return DiscoveryResult(integrations, errors, warnings, self.name)


@dataclass
class ConfigDiscoverySource(DiscoverySource):
    """Discovers integrations from configuration files."""
    config_path: str | None = None
    config_key: str = "integrations"
    
    def discover(self) -> DiscoveryResult:
        """Discover integrations from configuration files."""
        integrations = []
        errors = []
        warnings = []
        
        IntegrationInfo = _get_integration_info_class()
        
        try:
            from ..core.config import Config
            
            # Load config
            if self.config_path:
                config = Config.load(path=self.config_path)
            else:
                config = Config.load()
            
            # Get integrations config
            integrations_config = getattr(config, 'integrations', None)
            if not integrations_config:
                warnings.append("No integrations configuration found")
                return DiscoveryResult(integrations, errors, warnings, self.name)
            
            # Get enabled integrations
            enabled = getattr(integrations_config, 'enabled', [])
            disabled = getattr(integrations_config, 'disabled', [])
            
            # Get per-integration configs
            configs = getattr(integrations_config, 'configs', {})
            
            # For each configured integration, try to get info from registry
            from . import registry
            
            for name in enabled + disabled:
                try:
                    info = registry.get_integration_info(name)
                    if info:
                        # Add config if available
                        if name in configs:
                            info.metadata["config"] = configs[name]
                        info.metadata["source"] = "config"
                        info.metadata["configured"] = True
                        integrations.append(info)
                except Exception as e:
                    errors.append(f"Failed to get info for {name}: {e}")
                    
        except Exception as e:
            errors.append(f"Config discovery failed: {e}")
        
        return DiscoveryResult(integrations, errors, warnings, self.name)


class IntegrationDiscovery:
    """Main discovery class that coordinates multiple discovery sources."""
    
    def __init__(self, config: Any = None):
        """Initialize the discovery system.
        
        Args:
            config: SkeletonKey configuration
        """
        self.config = config
        self._sources: list[DiscoverySource] = []
        self._cache: dict = {}  # dict[str, IntegrationInfo]
        self._cache_valid = False
        self._init_sources()
    
    def _init_sources(self) -> None:
        """Initialize discovery sources from config."""
        # Always add entry point discovery
        self._sources.append(EntryPointDiscoverySource(
            name="entry_points",
            priority=100,
        ))
        
        # Add filesystem discovery
        if self.config:
            try:
                integrations_config = getattr(self.config, 'integrations', None)
                if integrations_config:
                    packs_dir = getattr(integrations_config, 'packs_dir', 'integrations')
                else:
                    packs_dir = 'integrations'
            except Exception:
                packs_dir = 'integrations'
        else:
            packs_dir = 'integrations'
        
        self._sources.append(FilesystemDiscoverySource(
            name="filesystem",
            priority=50,
            paths=[packs_dir, "integrations"],
            recursive=True,
        ))
        
        # Add package discovery
        self._sources.append(PackageDiscoverySource(
            name="packages",
            priority=25,
        ))
        
        # Add config discovery
        self._sources.append(ConfigDiscoverySource(
            name="config",
            priority=10,
        ))
        
        # Add registry discovery (highest priority - already registered integrations)
        self._sources.insert(0, RegistryDiscoverySource(
            name="registry",
            priority=200,
        ))
        
        # Sort by priority (descending)
        self._sources.sort(key=lambda s: s.priority, reverse=True)
    
    def add_source(self, source: DiscoverySource) -> None:
        """Add a custom discovery source.
        
        Args:
            source: Discovery source to add
        """
        self._sources.append(source)
        self._sources.sort(key=lambda s: s.priority, reverse=True)
        self._cache_valid = False
    
    def remove_source(self, name: str) -> bool:
        """Remove a discovery source by name.
        
        Args:
            name: Name of the source to remove
            
        Returns:
            bool: True if source was removed
        """
        for i, source in enumerate(self._sources):
            if source.name == name:
                del self._sources[i]
                self._cache_valid = False
                return True
        return False
    
    def discover(self, refresh: bool = False) -> list:
        """Discover all integrations from all sources.
        
        Args:
            refresh: Whether to refresh the cache
            
        Returns:
            list: List of IntegrationInfo objects
        """
        IntegrationInfo = _get_integration_info_class()
        
        if not refresh and self._cache_valid and self._cache:
            return list(self._cache.values())
        
        all_integrations = []
        seen_names = set()
        
        for source in self._sources:
            if not source.enabled:
                continue
            
            try:
                result = source.discover()
                for info in result.integrations:
                    # Avoid duplicates
                    if info.name not in seen_names:
                        all_integrations.append(info)
                        seen_names.add(info.name)
                    else:
                        # Merge metadata
                        for existing in all_integrations:
                            if existing.name == info.name:
                                existing.metadata.update(info.metadata)
                
                # Log errors and warnings
                for error in result.errors:
                    logger.error(f"Discovery error from {source.name}: {error}")
                for warning in result.warnings:
                    logger.warning(f"Discovery warning from {source.name}: {warning}")
                    
            except Exception as e:
                logger.error(f"Discovery source {source.name} failed: {e}")
        
        # Cache results
        self._cache = {info.name: info for info in all_integrations}
        self._cache_valid = True
        
        return all_integrations
    
    def discover_by_source(self, source_name: str) -> list:
        """Discover integrations from a specific source.
        
        Args:
            source_name: Name of the discovery source
            
        Returns:
            list: List of IntegrationInfo objects from that source
        """
        for source in self._sources:
            if source.name == source_name:
                result = source.discover()
                return result.integrations
        
        return []
    
    def get_integration_info(self, name: str) -> Any:
        """Get information about a specific integration.
        
        Args:
            name: Integration name
            
        Returns:
            IntegrationInfo: Info about the integration, or None if not found
        """
        IntegrationInfo = _get_integration_info_class()
        
        if not self._cache_valid:
            self.discover()
        
        return self._cache.get(name)
    
    def has_integration(self, name: str) -> bool:
        """Check if an integration exists.
        
        Args:
            name: Integration name
            
        Returns:
            bool: True if the integration exists
        """
        if not self._cache_valid:
            self.discover()
        
        return name in self._cache
    
    def list_integration_names(self) -> list[str]:
        """List all discovered integration names.
        
        Returns:
            list: List of integration names
        """
        if not self._cache_valid:
            self.discover()
        
        return list(self._cache.keys())
    
    def invalidate_cache(self) -> None:
        """Invalidate the discovery cache."""
        self._cache = {}
        self._cache_valid = False
    
    def get_sources(self) -> list[DiscoverySource]:
        """Get all discovery sources.
        
        Returns:
            list: List of DiscoverySource objects
        """
        return list(self._sources)
    
    def get_statistics(self) -> dict[str, Any]:
        """Get discovery statistics.
        
        Returns:
            dict: Statistics about discovery
        """
        if not self._cache_valid:
            self.discover()
        
        by_type = {}
        by_category = {}
        by_platform = {}
        
        for info in self._cache.values():
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
            "total": len(self._cache),
            "sources": [s.name for s in self._sources],
            "by_type": by_type,
            "by_category": by_category,
            "by_platform": by_platform,
            "cache_valid": self._cache_valid,
        }


# Global discovery instance
_discovery: IntegrationDiscovery | None = None


def get_discovery(config: Any = None) -> IntegrationDiscovery:
    """Get the global discovery instance.
    
    Args:
        config: SkeletonKey configuration (optional)
        
    Returns:
        IntegrationDiscovery: The global discovery instance
    """
    global _discovery
    
    if _discovery is None:
        _discovery = IntegrationDiscovery(config)
    
    return _discovery
