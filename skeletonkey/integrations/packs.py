"""Integration Pack Management for SkeletonKey (P5.3).

This module provides installation, upgrade, and management of integration packs.
Integration packs are collections of integrations that can be installed as a unit.

Supported sources:
- Local directories
- Git repositories
- PyPI packages

To avoid circular imports, this module uses lazy imports.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Type checking imports
if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _get_registry():
    """Lazy import of registry."""
    from . import registry
    return registry


def _get_integration_info_class():
    """Lazy import of IntegrationInfo."""
    from . import IntegrationInfo
    return IntegrationInfo


@dataclass
class PackInfo:
    """Information about an integration pack."""
    name: str
    version: str = "1.0.0"
    title: str = ""
    description: str = ""
    author: str = ""
    author_email: str = ""
    homepage: str = ""
    repository: str = ""
    license: str = ""
    integrations: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    source: str = ""  # local, git, pypi
    source_url: str = ""
    installed_at: str = ""
    updated_at: str = ""
    installed_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "author": self.author,
            "author_email": self.author_email,
            "homepage": self.homepage,
            "repository": self.repository,
            "license": self.license,
            "integrations": self.integrations,
            "dependencies": self.dependencies,
            "conflicts": self.conflicts,
            "source": self.source,
            "source_url": self.source_url,
            "installed_at": self.installed_at,
            "updated_at": self.updated_at,
            "installed_path": self.installed_path,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackInfo:
        """Create a PackInfo from a dictionary."""
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            author=data.get("author", ""),
            author_email=data.get("author_email", ""),
            homepage=data.get("homepage", ""),
            repository=data.get("repository", ""),
            license=data.get("license", ""),
            integrations=data.get("integrations", []),
            dependencies=data.get("dependencies", []),
            conflicts=data.get("conflicts", []),
            source=data.get("source", ""),
            source_url=data.get("source_url", ""),
            installed_at=data.get("installed_at", ""),
            updated_at=data.get("updated_at", ""),
            installed_path=data.get("installed_path", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass
class PackManifest:
    """Manifest for an integration pack."""
    schema_version: str = "1.0"
    name: str = ""
    version: str = "1.0.0"
    title: str = ""
    description: str = ""
    author: str = ""
    author_email: str = ""
    homepage: str = ""
    repository: str = ""
    license: str = ""
    integrations: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "title": self.title,
            "description": self.description,
            "author": self.author,
            "author_email": self.author_email,
            "homepage": self.homepage,
            "repository": self.repository,
            "license": self.license,
            "integrations": self.integrations,
            "dependencies": self.dependencies,
            "conflicts": self.conflicts,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackManifest:
        """Create a PackManifest from a dictionary."""
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            author=data.get("author", ""),
            author_email=data.get("author_email", ""),
            homepage=data.get("homepage", ""),
            repository=data.get("repository", ""),
            license=data.get("license", ""),
            integrations=data.get("integrations", []),
            dependencies=data.get("dependencies", []),
            conflicts=data.get("conflicts", []),
            metadata=data.get("metadata", {}),
        )
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> PackManifest:
        """Create a PackManifest from JSON string."""
        return cls.from_dict(json.loads(json_str))


class IntegrationPackManager:
    """Manages installation, upgrade, and removal of integration packs."""
    
    MANIFEST_FILENAME = "pack.json"
    INSTALLED_FILE = "installed.json"
    
    def __init__(
        self,
        config: Any = None,
        loader: Any = None,
        install_root: str | Path | None = None,
    ):
        """Initialize the pack manager."""
        self.config = config
        self._loader = loader
        self._installed: dict[str, PackInfo] = {}
        self._available: dict[str, PackManifest] = {}
        
        # Determine install root
        if install_root:
            self.install_root = Path(install_root)
        elif config:
            try:
                integrations_config = getattr(config, 'integrations', None)
                if integrations_config:
                    packs_dir = getattr(integrations_config, 'packs_dir', 'integrations')
                else:
                    packs_dir = 'integrations'
                
                workspace = getattr(config, 'workspace', os.getcwd())
                self.install_root = Path(workspace) / packs_dir
            except Exception:
                self.install_root = Path('integrations')
        else:
            self.install_root = Path('integrations')
        
        # NOTE: the install root is deliberately *not* created here. A toolkit
        # build constructs this manager, and a build must never write into the
        # workspace (the policy property test snapshots the tree around build()).
        # The root is created lazily, on first install/persist.
        self._load_installed()
    
    def _ensure_root(self) -> None:
        """Create the install root on demand (never at construction)."""
        self.install_root.mkdir(parents=True, exist_ok=True)
    
    def _load_installed(self) -> None:
        """Load information about installed packs."""
        installed_file = self.install_root / self.INSTALLED_FILE
        
        if installed_file.exists():
            try:
                with open(installed_file) as f:
                    data = json.load(f)
                    for _name, pack_data in data.items():
                        pack_info = PackInfo.from_dict(pack_data)
                        self._installed[pack_info.name] = pack_info
            except Exception as e:
                logger.error(f"Failed to load installed packs: {e}")
    
    def _save_installed(self) -> None:
        """Save information about installed packs."""
        self._ensure_root()
        installed_file = self.install_root / self.INSTALLED_FILE
        
        try:
            data = {}
            for name, pack_info in self._installed.items():
                data[name] = pack_info.to_dict()
            
            with open(installed_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save installed packs: {e}")
    
    def install(
        self,
        name: str,
        source: str | None = None,
        version: str | None = None,
        force: bool = False,
    ) -> bool:
        """Install an integration pack."""
        self._ensure_root()
        # Check if already installed
        if name in self._installed and not force:
            logger.warning(f"Pack {name} already installed, use force=True to reinstall")
            return False
        
        # Determine source
        if source:
            source_type = source
        elif name.startswith("http://") or name.startswith("https://"):
            source_type = "git"
        elif name.startswith("./") or name.startswith("/") or "://" not in name:
            source_type = "local"
        else:
            source_type = "git"
        
        # Install based on source type
        try:
            if source_type in ["git", "https://", "http://"]:
                return self._install_from_git(name, version)
            if source_type == "local":
                return self._install_from_local(name)
            if source_type == "pypi":
                return self._install_from_pypi(name, version)
            logger.error(f"Unknown source type: {source_type}")
            return False
        except Exception as e:
            logger.error(f"Failed to install pack {name}: {e}")
            return False
    
    def _install_from_local(self, path: str) -> bool:
        """Install a pack from a local directory."""
        path = Path(path)
        
        if not path.exists():
            logger.error(f"Local path does not exist: {path}")
            return False
        
        if not path.is_dir():
            logger.error(f"Local path is not a directory: {path}")
            return False
        
        # Check for manifest
        manifest_file = path / self.MANIFEST_FILENAME
        if not manifest_file.exists():
            logger.error(f"No {self.MANIFEST_FILENAME} found in {path}")
            return False
        
        # Read manifest
        try:
            with open(manifest_file) as f:
                manifest_data = json.load(f)
            manifest = PackManifest.from_dict(manifest_data)
        except Exception as e:
            logger.error(f"Failed to read manifest from {path}: {e}")
            return False
        
        # Create install directory
        install_dir = self.install_root / manifest.name
        if install_dir.exists():
            if not self._installed.get(manifest.name):
                shutil.rmtree(install_dir)
            else:
                logger.error(f"Pack {manifest.name} already installed")
                return False
        
        # Copy pack to install directory
        try:
            shutil.copytree(path, install_dir)
        except Exception as e:
            logger.error(f"Failed to copy pack to {install_dir}: {e}")
            return False
        
        # Create pack info
        now = datetime.utcnow().isoformat() + "Z"
        pack_info = PackInfo(
            name=manifest.name,
            version=manifest.version,
            title=manifest.title,
            description=manifest.description,
            author=manifest.author,
            author_email=manifest.author_email,
            homepage=manifest.homepage,
            repository=manifest.repository,
            license=manifest.license,
            integrations=[i.get("name", "") for i in manifest.integrations],
            dependencies=manifest.dependencies,
            conflicts=manifest.conflicts,
            source="local",
            source_url=str(path),
            installed_at=now,
            updated_at=now,
            installed_path=str(install_dir),
            metadata=manifest.metadata,
        )
        
        # Store pack info
        self._installed[manifest.name] = pack_info
        self._save_installed()
        
        # Resolve and install pack dependencies
        if not self._resolve_pack_dependencies(manifest):
            return False
        
        # Load integrations from the pack
        self._load_pack_integrations(pack_info)
        
        logger.info(f"Installed pack {manifest.name} v{manifest.version} from {path}")
        return True
    
    def _install_from_git(self, url: str, version: str | None = None) -> bool:
        """Install a pack from a git repository."""
        # Parse URL
        if not url.startswith("http://") and not url.startswith("https://"):
            if "/" in url:
                parts = url.split("/")
                if len(parts) >= 2:
                    user, repo = parts[-2], parts[-1]
                    if not repo.endswith(".git"):
                        repo += ".git"
                    url = f"https://github.com/{user}/{repo}"
                else:
                    url = f"https://github.com/{url}.git"
            else:
                url = f"https://github.com/{url}.git"
        
        # Create temp directory for clone
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Clone the repository
            cmd = ["git", "clone", url, str(temp_path / "repo")]
            if version:
                cmd.extend(["--branch", version])
            
            try:
                subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to clone {url}: {e.stderr}")
                return False
            except FileNotFoundError:
                logger.error("Git not found, cannot install from git repository")
                return False
            
            # Find pack directory in the repo
            repo_path = temp_path / "repo"
            pack_dirs = []
            
            for dir_path in repo_path.rglob("*"):
                if (dir_path / self.MANIFEST_FILENAME).exists():
                    pack_dirs.append(dir_path)
            
            if not pack_dirs:
                logger.error(f"No {self.MANIFEST_FILENAME} found in {url}")
                return False
            
            # Use the first pack directory found
            pack_dir = pack_dirs[0]
            
            # Read manifest
            manifest_file = pack_dir / self.MANIFEST_FILENAME
            try:
                with open(manifest_file) as f:
                    manifest_data = json.load(f)
                manifest = PackManifest.from_dict(manifest_data)
            except Exception as e:
                logger.error(f"Failed to read manifest from {pack_dir}: {e}")
                return False
            
            # Create install directory
            install_dir = self.install_root / manifest.name
            if install_dir.exists():
                if not self._installed.get(manifest.name):
                    shutil.rmtree(install_dir)
                else:
                    logger.error(f"Pack {manifest.name} already installed")
                    return False
            
            # Copy pack to install directory
            try:
                shutil.copytree(pack_dir, install_dir)
            except Exception as e:
                logger.error(f"Failed to copy pack to {install_dir}: {e}")
                return False
            
            # Create pack info
            now = datetime.utcnow().isoformat() + "Z"
            pack_info = PackInfo(
                name=manifest.name,
                version=manifest.version,
                title=manifest.title,
                description=manifest.description,
                author=manifest.author,
                author_email=manifest.author_email,
                homepage=manifest.homepage,
                repository=url,
                license=manifest.license,
                integrations=[i.get("name", "") for i in manifest.integrations],
                dependencies=manifest.dependencies,
                conflicts=manifest.conflicts,
                source="git",
                source_url=url,
                installed_at=now,
                updated_at=now,
                installed_path=str(install_dir),
                metadata={"git_ref": version} if version else {},
            )
            
            # Store pack info
            self._installed[manifest.name] = pack_info
            self._save_installed()
            
            # Load integrations from the pack
            self._load_pack_integrations(pack_info)
            
            logger.info(f"Installed pack {manifest.name} v{manifest.version} from {url}")
            return True
    
    def _install_from_pypi(self, package: str, version: str | None = None) -> bool:
        """Install a pack from PyPI."""
        # Install the package
        cmd = [sys.executable, "-m", "pip", "install", package]
        if version:
            cmd.append(f"=={version}")
        
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install {package}: {e.stderr}")
            return False
        except FileNotFoundError:
            logger.error("pip not found, cannot install from PyPI")
            return False
        
        # Import the package and look for integrations
        try:
            __import__(package)
            
            # Look for pack manifest
            manifest_path = None
            for path in sys.path:
                candidate = Path(path) / package / self.MANIFEST_FILENAME
                if candidate.exists():
                    manifest_path = candidate
                    break
            
            if not manifest_path:
                logger.error(f"No {self.MANIFEST_FILENAME} found in package {package}")
                return False
            
            # Read manifest
            with open(manifest_path) as f:
                manifest_data = json.load(f)
            manifest = PackManifest.from_dict(manifest_data)
            
            # Create pack info
            now = datetime.utcnow().isoformat() + "Z"
            pack_info = PackInfo(
                name=manifest.name,
                version=manifest.version,
                title=manifest.title,
                description=manifest.description,
                author=manifest.author,
                author_email=manifest.author_email,
                homepage=manifest.homepage,
                repository=manifest.repository,
                license=manifest.license,
                integrations=[i.get("name", "") for i in manifest.integrations],
                dependencies=manifest.dependencies,
                conflicts=manifest.conflicts,
                source="pypi",
                source_url=package,
                installed_at=now,
                updated_at=now,
                installed_path=str(manifest_path.parent),
                metadata={"package": package, "version": version} if version else {"package": package},
            )
            
            # Store pack info
            self._installed[manifest.name] = pack_info
            self._save_installed()
            
            # Register integrations from the package
            self._register_package_integrations(package, manifest)
            
            logger.info(f"Installed pack {manifest.name} v{manifest.version} from PyPI")
            return True
            
        except Exception as e:
            logger.error(f"Failed to process package {package}: {e}")
            return False
    
    def _load_pack_integrations(self, pack_info: PackInfo) -> None:
        """Load integrations from an installed pack."""
        install_path = Path(pack_info.installed_path)
        
        # Look for integration files
        for pattern in ["*.py", "*_integration.py", "integration.py"]:
            for file_path in install_path.glob(pattern):
                if file_path.is_file():
                    self._register_integration_from_file(file_path, pack_info)
    
    def _register_integration_from_file(self, file_path: Path, pack_info: PackInfo) -> None:
        """Register an integration from a Python file."""
        Integration = None
        IntegrationInfo = _get_integration_info_class()
        registry = _get_registry()
        
        # Lazy import to avoid circular imports
        try:
            from . import Integration as IntegrationClass
            Integration = IntegrationClass
        except Exception:
            logger.error("Failed to import Integration class")
            return
        
        try:
            # Get module name
            module_name = file_path.stem
            if module_name.endswith("_integration"):
                module_name = module_name[:-12]
            
            # Add to sys.path temporarily
            parent_dir = str(file_path.parent.absolute())
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            
            try:
                # Import the module
                spec = importlib.util.spec_from_file_location(module_name, str(file_path))
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = module
                    spec.loader.exec_module(module)
                    
                    # Look for integration classes
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if isinstance(attr, type) and issubclass(attr, Integration):
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
                                    # Add pack metadata
                                    info.metadata["pack"] = pack_info.name
                                    info.metadata["pack_version"] = pack_info.version
                                    info.metadata["pack_source"] = pack_info.source
                                    
                                    # Register with registry
                                    if not registry.has_integration(info.name):
                                        registry.register_integration(attr, info)
                                    
                                    logger.info(f"Registered integration {info.name} from pack {pack_info.name}")
                            except Exception as e:
                                logger.error(f"Failed to get info from {attr_name} in {file_path}: {e}")
            finally:
                # Clean up sys.path
                if parent_dir in sys.path:
                    sys.path.remove(parent_dir)
                    
        except Exception as e:
            logger.error(f"Failed to register integration from {file_path}: {e}")
    
    def _register_package_integrations(self, package: str, manifest: PackManifest) -> None:
        """Register integrations from an installed package."""
        Integration = None
        IntegrationInfo = _get_integration_info_class()
        registry = _get_registry()
        
        try:
            from . import Integration as IntegrationClass
            Integration = IntegrationClass
        except Exception:
            logger.error("Failed to import Integration class")
            return
        
        try:
            module = __import__(package)
            
            # Look for integration classes in the package
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Integration):
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
                            # Add pack metadata
                            info.metadata["pack"] = manifest.name
                            info.metadata["pack_version"] = manifest.version
                            info.metadata["pack_source"] = "pypi"
                            info.metadata["package"] = package
                            
                            # Register with registry
                            if not registry.has_integration(info.name):
                                registry.register_integration(attr, info)
                            
                            logger.info(f"Registered integration {info.name} from package {package}")
                    except Exception as e:
                        logger.error(f"Failed to get info from {attr_name} in {package}: {e}")
                        
        except Exception as e:
            logger.error(f"Failed to process package {package}: {e}")
    
    def uninstall(self, name: str, force: bool = False) -> bool:
        """Uninstall an integration pack."""
        if name not in self._installed:
            logger.error(f"Pack {name} not installed")
            return False
        
        pack_info = self._installed[name]
        
        # Check if any integrations are loaded
        if self._loader:
            for integration_name in pack_info.integrations:
                if self._loader.is_loaded(integration_name):
                    if not force:
                        logger.error(
                            f"Integration {integration_name} from pack {name} is loaded, "
                            "use force=True to uninstall anyway"
                        )
                        return False
                    # Unload the integration
                    self._loader.unload(integration_name)
        
        # Remove from installed
        del self._installed[name]
        self._save_installed()
        
        # Remove the pack directory
        install_path = Path(pack_info.installed_path)
        if install_path.exists():
            try:
                shutil.rmtree(install_path)
            except Exception as e:
                logger.error(f"Failed to remove {install_path}: {e}")
                return False
        
        # Unregister integrations
        registry = _get_registry()
        for integration_name in pack_info.integrations:
            if registry.has_integration(integration_name):
                registry.unregister_integration(integration_name)
        
        logger.info(f"Uninstalled pack {name}")
        return True
    
    def upgrade(self, name: str, version: str | None = None) -> bool:
        """Upgrade an integration pack."""
        if name not in self._installed:
            logger.error(f"Pack {name} not installed")
            return False
        
        pack_info = self._installed[name]
        
        # Uninstall first
        if not self.uninstall(name, force=True):
            logger.error(f"Failed to uninstall {name} for upgrade")
            return False
        
        # Reinstall with new version
        if pack_info.source == "local":
            return self.install(pack_info.source_url, "local", version, force=True)
        if pack_info.source == "git":
            return self.install(pack_info.source_url, "git", version, force=True)
        if pack_info.source == "pypi":
            return self.install(pack_info.source_url, "pypi", version, force=True)
        logger.error(f"Unknown source for pack {name}: {pack_info.source}")
        return False
    
    def list_installed(self) -> list[PackInfo]:
        """List all installed packs."""
        return list(self._installed.values())
    
    def get_installed(self, name: str) -> PackInfo | None:
        """Get information about an installed pack."""
        return self._installed.get(name)
    
    def is_installed(self, name: str) -> bool:
        """Check if a pack is installed."""
        return name in self._installed
    
    def search(self, query: str) -> list[PackInfo]:
        """Search for installed packs by name or description."""
        results = []
        query_lower = query.lower()
        
        for pack_info in self._installed.values():
            if (
                query_lower in pack_info.name.lower() or
                query_lower in pack_info.title.lower() or
                query_lower in pack_info.description.lower()
            ):
                results.append(pack_info)
        
        return results
    
    def check_updates(self) -> list[dict[str, Any]]:
        """Check for available updates for installed packs."""
        updates = []
        
        for _name, pack_info in self._installed.items():
            if pack_info.source == "git":
                update_info = self._check_git_update(pack_info)
                if update_info:
                    updates.append(update_info)
            elif pack_info.source == "pypi":
                update_info = self._check_pypi_update(pack_info)
                if update_info:
                    updates.append(update_info)
        
        return updates
    
    def _check_git_update(self, pack_info: PackInfo) -> dict[str, Any] | None:
        """Check for updates for a git-installed pack."""
        try:
            install_path = Path(pack_info.installed_path)
            current_commit = self._get_git_commit(install_path)
            
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                cmd = ["git", "clone", pack_info.source_url, str(temp_path / "repo")]
                if pack_info.metadata.get("git_ref"):
                    cmd.extend(["--branch", pack_info.metadata["git_ref"]])
                
                subprocess.run(cmd, capture_output=True, check=True)
                
                remote_commit = self._get_git_commit(temp_path / "repo")
                
                if current_commit != remote_commit:
                    return {
                        "name": pack_info.name,
                        "current_version": pack_info.version,
                        "current_commit": current_commit[:8] if current_commit else "unknown",
                        "remote_commit": remote_commit[:8] if remote_commit else "unknown",
                        "has_update": True,
                    }
        except Exception:
            pass
        
        return None
    
    def _get_git_commit(self, path: Path) -> str | None:
        """Get the current git commit hash for a directory."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(path),
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except Exception:
            return None
    
    def _check_pypi_update(self, pack_info: PackInfo) -> dict[str, Any] | None:
        """Check for updates for a PyPI-installed pack."""
        try:
            package = pack_info.metadata.get("package")
            if not package:
                return None
            
            import requests
            response = requests.get(f"https://pypi.org/pypi/{package}/json", timeout=10)
            if response.status_code == 200:
                data = response.json()
                latest_version = data["info"]["version"]
                
                if pack_info.version != latest_version:
                    return {
                        "name": pack_info.name,
                        "current_version": pack_info.version,
                        "latest_version": latest_version,
                        "has_update": True,
                    }
        except Exception:
            pass
        
        return None
    
    def _resolve_pack_dependencies(self, manifest: PackManifest) -> bool:
        """Resolve and install pack dependencies from manifest.
        
        Dependencies in manifest are a list of dicts with:
        - name: pack name
        - version: version constraint (optional)
        - source: source type (local, git, pypi) (optional)
        - optional: whether dependency is optional (optional)
        
        Returns True if all required dependencies are satisfied.
        """
        for dep in manifest.dependencies:
            if not isinstance(dep, dict):
                logger.warning(f"Skipping invalid dependency format: {dep}")
                continue
            
            dep_name = dep.get("name")
            dep_version = dep.get("version")
            dep_source = dep.get("source")
            dep_optional = dep.get("optional", False)
            
            if not dep_name:
                logger.warning("Skipping dependency with no name")
                continue
            
            # Check if already installed
            if dep_name in self._installed:
                # Check version constraint if specified
                if dep_version:
                    installed_version = self._installed[dep_name].version
                    if not self._version_satisfies(installed_version, dep_version):
                        logger.error(
                            f"Pack {dep_name} version {installed_version} does not satisfy "
                            f"constraint {dep_version}"
                        )
                        if not dep_optional:
                            return False
                continue
            
            # Install the dependency
            logger.info(f"Installing pack dependency: {dep_name}")
            success = self.install(dep_name, source=dep_source, version=dep_version, force=False)
            if not success:
                if dep_optional:
                    logger.warning(f"Optional pack dependency {dep_name} failed to install, continuing")
                    continue
                logger.error(f"Required pack dependency {dep_name} failed to install")
                return False
        
        return True
    
    def _version_satisfies(self, version: str, constraint: str) -> bool:
        """Check if a version satisfies a constraint.
        
        Supports basic constraints: >=, <=, >, <, ==, ~=, !=
        """
        import re
        
        # Simple constraint parsing
        match = re.match(r'^(>=|<=|>|<|==|~=|!=)\s*(.+)$', constraint.strip())
        if not match:
            # No operator means exact match
            return version == constraint.strip()
        
        op, constraint_version = match.groups()
        
        # Parse versions (simple semantic version comparison)
        def parse_ver(v: str) -> tuple:
            parts = v.split('.')
            nums = []
            for p in parts:
                # Remove pre-release suffixes
                p = re.split(r'[-+]', p)[0]
                try:
                    nums.append(int(p))
                except ValueError:
                    nums.append(0)
            while len(nums) < 3:
                nums.append(0)
            return tuple(nums[:3])
        
        v1 = parse_ver(version)
        v2 = parse_ver(constraint_version)
        
        if op == '>=':
            return v1 >= v2
        elif op == '<=':
            return v1 <= v2
        elif op == '>':
            return v1 > v2
        elif op == '<':
            return v1 < v2
        elif op == '==':
            return v1 == v2
        elif op == '~=':
            # Compatible release: >= major.minor, < major+1.0
            return v1 >= v2 and v1[0] == v2[0]
        elif op == '!=':
            return v1 != v2
        
        return False
    
    def validate_pack(self, path: str | Path) -> tuple[bool, list[str]]:
        """Validate an integration pack."""
        path = Path(path)
        errors = []
        
        if not path.exists():
            errors.append(f"Path does not exist: {path}")
            return False, errors
        
        if not path.is_dir():
            errors.append(f"Path is not a directory: {path}")
            return False, errors
        
        manifest_file = path / self.MANIFEST_FILENAME
        if not manifest_file.exists():
            errors.append(f"No {self.MANIFEST_FILENAME} found in {path}")
            return False, errors
        
        try:
            with open(manifest_file) as f:
                manifest_data = json.load(f)
            manifest = PackManifest.from_dict(manifest_data)
        except Exception as e:
            errors.append(f"Invalid manifest: {e}")
            return False, errors
        
        if not manifest.name:
            errors.append("Manifest missing required field: name")
        if not manifest.version:
            errors.append("Manifest missing required field: version")
        
        has_integrations = False
        for pattern in ["*.py", "*_integration.py", "integration.py"]:
            if list(path.glob(pattern)):
                has_integrations = True
                break
        
        if not has_integrations:
            errors.append("No integration files found in pack")
        
        return len(errors) == 0, errors
    
    def get_statistics(self) -> dict[str, Any]:
        """Get pack manager statistics."""
        by_source = {}
        for pack_info in self._installed.values():
            source = pack_info.source
            by_source[source] = by_source.get(source, 0) + 1
        
        return {
            "total_installed": len(self._installed),
            "by_source": by_source,
            "install_root": str(self.install_root),
        }


# Global pack manager instance
_pack_manager: IntegrationPackManager | None = None


def get_pack_manager(
    config: Any = None,
    loader: Any = None,
    install_root: str | Path | None = None,
) -> IntegrationPackManager:
    """Get the global pack manager instance."""
    global _pack_manager
    
    if _pack_manager is None:
        _pack_manager = IntegrationPackManager(config, loader, install_root)
    
    return _pack_manager
