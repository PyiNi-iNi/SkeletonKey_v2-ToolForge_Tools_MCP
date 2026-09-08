# P5.3 Plan - Integration Discovery & Dynamic Loading

## Overview

Phase P5.3 focuses on implementing integration discovery and dynamic loading capabilities. This phase will enable SkeletonKey to automatically discover integrations from various sources and load them dynamically at runtime without requiring a restart.

## Goals

1. **Discovery**: Automatically discover integrations from multiple sources
2. **Dynamic Loading**: Load and unload integrations at runtime
3. **Pack Management**: Install, upgrade, and manage integration packs
4. **Dependency Resolution**: Resolve integration dependencies automatically
5. **Conflict Detection**: Detect and resolve integration conflicts

## Deliverables

### 1. Discovery System

**File:** `skeletonkey/integrations/discovery.py`

**Features:**
- [ ] Discover integrations from Python entry points (`skeletonkey.integrations`)
- [ ] Discover integrations from filesystem directories
- [ ] Discover integrations from installed Python packages
- [ ] Discover integrations from configuration files
- [ ] Support for lazy discovery (discover on first access)
- [ ] Support for eager discovery (discover all at startup)
- [ ] Discovery caching for performance
- [ ] Discovery error handling and reporting

**Classes:**
```python
class IntegrationDiscovery:
    """Discovers integrations from various sources."""
    
    def __init__(self, config: Config):
        self.config = config
        self._cache: dict[str, IntegrationInfo] = {}
        self._sources: list[DiscoverySource] = []
    
    def discover(self, refresh: bool = False) -> list[IntegrationInfo]:
        """Discover all available integrations."""
        
    def discover_from_entry_points(self) -> list[IntegrationInfo]:
        """Discover integrations from Python entry points."""
        
    def discover_from_filesystem(self, paths: list[str]) -> list[IntegrationInfo]:
        """Discover integrations from filesystem directories."""
        
    def discover_from_packages(self) -> list[IntegrationInfo]:
        """Discover integrations from installed Python packages."""
        
    def discover_from_config(self) -> list[IntegrationInfo]:
        """Discover integrations from configuration files."""
        
    def register_source(self, source: DiscoverySource) -> None:
        """Register a custom discovery source."""
        
    def invalidate_cache(self) -> None:
        """Invalidate the discovery cache."""

@dataclass
class DiscoverySource:
    """A source for integration discovery."""
    name: str
    priority: int = 0
    enabled: bool = True
    
    def discover(self) -> list[IntegrationInfo]:
        """Discover integrations from this source."""
        raise NotImplementedError
```

### 2. Dynamic Loader

**File:** `skeletonkey/integrations/loader.py`

**Features:**
- [ ] Load integrations from Python modules
- [ ] Load integrations from entry points
- [ ] Load integrations from filesystem paths
- [ ] Support for lazy loading (load on first use)
- [ ] Support for eager loading (load all at startup)
- [ ] Loading error handling and reporting
- [ ] Loading dependency resolution
- [ ] Loading conflict detection
- [ ] Loading validation

**Classes:**
```python
class IntegrationLoader:
    """Loads integrations dynamically at runtime."""
    
    def __init__(self, registry: IntegrationRegistry, config: Config):
        self.registry = registry
        self.config = config
        self._loaded: set[str] = set()
        self._loading: set[str] = set()
    
    def load(self, name: str | IntegrationInfo) -> IntegrationInstance | None:
        """Load an integration by name or info."""
        
    def load_from_module(self, module_name: str) -> IntegrationInstance | None:
        """Load an integration from a Python module."""
        
    def load_from_entry_point(self, entry_point: str) -> IntegrationInstance | None:
        """Load an integration from an entry point."""
        
    def load_from_path(self, path: str | Path) -> IntegrationInstance | None:
        """Load an integration from a filesystem path."""
        
    def unload(self, name: str) -> bool:
        """Unload an integration."""
        
    def reload(self, name: str) -> IntegrationInstance | None:
        """Reload an integration."""
        
    def is_loaded(self, name: str) -> bool:
        """Check if an integration is loaded."""
        
    def is_loading(self, name: str) -> bool:
        """Check if an integration is currently loading."""
        
    def get_loading_errors(self, name: str) -> list[str]:
        """Get loading errors for an integration."""
```

### 3. Pack Management

**File:** `skeletonkey/integrations/packs.py`

**Features:**
- [ ] Install integration packs from local directories
- [ ] Install integration packs from git repositories
- [ ] Install integration packs from PyPI
- [ ] Upgrade integration packs
- [ ] Uninstall integration packs
- [ ] List installed integration packs
- [ ] Search for available integration packs
- [ ] Pack dependency resolution
- [ ] Pack conflict detection
- [ ] Pack version management

**Classes:**
```python
class IntegrationPackManager:
    """Manages integration packs."""
    
    def __init__(self, config: Config):
        self.config = config
        self._installed: dict[str, IntegrationPackInfo] = {}
        self._sources: list[PackSource] = []
    
    def install(self, name: str, source: str | None = None, version: str | None = None) -> bool:
        """Install an integration pack."""
        
    def uninstall(self, name: str) -> bool:
        """Uninstall an integration pack."""
        
    def upgrade(self, name: str, version: str | None = None) -> bool:
        """Upgrade an integration pack."""
        
    def list_installed(self) -> list[IntegrationPackInfo]:
        """List installed integration packs."""
        
    def search(self, query: str) -> list[IntegrationPackInfo]:
        """Search for available integration packs."""
        
    def get_info(self, name: str) -> IntegrationPackInfo | None:
        """Get information about an integration pack."""
        
    def check_updates(self) -> list[IntegrationPackInfo]:
        """Check for available updates."""
        
    def validate_pack(self, path: str | Path) -> list[str]:
        """Validate an integration pack."""

@dataclass
class IntegrationPackInfo:
    """Information about an integration pack."""
    name: str
    version: str
    source: str
    path: str
    integrations: list[str]
    dependencies: list[str]
    conflicts: list[str]
    installed_at: str
    updated_at: str
```

### 4. Dependency Resolution

**File:** `skeletonkey/integrations/dependencies.py`

**Features:**
- [ ] Resolve integration dependencies
- [ ] Detect dependency cycles
- [ ] Check dependency compatibility
- [ ] Install missing dependencies
- [ ] Dependency version resolution
- [ ] Dependency conflict detection

**Classes:**
```python
class DependencyResolver:
    """Resolves integration dependencies."""
    
    def __init__(self, registry: IntegrationRegistry):
        self.registry = registry
    
    def resolve(self, names: list[str]) -> dict[str, list[str]]:
        """Resolve dependencies for a list of integration names."""
        
    def get_dependencies(self, name: str) -> list[IntegrationDependency]:
        """Get dependencies for an integration."""
        
    def check_cycles(self, names: list[str]) -> list[list[str]]:
        """Check for dependency cycles."""
        
    def check_compatibility(self, name: str, version: str) -> bool:
        """Check if an integration is compatible with the current environment."""
        
    def install_dependencies(self, name: str) -> bool:
        """Install missing dependencies for an integration."""

class DependencyGraph:
    """Represents a dependency graph."""
    
    def __init__(self):
        self._graph: dict[str, list[str]] = {}
    
    def add_dependency(self, name: str, depends_on: str) -> None:
        """Add a dependency to the graph."""
        
    def get_dependencies(self, name: str) -> list[str]:
        """Get direct dependencies of a node."""
        
    def get_all_dependencies(self, name: str) -> list[str]:
        """Get all transitive dependencies of a node."""
        
    def find_cycles(self) -> list[list[str]]:
        """Find all cycles in the graph."""
        
    def topological_sort(self) -> list[str]:
        """Get a topological sort of the graph."""
```

### 5. Conflict Detection

**File:** `skeletonkey/integrations/conflicts.py`

**Features:**
- [ ] Detect integration conflicts
- [ ] Conflict resolution strategies
- [ ] Conflict reporting
- [ ] Conflict avoidance

**Classes:**
```python
class ConflictDetector:
    """Detects integration conflicts."""
    
    def __init__(self, registry: IntegrationRegistry):
        self.registry = registry
    
    def detect(self, names: list[str]) -> list[IntegrationConflict]:
        """Detect conflicts between a list of integration names."""
        
    def check_all(self) -> list[IntegrationConflict]:
        """Check all integrations for conflicts."""
        
    def resolve(self, conflicts: list[IntegrationConflict]) -> dict[str, str]:
        """Resolve conflicts with a strategy."""

@dataclass
class IntegrationConflict:
    """Represents a conflict between integrations."""
    integration1: str
    integration2: str
    type: str  # 'capability', 'resource', 'version', 'custom'
    description: str
    severity: str = "error"  # 'error', 'warning', 'info'
    resolution: str | None = None
```

## Integration with Existing Components

### 1. IntegrationManager Enhancements

Enhance `IntegrationManager` to support discovery and dynamic loading:

```python
class IntegrationManager:
    # Existing methods...
    
    def discover_integrations(self, refresh: bool = False) -> list[IntegrationInfo]:
        """Discover all available integrations."""
        
    def load_integration_dynamic(self, name: str) -> IntegrationInstance | None:
        """Load an integration dynamically."""
        
    def unload_integration_dynamic(self, name: str) -> bool:
        """Unload a dynamically loaded integration."""
        
    def install_pack(self, name: str, source: str | None = None) -> bool:
        """Install an integration pack."""
        
    def uninstall_pack(self, name: str) -> bool:
        """Uninstall an integration pack."""
```

### 2. Configuration Enhancements

Add configuration options for discovery and dynamic loading:

```python
@dataclass
class IntegrationConfig:
    # Existing fields...
    
    # Discovery settings
    discovery_enabled: bool = True
    discovery_sources: list[str] = field(default_factory=lambda: ["entry_points", "filesystem", "packages"])
    discovery_cache_enabled: bool = True
    discovery_cache_ttl_s: float = 3600.0
    
    # Dynamic loading settings
    dynamic_loading_enabled: bool = True
    lazy_loading: bool = True
    auto_unload: bool = False
    
    # Pack management settings
    pack_sources: list[str] = field(default_factory=lambda: ["local", "pypi"])
    pack_install_root: str = "integrations"
    auto_update_packs: bool = False
```

### 3. CLI Enhancements

Add CLI commands for discovery and pack management:

```python
# sk integrations discover
@click.command()
@click.option('--refresh', is_flag=True, help='Refresh the discovery cache')
def cmd_discover(refresh: bool):
    """Discover available integrations."""
    
# sk integrations install <name>
@click.command()
@click.argument('name')
@click.option('--source', help='Source for the integration pack')
@click.option('--version', help='Version to install')
def cmd_install(name: str, source: str | None, version: str | None):
    """Install an integration pack."""
    
# sk integrations uninstall <name>
@click.command()
@click.argument('name')
def cmd_uninstall(name: str):
    """Uninstall an integration pack."""
    
# sk integrations upgrade <name>
@click.command()
@click.argument('name')
@click.option('--version', help='Version to upgrade to')
def cmd_upgrade(name: str, version: str | None):
    """Upgrade an integration pack."""
```

### 4. Engine Tool Enhancements

Add engine tools for discovery and pack management:

```python
# integrations.discover
{
    "id": "integrations.discover",
    "title": "Discover Integrations",
    "description": "Discover available integrations from all sources",
    "input_schema": {
        "type": "object",
        "properties": {
            "refresh": {"type": "boolean", "default": false}
        }
    }
}

# integrations.install
{
    "id": "integrations.install",
    "title": "Install Integration Pack",
    "description": "Install an integration pack",
    "risk": "write",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "source": {"type": "string"},
            "version": {"type": "string"}
        },
        "required": ["name"]
    }
}

# integrations.uninstall
{
    "id": "integrations.uninstall",
    "title": "Uninstall Integration Pack",
    "description": "Uninstall an integration pack",
    "risk": "destructive",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"}
        },
        "required": ["name"]
    }
}

# integrations.upgrade
{
    "id": "integrations.upgrade",
    "title": "Upgrade Integration Pack",
    "description": "Upgrade an integration pack",
    "risk": "write",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "version": {"type": "string"}
        },
        "required": ["name"]
    }
}

# integrations.check_updates
{
    "id": "integrations.check_updates",
    "title": "Check for Updates",
    "description": "Check for available integration pack updates",
    "input_schema": {
        "type": "object",
        "properties": {}
    }
}
```

## Implementation Timeline

### Week 1: Discovery System
- [ ] Implement `IntegrationDiscovery` class
- [ ] Implement discovery sources (entry points, filesystem, packages, config)
- [ ] Add discovery caching
- [ ] Add discovery error handling
- [ ] Write tests for discovery

### Week 2: Dynamic Loader
- [ ] Implement `IntegrationLoader` class
- [ ] Implement lazy and eager loading
- [ ] Add loading error handling
- [ ] Add dependency resolution for loading
- [ ] Add conflict detection for loading
- [ ] Write tests for dynamic loading

### Week 3: Pack Management
- [ ] Implement `IntegrationPackManager` class
- [ ] Implement pack installation from various sources
- [ ] Implement pack upgrade and uninstall
- [ ] Add pack dependency resolution
- [ ] Add pack conflict detection
- [ ] Write tests for pack management

### Week 4: Integration and Testing
- [ ] Integrate discovery with IntegrationManager
- [ ] Integrate dynamic loading with IntegrationManager
- [ ] Integrate pack management with IntegrationManager
- [ ] Add CLI commands for new features
- [ ] Add engine tools for new features
- [ ] Write integration tests
- [ ] Fix any issues found during testing

## Testing Strategy

### Unit Tests
- Test discovery from each source
- Test dynamic loading of integrations
- Test pack installation and uninstallation
- Test dependency resolution
- Test conflict detection

### Integration Tests
- Test discovery + loading workflow
- Test pack installation + loading workflow
- Test dependency resolution + loading workflow
- Test conflict detection + loading workflow

### End-to-End Tests
- Test full workflow from discovery to loading to using integrations
- Test pack installation from various sources
- Test upgrade workflow
- Test error handling and recovery

## Documentation

### User Documentation
- [ ] Discovery configuration guide
- [ ] Dynamic loading guide
- [ ] Pack management guide
- [ ] Dependency management guide
- [ ] Conflict resolution guide

### Developer Documentation
- [ ] Discovery source development guide
- [ ] Dynamic loader development guide
- [ ] Pack development guide
- [ ] Dependency resolver development guide
- [ ] Conflict detector development guide

### API Documentation
- [ ] Discovery API reference
- [ ] Dynamic loading API reference
- [ ] Pack management API reference
- [ ] Dependency resolution API reference
- [ ] Conflict detection API reference

## Success Criteria

1. **Discovery**: All registered integrations can be discovered from all configured sources
2. **Dynamic Loading**: Integrations can be loaded and unloaded at runtime without restart
3. **Pack Management**: Integration packs can be installed, upgraded, and uninstalled
4. **Dependency Resolution**: Dependencies are automatically resolved and installed
5. **Conflict Detection**: Conflicts are automatically detected and reported
6. **Performance**: Discovery and loading operations complete within acceptable time limits
7. **Reliability**: All operations handle errors gracefully and provide meaningful error messages
8. **Security**: All operations respect security constraints and access controls

## Dependencies

### Python Packages
- `importlib_metadata` (for entry point discovery)
- `packaging` (for version comparison)
- `requests` (for remote pack sources)

### Internal Dependencies
- `skeletonkey.core.config`
- `skeletonkey.integrations`
- `skeletonkey.core.registry`
- `skeletonkey.core.engine`

## Risks and Mitigations

### Risk 1: Performance Issues
- **Impact**: Slow discovery or loading could affect user experience
- **Mitigation**: Implement caching, lazy loading, and background operations

### Risk 2: Dependency Conflicts
- **Impact**: Conflicting dependencies could prevent integrations from loading
- **Mitigation**: Implement comprehensive dependency resolution and conflict detection

### Risk 3: Security Vulnerabilities
- **Impact**: Dynamic loading could introduce security risks
- **Mitigation**: Implement sandboxing, access controls, and validation

### Risk 4: Compatibility Issues
- **Impact**: Integrations might not be compatible with the current environment
- **Mitigation**: Implement compatibility checking and clear error messages

### Risk 5: Complexity
- **Impact**: The system could become too complex to maintain
- **Mitigation**: Keep interfaces simple, document thoroughly, and write comprehensive tests

## Next Steps

1. **Start Implementation**: Begin with the discovery system
2. **Iterative Development**: Implement each component incrementally
3. **Continuous Testing**: Test each component as it's implemented
4. **Integration**: Integrate components with existing system
5. **Documentation**: Document each component as it's implemented

---

**Status:** 🟡 PLANNED
**Start Date:** 2026-09-07 (After P5.2 completion)
**Estimated Duration:** 4 weeks
**Priority:** High
