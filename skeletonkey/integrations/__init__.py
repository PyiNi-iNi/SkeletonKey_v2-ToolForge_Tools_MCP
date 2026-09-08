"""SkeletonKey Integrations Framework

This module provides the foundation for integrating external SDKs, APIs, client
libraries, frameworks, toolchains, CLIs, runtimes, protocol bindings, codegen
systems, platform devkits, and domain-specific integrations.

The integration system allows SkeletonKey to:
- Discover and load integrations dynamically
- Manage integration lifecycles
- Validate integration configurations
- Provide health monitoring
- Enable/disable integrations
- Resolve integration dependencies
- Sandbox integrations for security

Integration Types Supported:
- SDKs (Software Development Kits)
- External APIs (REST, GraphQL, gRPC, etc.)
- Client Libraries
- API Generators (OpenAPI, Protobuf, etc.)
- Frameworks (web, mobile, desktop)
- Toolchains (build, test, deploy)
- CLIs (Command Line Interfaces)
- Runtimes (Python, Node.js, etc.)
- Protocol Bindings (HTTP, WebSocket, MQTT, etc.)
- Codegen Systems
- Platform DevKits (AWS, GCP, Azure, Vercel, etc.)
- 3D Modeling (Blender, Three.js, CAD)
- Billing & Payment (Stripe, Paddle, etc.)
- Databases (Postgres, MongoDB, Redis, etc.)
- Agent Harnesses (LangChain, LlamaIndex, CrewAI, etc.)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# P5.3: Discovery and Dynamic Loading
from .discovery import IntegrationDiscovery, get_discovery
from .loader import IntegrationLoader, get_loader
from .packs import IntegrationPackManager, get_pack_manager, PackInfo
from typing import Any, Callable, TypeVar

from ..core.config import Config
from ..core.secrets import SecretStore, SecretValue, get_secret_store
from ..core.util import compact_json, pretty_json

logger = logging.getLogger(__name__)

T = TypeVar('T', bound='Integration')


# =============================================================================
# Enums and Constants
# =============================================================================

class IntegrationType(Enum):
    """Categories of integrations supported by SkeletonKey."""
    SDK = "sdk"
    API = "api"
    CLIENT_LIBRARY = "client-library"
    API_GENERATOR = "api-generator"
    FRAMEWORK = "framework"
    TOOLCHAIN = "toolchain"
    CLI = "cli"
    RUNTIME = "runtime"
    PROTOCOL_BINDING = "protocol-binding"
    CODEGEN = "codegen"
    PLATFORM_DEVKIT = "platform-devkit"
    MODELING_3D = "3d-modeling"
    BILLING_PAYMENT = "billing-payment"
    DATABASE = "database"
    AGENT_HARNESS = "agent-harness"
    INTEGRATION_PACK = "integration-pack"


class IntegrationCategory(Enum):
    """High-level categories for grouping integrations."""
    CLOUD = "cloud"
    DATABASE = "database"
    AI_ML = "ai-ml"
    WEB = "web"
    MOBILE = "mobile"
    DESKTOP = "desktop"
    SERVERLESS = "serverless"
    DEVTOOLS = "devtools"
    MONITORING = "monitoring"
    SECURITY = "security"
    PAYMENT = "payment"
    GRAPHICS_3D = "3d-graphics"
    COLLABORATION = "collaboration"
    STORAGE = "storage"
    NETWORKING = "networking"
    MESSAGING = "messaging"
    ANALYTICS = "analytics"
    OTHER = "other"


class IntegrationPlatform(Enum):
    """Target platforms for integrations."""
    CROSS_PLATFORM = "cross-platform"
    # Cloud platforms
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"
    VERCEL = "vercel"
    SUPABASE = "supabase"
    FIREBASE = "firebase"
    CLOUDFLARE = "cloudflare"
    NETLIFY = "netlify"
    RENDER = "render"
    FLYIO = "flyio"
    RAILWAY = "railway"
    HEROKU = "heroku"
    DIGITALOCEAN = "digitalocean"
    # Operating systems
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    IOS = "ios"
    ANDROID = "android"
    # Runtimes
    NODEJS = "nodejs"
    PYTHON = "python"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    CSHARP = "csharp"
    PHP = "php"
    RUBY = "ruby"
    # Browsers
    WEB_BROWSER = "web-browser"


class IntegrationMaturity(Enum):
    """Maturity levels for integrations."""
    EXPERIMENTAL = "experimental"
    ALPHA = "alpha"
    BETA = "beta"
    STABLE = "stable"
    DEPRECATED = "deprecated"


class IntegrationStatus(Enum):
    """Runtime status of an integration."""
    NOT_INSTALLED = "not-installed"
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    LOADING = "loading"
    LOADED = "loaded"
    ERROR = "error"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class IntegrationDependency:
    """Represents a dependency for an integration."""
    name: str
    version: str | None = None
    optional: bool = False
    description: str = ""


@dataclass
class IntegrationCapability:
    """Represents a capability required by an integration."""
    name: str
    description: str = ""
    required: bool = True
    default_granted: bool = False


@dataclass
class IntegrationHealthCheck:
    """Health check configuration for an integration."""
    enabled: bool = True
    interval_seconds: int = 60
    command: str | None = None
    url: str | None = None
    timeout_seconds: int = 5


@dataclass
class IntegrationSandbox:
    """Sandbox configuration for an integration."""
    enabled: bool = True
    filesystem_isolation: bool = False
    network_isolation: bool = False
    allowed_paths: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=list)
    allowed_hosts: list[str] = field(default_factory=list)
    denied_hosts: list[str] = field(default_factory=list)
    resource_limits: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntegrationConfig:
    """Configuration for an integration instance."""
    name: str
    integration_id: str
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)


@dataclass
class IntegrationInfo:
    """Metadata about an integration."""
    name: str
    title: str
    version: str
    description: str
    integration_type: IntegrationType
    category: IntegrationCategory
    platform: IntegrationPlatform | None = None
    author: str | None = None
    author_email: str | None = None
    author_url: str | None = None
    license: str | None = None
    homepage: str | None = None
    repository: str | None = None
    documentation: str | None = None
    keywords: list[str] = field(default_factory=list)
    maturity: IntegrationMaturity = IntegrationMaturity.STABLE
    dependencies: list[IntegrationDependency] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    capabilities: list[IntegrationCapability] = field(default_factory=list)
    sandbox: IntegrationSandbox = field(default_factory=IntegrationSandbox)
    health_check: IntegrationHealthCheck = field(default_factory=IntegrationHealthCheck)
    config_schema: dict[str, Any] | None = None
    default_config: dict[str, Any] = field(default_factory=dict)
    entry_point: str | None = None
    module_name: str | None = None
    class_name: str | None = None
    installation: dict[str, Any] = field(default_factory=dict)
    uninstallation: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "title": self.title,
            "version": self.version,
            "description": self.description,
            "type": self.integration_type.value,
            "category": self.category.value,
            "platform": self.platform.value if self.platform else None,
            "author": self.author,
            "author_email": self.author_email,
            "author_url": self.author_url,
            "license": self.license,
            "homepage": self.homepage,
            "repository": self.repository,
            "documentation": self.documentation,
            "keywords": self.keywords,
            "maturity": self.maturity.value,
            "dependencies": [d.__dict__ for d in self.dependencies],
            "conflicts": self.conflicts,
            "capabilities": [c.__dict__ for c in self.capabilities],
            "sandbox": self.sandbox.__dict__,
            "health_check": self.health_check.__dict__,
            "config_schema": self.config_schema,
            "default_config": self.default_config,
            "entry_point": self.entry_point,
            "module_name": self.module_name,
            "class_name": self.class_name,
            "installation": self.installation,
            "uninstallation": self.uninstallation,
            "environment": self.environment,
            "metadata": self.metadata,
        }


@dataclass
class IntegrationInstance:
    """Represents a loaded instance of an integration."""
    info: IntegrationInfo
    config: IntegrationConfig
    status: IntegrationStatus = IntegrationStatus.NOT_INSTALLED
    error: str | None = None
    health: str = "unknown"
    health_message: str = ""
    instance: Any = None  # The actual loaded integration instance
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "info": self.info.to_dict(),
            "config": {
                "name": self.config.name,
                "integration_id": self.config.integration_id,
                "enabled": self.config.enabled,
                "config": self.config.config,
            },
            "status": self.status.value,
            "error": self.error,
            "health": self.health,
            "health_message": self.health_message,
        }


# =============================================================================
# Integration Base Class
# =============================================================================

class Integration(ABC):
    """Abstract base class for all SkeletonKey integrations.
    
    To create a new integration, subclass this class and implement the
    required methods. Integrations can be registered using the
    @register_integration decorator.
    
    Example:
        @register_integration(
            name="my-integration",
            type=IntegrationType.SDK,
            category=IntegrationCategory.CLOUD,
            version="1.0.0",
            description="My awesome integration"
        )
        class MyIntegration(Integration):
            def __init__(self, config: IntegrationConfig):
                super().__init__(config)
                # Initialize your integration
                
            def initialize(self) -> bool:
                # Initialize the integration
                return True
                
            def cleanup(self) -> None:
                # Clean up resources
                pass
    """
    
    # Class-level metadata (set by decorator)
    _integration_name: str = ""
    _integration_title: str = ""
    _integration_version: str = "1.0.0"
    _integration_type: IntegrationType = IntegrationType.INTEGRATION_PACK
    _integration_category: IntegrationCategory = IntegrationCategory.OTHER
    _integration_platform: IntegrationPlatform | None = None
    _integration_description: str = ""
    _integration_author: str = ""
    _integration_license: str = "MIT"
    _integration_keywords: list[str] = field(default_factory=list)
    _integration_dependencies: list[IntegrationDependency] = field(default_factory=list)
    _integration_capabilities: list[IntegrationCapability] = field(default_factory=list)
    
    def __init__(self, config: IntegrationConfig):
        """Initialize the integration with configuration."""
        self.config = config
        self._initialized = False
        self._error: str | None = None
        
    @property
    def name(self) -> str:
        """Get the integration name."""
        return self._integration_name or self.__class__.__name__.lower()
    
    @property
    def title(self) -> str:
        """Get the integration title."""
        return self._integration_title or self._integration_name
    
    @property
    def version(self) -> str:
        """Get the integration version."""
        return self._integration_version
    
    @property
    def integration_type(self) -> IntegrationType:
        """Get the integration type."""
        return self._integration_type
    
    @property
    def category(self) -> IntegrationCategory:
        """Get the integration category."""
        return self._integration_category
    
    @property
    def description(self) -> str:
        """Get the integration description."""
        return self._integration_description
    
    @property
    def info(self) -> IntegrationInfo:
        """Get the integration metadata."""
        return IntegrationInfo(
            name=self.name,
            title=self.title,
            version=self.version,
            description=self.description,
            integration_type=self.integration_type,
            category=self.category,
            platform=self._integration_platform,
            author=self._integration_author,
            license=self._integration_license,
            keywords=self._integration_keywords,
            dependencies=self._integration_dependencies,
            capabilities=self._integration_capabilities,
        )
    
    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the integration.
        
        Called when the integration is loaded/enabled.
        
        Returns:
            bool: True if initialization succeeded, False otherwise
        """
        pass
    
    @abstractmethod
    def cleanup(self) -> None:
        """Clean up the integration.
        
        Called when the integration is unloaded/disabled.
        Should release any resources held by the integration.
        """
        pass
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the integration.
        
        Returns:
            tuple: (health_status, health_message)
        """
        return ("healthy", "Integration is functioning normally")
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration.
        
        Returns:
            list: List of tool definitions
        """
        return []
    
    def get_config_schema(self) -> dict[str, Any] | None:
        """Get the configuration schema for this integration.
        
        Returns:
            dict: JSON Schema for configuration
        """
        return None
    
    def validate_config(self, config: dict[str, Any]) -> tuple[bool, str]:
        """Validate configuration for this integration.
        
        Args:
            config: Configuration dictionary to validate
            
        Returns:
            tuple: (is_valid, error_message)
        """
        return (True, "")


# =============================================================================
# Registration Decorator
# =============================================================================

class IntegrationRegistry:
    """Registry for all available integrations."""
    
    def __init__(self):
        self._integrations: dict[str, type[Integration]] = {}
        self._instances: dict[str, IntegrationInstance] = {}
    
    def register(
        self,
        integration_class: type[Integration],
        name: str | None = None,
        title: str | None = None,
        version: str = "1.0.0",
        integration_type: IntegrationType = IntegrationType.INTEGRATION_PACK,
        category: IntegrationCategory = IntegrationCategory.OTHER,
        platform: IntegrationPlatform | None = None,
        description: str = "",
        author: str = "",
        license: str = "MIT",
        keywords: list[str] | None = None,
        dependencies: list[IntegrationDependency] | None = None,
        capabilities: list[IntegrationCapability] | None = None,
    ) -> type[Integration]:
        """Register an integration class."""
        # Set class attributes
        integration_class._integration_name = name or integration_class.__name__.lower()
        integration_class._integration_title = title or name or integration_class.__name__
        integration_class._integration_version = version
        integration_class._integration_type = integration_type
        integration_class._integration_category = category
        integration_class._integration_platform = platform
        integration_class._integration_description = description
        integration_class._integration_author = author
        integration_class._integration_license = license
        integration_class._integration_keywords = keywords or []
        integration_class._integration_dependencies = dependencies or []
        integration_class._integration_capabilities = capabilities or []
        
        # Register the class
        self._integrations[integration_class._integration_name] = integration_class
        
        logger.debug(f"Registered integration: {integration_class._integration_name}")
        
        return integration_class
    
    def get_integration_class(self, name: str) -> type[Integration] | None:
        """Get an integration class by name."""
        return self._integrations.get(name)
    
    def get_integration_info(self, name: str) -> IntegrationInfo | None:
        """Get integration metadata by name."""
        integration_class = self._integrations.get(name)
        if integration_class:
            # Create a temporary instance to get info
            dummy_config = IntegrationConfig(
                name=name,
                integration_id=name,
                enabled=True,
            )
            instance = integration_class(dummy_config)
            return instance.info
        return None
    
    def list_integrations(self) -> list[IntegrationInfo]:
        """List all registered integrations."""
        infos = []
        for name, integration_class in self._integrations.items():
            dummy_config = IntegrationConfig(
                name=name,
                integration_id=name,
                enabled=True,
            )
            instance = integration_class(dummy_config)
            infos.append(instance.info)
        return infos
    
    def create_instance(
        self,
        name: str,
        config: IntegrationConfig,
    ) -> IntegrationInstance | None:
        """Create an instance of an integration."""
        integration_class = self._integrations.get(name)
        if not integration_class:
            return None
        
        try:
            instance = integration_class(config)
            return IntegrationInstance(
                info=instance.info,
                config=config,
                status=IntegrationStatus.NOT_INSTALLED,
            )
        except Exception as e:
            logger.error(f"Failed to create integration instance {name}: {e}")
            return None
    
    def load_integration(
        self,
        name: str,
        config: IntegrationConfig,
    ) -> IntegrationInstance | None:
        """Load and initialize an integration."""
        integration_class = self._integrations.get(name)
        if not integration_class:
            logger.error(f"Integration not found: {name}")
            return None
        
        try:
            instance = integration_class(config)
            if instance.initialize():
                return IntegrationInstance(
                    info=instance.info,
                    config=config,
                    status=IntegrationStatus.LOADED,
                    instance=instance,
                )
            else:
                return IntegrationInstance(
                    info=instance.info,
                    config=config,
                    status=IntegrationStatus.ERROR,
                    error="Initialization failed",
                    instance=instance,
                )
        except Exception as e:
            logger.error(f"Failed to load integration {name}: {e}\n{traceback.format_exc()}")
            return IntegrationInstance(
                info=IntegrationInfo(
                    name=name,
                    title=name,
                    version="0.0.0",
                    description="",
                    integration_type=IntegrationType.INTEGRATION_PACK,
                    category=IntegrationCategory.OTHER,
                ),
                config=config,
                status=IntegrationStatus.ERROR,
                error=str(e),
            )
    
    def unload_integration(self, name: str) -> bool:
        """Unload an integration."""
        instance = self._instances.get(name)
        if instance and instance.instance:
            try:
                instance.instance.cleanup()
                del self._instances[name]
                return True
            except Exception as e:
                logger.error(f"Failed to unload integration {name}: {e}")
                return False
        return True
    
    def list_instances(self) -> list[IntegrationInstance]:
        """List all loaded integration instances."""
        return list(self._instances.values())
    
    def get_instance(self, name: str) -> IntegrationInstance | None:
        """Get a loaded integration instance by name."""
        return self._instances.get(name)
    
    def list_loaded_names(self) -> list[str]:
        """Get list of loaded integration names."""
        return list(self._instances.keys())


# Global registry instance
registry = IntegrationRegistry()


def register_integration(
    name: str | None = None,
    title: str | None = None,
    version: str = "1.0.0",
    integration_type: IntegrationType = IntegrationType.INTEGRATION_PACK,
    category: IntegrationCategory = IntegrationCategory.OTHER,
    platform: IntegrationPlatform | None = None,
    description: str = "",
    author: str = "",
    license: str = "MIT",
    keywords: list[str] | None = None,
    dependencies: list[IntegrationDependency] | None = None,
    capabilities: list[IntegrationCapability] | None = None,
) -> Callable[[type[Integration]], type[Integration]]:
    """Decorator to register an integration class.
    
    Args:
        name: Unique name of the integration
        title: Human-readable title
        version: Version string (semver recommended)
        integration_type: Type of integration
        category: Category for grouping
        platform: Target platform
        description: Description of the integration
        author: Author name
        license: License type
        keywords: Tags for searching
        dependencies: List of dependencies
        capabilities: List of required capabilities
        
    Returns:
        Decorator function
        
    Example:
        @register_integration(
            name="my-sdk",
            title="My SDK Integration",
            version="1.0.0",
            integration_type=IntegrationType.SDK,
            category=IntegrationCategory.CLOUD,
            description="Integration with My SDK"
        )
        class MySDKIntegration(Integration):
            def initialize(self) -> bool:
                return True
            
            def cleanup(self) -> None:
                pass
    """
    def decorator(cls: type[Integration]) -> type[Integration]:
        return registry.register(
            cls,
            name=name,
            title=title,
            version=version,
            integration_type=integration_type,
            category=category,
            platform=platform,
            description=description,
            author=author,
            license=license,
            keywords=keywords,
            dependencies=dependencies,
            capabilities=capabilities,
        )
    
    return decorator


# =============================================================================
# Integration Manager
# =============================================================================

class IntegrationManager:
    """Manages the lifecycle of integrations within SkeletonKey.
    
    This class handles:
    - Loading integrations from various sources
    - Managing integration instances
    - Health monitoring
    - Configuration management
    - Dependency resolution
    """
    
    def __init__(self, config: Config | None = None):
        """Initialize the integration manager."""
        self.config = config or Config.load()
        self.registry = registry
        self._instances: dict[str, IntegrationInstance] = {}
        self._enabled_integrations: set[str] = set()
        self._disabled_integrations: set[str] = set()
        self._secret_store: SecretStore | None = None
        self._discovery: IntegrationDiscovery | None = None
        self._loader: IntegrationLoader | None = None
        self._pack_manager: IntegrationPackManager | None = None
        
        # Initialize secret store if configured
        self._init_secret_store()
        
        # P5.3: Initialize discovery and loader
        self._init_discovery()
        self._init_loader()
        self._init_pack_manager()
        
        self._load_enabled_integrations()
    
    def _init_secret_store(self) -> None:
        """Initialize the secret store for integrations."""
        try:
            integrations_config = getattr(self.config, 'integrations', None)
            if integrations_config:
                secrets_dir = getattr(integrations_config, 'secrets_dir', '')
                if secrets_dir:
                    self._secret_store = SecretStore(
                        path=Path(secrets_dir) / "secrets.json"
                    )
                else:
                    # Default to state dir
                    self._secret_store = SecretStore(
                        path=Path(self.config.state.dir) / "integrations" / "secrets.json"
                    )
            else:
                # No integration config, use default
                self._secret_store = SecretStore(
                    path=Path(self.config.state.dir) / "integrations" / "secrets.json"
                )
        except Exception as e:
            logger.warning(f"Failed to initialize secret store: {e}")
            self._secret_store = None
    
    # P5.3: Discovery
    def _init_discovery(self) -> None:
        """Initialize the discovery system."""
        try:
            self._discovery = IntegrationDiscovery(self.config)
            logger.info("Initialized integration discovery")
        except Exception as e:
            logger.warning(f"Failed to initialize discovery: {e}")
            self._discovery = None
    
    def _init_loader(self) -> None:
        """Initialize the dynamic loader."""
        try:
            self._loader = IntegrationLoader(
                registry=self.registry,
                config=self.config,
                discovery=self._discovery,
            )
            logger.info("Initialized integration loader")
        except Exception as e:
            logger.warning(f"Failed to initialize loader: {e}")
            self._loader = None
    
    def _init_pack_manager(self) -> None:
        """Initialize the pack manager."""
        try:
            self._pack_manager = IntegrationPackManager(
                config=self.config,
                loader=self._loader,
            )
            logger.info("Initialized pack manager")
        except Exception as e:
            logger.warning(f"Failed to initialize pack manager: {e}")
            self._pack_manager = None
    
    def _load_enabled_integrations(self) -> None:
        """Load the list of enabled/disabled integrations from config."""
        # Load from config if available
        integrations_config = getattr(self.config, 'integrations', None)
        if integrations_config:
            self._enabled_integrations = set(
                getattr(integrations_config, 'enabled', [])
            )
            self._disabled_integrations = set(
                getattr(integrations_config, 'disabled', [])
            )
            
            # Auto-load enabled integrations if configured
            if getattr(integrations_config, 'auto_load', False):
                for name in self._enabled_integrations:
                    self.load_integration(name)
    
    def list_integrations(
        self,
        type_filter: IntegrationType | None = None,
        category_filter: IntegrationCategory | None = None,
        platform_filter: IntegrationPlatform | None = None,
        enabled_only: bool = False,
        disabled_only: bool = False,
    ) -> list[IntegrationInfo]:
        """List available integrations with optional filtering."""
        infos = self.registry.list_integrations()
        
        filtered = []
        for info in infos:
            # Filter by type
            if type_filter and info.integration_type != type_filter:
                continue
            
            # Filter by category
            if category_filter and info.category != category_filter:
                continue
            
            # Filter by platform
            if platform_filter and info.platform != platform_filter:
                continue
            
            # Filter by enabled/disabled
            if enabled_only and info.name not in self._enabled_integrations:
                continue
            if disabled_only and info.name not in self._disabled_integrations:
                continue
            
            filtered.append(info)
        
        return filtered
    
    def get_integration(self, name: str) -> IntegrationInstance | None:
        """Get a loaded integration instance."""
        return self._instances.get(name)
    
    def load_integration(
        self,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> IntegrationInstance | None:
        """Load and initialize an integration."""
        # Check if already loaded
        if name in self._instances:
            return self._instances[name]
        
        # Get integration class
        integration_class = self.registry.get_integration_class(name)
        if not integration_class:
            logger.error(f"Integration not found: {name}")
            return None
        
        # Create config
        integration_config = IntegrationConfig(
            name=name,
            integration_id=name,
            enabled=True,
            config=config or {},
        )
        
        # Load the integration
        instance = self.registry.load_integration(name, integration_config)
        if instance and instance.status == IntegrationStatus.LOADED:
            self._instances[name] = instance
            self._enabled_integrations.add(name)
            
            # Run health check
            health, message = instance.instance.check_health()
            instance.health = health
            instance.health_message = message
            
            logger.info(f"Loaded integration: {name} (v{instance.info.version})")
        
        return instance
    
    def unload_integration(self, name: str) -> bool:
        """Unload an integration."""
        if name not in self._instances:
            return True
        
        success = self.registry.unload_integration(name)
        if success:
            del self._instances[name]
            self._enabled_integrations.discard(name)
            logger.info(f"Unloaded integration: {name}")
        
        return success
    
    def enable_integration(self, name: str) -> bool:
        """Enable an integration."""
        if name in self._enabled_integrations:
            return True
        
        self._enabled_integrations.add(name)
        self._disabled_integrations.discard(name)
        
        # Load the integration
        self.load_integration(name)
        
        logger.info(f"Enabled integration: {name}")
        return True
    
    def disable_integration(self, name: str) -> bool:
        """Disable an integration."""
        if name in self._disabled_integrations:
            return True
        
        # Unload if loaded
        self.unload_integration(name)
        
        self._disabled_integrations.add(name)
        self._enabled_integrations.discard(name)
        
        logger.info(f"Disabled integration: {name}")
        return True
    
    def check_health(self, name: str) -> dict[str, Any]:
        """Check the health of an integration."""
        instance = self._instances.get(name)
        if not instance:
            return {
                "name": name,
                "health": "not-loaded",
                "message": "Integration is not loaded",
            }
        
        health, message = instance.instance.check_health()
        instance.health = health
        instance.health_message = message
        
        return {
            "name": name,
            "health": health,
            "message": message,
            "version": instance.info.version,
            "type": instance.info.integration_type.value,
        }
    
    def check_all_health(self) -> list[dict[str, Any]]:
        """Check the health of all loaded integrations."""
        results = []
        for name in self._instances:
            results.append(self.check_health(name))
        return results
    
    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about integrations."""
        all_integrations = self.registry.list_integrations()
        
        by_type: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_platform: dict[str, int] = {}
        
        for info in all_integrations:
            # Count by type
            type_key = info.integration_type.value
            by_type[type_key] = by_type.get(type_key, 0) + 1
            
            # Count by category
            category_key = info.category.value
            by_category[category_key] = by_category.get(category_key, 0) + 1
            
            # Count by platform
            if info.platform:
                platform_key = info.platform.value
                by_platform[platform_key] = by_platform.get(platform_key, 0) + 1
        
        return {
            "total_integrations": len(all_integrations),
            "loaded_integrations": len(self._instances),
            "enabled_integrations": len(self._enabled_integrations),
            "disabled_integrations": len(self._disabled_integrations),
            "by_type": by_type,
            "by_category": by_category,
            "by_platform": by_platform,
        }
    
    def get_tools_from_integrations(self) -> list[dict[str, Any]]:
        """Get all tools provided by loaded integrations."""
        tools = []
        for name, instance in self._instances.items():
            integration_tools = instance.instance.get_tools()
            for tool in integration_tools:
                # Add integration metadata to tool
                tool_with_meta = {
                    **tool,
                    "_integration": name,
                    "_integration_version": instance.info.version,
                    "_integration_type": instance.info.integration_type.value,
                }
                tools.append(tool_with_meta)
        return tools
    
    # P5.2: Secret Management
    
    def get_secret_store(self) -> SecretStore | None:
        """Get the secret store for integrations."""
        return self._secret_store
    
    def set_secret(
        self,
        key: str,
        value: str,
        integration: str | None = None,
        description: str = "",
        overwrite: bool = False,
    ) -> bool:
        """Set a secret for an integration.
        
        Args:
            key: Secret key/name
            value: Secret value
            integration: Integration name (for scoping)
            description: Description of the secret
            overwrite: Whether to overwrite existing value
            
        Returns:
            bool: True if set successfully
        """
        if not self._secret_store:
            logger.warning("No secret store configured")
            return False
        return self._secret_store.set(key, value, integration=integration, 
                                       description=description, overwrite=overwrite)
    
    def get_secret(
        self,
        key: str,
        integration: str | None = None,
        default: str | None = None,
    ) -> SecretValue | None:
        """Get a secret for an integration.
        
        Args:
            key: Secret key/name
            integration: Integration name (for scoping)
            default: Default value if not found
            
        Returns:
            SecretValue: The secret value with metadata, or None if not found
        """
        if not self._secret_store:
            # Try environment variables as fallback
            return self._get_secret_from_env(key, integration, default)
        return self._secret_store.get(key, integration=integration, default=default)
    
    def _get_secret_from_env(
        self,
        key: str,
        integration: str | None = None,
        default: str | None = None,
    ) -> SecretValue | None:
        """Get a secret from environment variables."""
        import os
        
        # Try environment variables
        env_key = f"SKELETONKEY_SECRET_{key.upper()}"
        if env_key in os.environ:
            return SecretValue(
                value=os.environ[env_key],
                integration=integration,
                source="env",
                masked=False,
            )
        
        # Try integration-specific environment variable
        if integration:
            env_key = f"SKELETONKEY_SECRET_{integration.upper()}_{key.upper()}"
            if env_key in os.environ:
                return SecretValue(
                    value=os.environ[env_key],
                    integration=integration,
                    source="env",
                    masked=False,
                )
        
        if default is not None:
            return SecretValue(
                value=default,
                integration=integration,
                source="default",
                masked=False,
            )
        
        return None
    
    def list_secrets(self, integration: str | None = None) -> list:
        """List secret information for an integration.
        
        Args:
            integration: Integration name to filter by (None = all)
            
        Returns:
            list: List of secret info objects
        """
        if not self._secret_store:
            return []
        return self._secret_store.list_secrets(integration=integration)
    
    def delete_secret(self, key: str, integration: str | None = None) -> bool:
        """Delete a secret for an integration.
        
        Args:
            key: Secret key/name
            integration: Integration name (for scoping)
            
        Returns:
            bool: True if deleted successfully
        """
        if not self._secret_store:
            logger.warning("No secret store configured")
            return False
        return self._secret_store.delete(key, integration=integration)
    
    def has_secret(self, key: str, integration: str | None = None) -> bool:
        """Check if a secret exists for an integration.
        
        Args:
            key: Secret key/name
            integration: Integration name (for scoping)
            
        Returns:
            bool: True if the secret exists
        """
        if not self._secret_store:
            return False
        return self._secret_store.has(key, integration=integration)
    
    def get_integration_config(self, name: str) -> dict[str, Any]:
        """Get the full configuration for an integration including secrets.
        
        This method combines:
        1. Configuration from config file
        2. Secrets from secret store
        3. Environment variables
        
        Args:
            name: Integration name
            
        Returns:
            dict: Full configuration with secrets resolved
        """
        # Get base config from config file
        integrations_config = getattr(self.config, 'integrations', None)
        base_config = {}
        if integrations_config:
            configs = getattr(integrations_config, 'configs', {})
            if isinstance(configs, dict):
                base_config = configs.get(name, {})
        
        # Get integration-specific config from the integration's default
        integration_class = self.registry.get_integration_class(name)
        if integration_class:
            # Create a minimal IntegrationConfig for the integration
            dummy_config = IntegrationConfig(
                name=name,
                integration_id=name,
                enabled=True,
            )
            instance = integration_class(dummy_config)
            if instance.info.default_config:
                base_config = {**instance.info.default_config, **base_config}
        
        # Get secrets
        if self._secret_store:
            secrets = self._secret_store.export_secrets(integration=name)
            for key, value in secrets.items():
                base_config[f"secret_{key}"] = value
        
        return base_config
    
    # P5.3: Discovery Methods
    
    def discover_integrations(self, refresh: bool = False) -> list[IntegrationInfo]:
        """Discover all available integrations.
        
        Args:
            refresh: Whether to refresh the discovery cache
            
        Returns:
            list: List of IntegrationInfo objects
        """
        if not self._discovery:
            logger.warning("Discovery not initialized")
            return []
        
        return self._discovery.discover(refresh=refresh)
    
    def discover_by_source(self, source_name: str) -> list[IntegrationInfo]:
        """Discover integrations from a specific source.
        
        Args:
            source_name: Name of the discovery source
            
        Returns:
            list: List of IntegrationInfo objects from that source
        """
        if not self._discovery:
            logger.warning("Discovery not initialized")
            return []
        
        return self._discovery.discover_by_source(source_name)
    
    def get_discovery_sources(self) -> list:
        """Get all discovery sources.
        
        Returns:
            list: List of DiscoverySource objects
        """
        if not self._discovery:
            return []
        return self._discovery.get_sources()
    
    def get_discovery_statistics(self) -> dict[str, Any]:
        """Get discovery statistics.
        
        Returns:
            dict: Statistics about discovery
        """
        if not self._discovery:
            return {}
        return self._discovery.get_statistics()
    
    # P5.3: Dynamic Loading Methods
    
    def load_integration_dynamic(
        self,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> IntegrationInstance | None:
        """Load an integration dynamically.
        
        Args:
            name: Integration name
            config: Optional configuration for the integration
            
        Returns:
            IntegrationInstance: The loaded instance, or None if failed
        """
        if not self._loader:
            logger.warning("Loader not initialized")
            return None
        
        result = self._loader.load(name, config)
        if result.success and result.instance:
            # Store in instances
            self._instances[name] = result.instance
            return result.instance
        
        return None
    
    def unload_integration_dynamic(self, name: str) -> bool:
        """Unload a dynamically loaded integration.
        
        Args:
            name: Integration name
            
        Returns:
            bool: True if unloaded successfully
        """
        if not self._loader:
            logger.warning("Loader not initialized")
            return False
        
        # Remove from instances
        self._instances.pop(name, None)
        
        return self._loader.unload(name)
    
    def reload_integration(self, name: str, config: dict[str, Any] | None = None) -> IntegrationInstance | None:
        """Reload an integration.
        
        Args:
            name: Integration name
            config: Optional configuration for the integration
            
        Returns:
            IntegrationInstance: The reloaded instance, or None if failed
        """
        if not self._loader:
            logger.warning("Loader not initialized")
            return None
        
        # Unload first
        self._instances.pop(name, None)
        
        # Load again
        result = self._loader.load(name, config)
        if result.success and result.instance:
            self._instances[name] = result.instance
            return result.instance
        
        return None
    
    def is_loaded_dynamic(self, name: str) -> bool:
        """Check if an integration is dynamically loaded.
        
        Args:
            name: Integration name
            
        Returns:
            bool: True if the integration is loaded
        """
        if not self._loader:
            return False
        return self._loader.is_loaded(name)
    
    def is_loading(self, name: str) -> bool:
        """Check if an integration is currently loading.
        
        Args:
            name: Integration name
            
        Returns:
            bool: True if the integration is currently loading
        """
        if not self._loader:
            return False
        return self._loader.is_loading(name)
    
    def get_loader_statistics(self) -> dict[str, Any]:
        """Get loader statistics.
        
        Returns:
            dict: Statistics about loaded integrations
        """
        if not self._loader:
            return {}
        return self._loader.get_statistics()
    
    # P5.3: Pack Management Methods
    
    def install_pack(
        self,
        name: str,
        source: str | None = None,
        version: str | None = None,
        force: bool = False,
    ) -> bool:
        """Install an integration pack.
        
        Args:
            name: Pack name or URL
            source: Source type (local, git, pypi) or URL
            version: Version to install (for git/pypi)
            force: Force reinstall if already installed
            
        Returns:
            bool: True if installation succeeded
        """
        if not self._pack_manager:
            logger.warning("Pack manager not initialized")
            return False
        
        return self._pack_manager.install(name, source, version, force)
    
    def uninstall_pack(self, name: str, force: bool = False) -> bool:
        """Uninstall an integration pack.
        
        Args:
            name: Pack name
            force: Force uninstall even if integrations are loaded
            
        Returns:
            bool: True if uninstallation succeeded
        """
        if not self._pack_manager:
            logger.warning("Pack manager not initialized")
            return False
        
        return self._pack_manager.uninstall(name, force)
    
    def upgrade_pack(self, name: str, version: str | None = None) -> bool:
        """Upgrade an integration pack.
        
        Args:
            name: Pack name
            version: Version to upgrade to (optional)
            
        Returns:
            bool: True if upgrade succeeded
        """
        if not self._pack_manager:
            logger.warning("Pack manager not initialized")
            return False
        
        return self._pack_manager.upgrade(name, version)
    
    def list_installed_packs(self) -> list[PackInfo]:
        """List all installed packs.
        
        Returns:
            list: List of PackInfo objects
        """
        if not self._pack_manager:
            return []
        return self._pack_manager.list_installed()
    
    def get_pack_info(self, name: str) -> PackInfo | None:
        """Get information about an installed pack.
        
        Args:
            name: Pack name
            
        Returns:
            PackInfo: Pack information, or None if not installed
        """
        if not self._pack_manager:
            return None
        return self._pack_manager.get_installed(name)
    
    def is_pack_installed(self, name: str) -> bool:
        """Check if a pack is installed.
        
        Args:
            name: Pack name
            
        Returns:
            bool: True if the pack is installed
        """
        if not self._pack_manager:
            return False
        return self._pack_manager.is_installed(name)
    
    def check_pack_updates(self) -> list[dict[str, Any]]:
        """Check for available updates for installed packs.
        
        Returns:
            list: List of update information dictionaries
        """
        if not self._pack_manager:
            return []
        return self._pack_manager.check_updates()
    
    def get_pack_statistics(self) -> dict[str, Any]:
        """Get pack manager statistics.
        
        Returns:
            dict: Statistics about installed packs
        """
        if not self._pack_manager:
            return {}
        return self._pack_manager.get_statistics()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    # Enums
    'IntegrationType',
    'IntegrationCategory',
    'IntegrationPlatform',
    'IntegrationMaturity',
    'IntegrationStatus',
    # Data classes
    'IntegrationDependency',
    'IntegrationCapability',
    'IntegrationHealthCheck',
    'IntegrationSandbox',
    'IntegrationConfig',
    'IntegrationInfo',
    'IntegrationInstance',
    # Base class
    'Integration',
    # Registry
    'IntegrationRegistry',
    'registry',
    # Decorator
    'register_integration',
    # Manager
    'IntegrationManager',
    # P5.2: Secret Management
    'SecretStore',
    'SecretValue',
    'SecretInfo',
    'get_secret_store',
    # P5.3: Discovery
    'IntegrationDiscovery',
    'get_discovery',
    'DiscoverySource',
    'DiscoveryResult',
    'EntryPointDiscoverySource',
    'FilesystemDiscoverySource',
    'PackageDiscoverySource',
    'ConfigDiscoverySource',
    # P5.3: Dynamic Loading
    'IntegrationLoader',
    'get_loader',
    'LoadResult',
    'LoadError',
    # P5.3: Pack Management
    'IntegrationPackManager',
    'get_pack_manager',
    'PackInfo',
    'PackManifest',
]

# Import example integrations to register them
try:
    from . import examples  # noqa: F401
    _examples_imported = True
except ImportError as e:
    # Example integrations may have optional dependencies
    # This is expected and okay - they'll be registered when their deps are installed
    _examples_imported = False
    logger.debug(f"Could not import example integrations: {e}")
