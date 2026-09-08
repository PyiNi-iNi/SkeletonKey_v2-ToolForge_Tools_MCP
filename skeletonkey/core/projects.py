"""Project Indexer - External project discovery and integration.

This module provides the foundation for discovering, indexing, and integrating
external projects into the SkeletonKey toolkit. It enables automatic detection
of skills, tool packs, Python packages, and MCP servers within the workspace.

Phase: P5 (Integration Foundation)
Status: In Development
Author: Arena AI
Date: 2026-09-07
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .errors import E, SkeletonKeyError

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- Types
@dataclass
class ProjectInfo:
    """Information about an indexed project.
    
    Attributes:
        name: Project name (directory basename or package name)
        path: Absolute filesystem path to the project
        type: Project type (skill, tool-pack, python-package, mcp-server, unknown)
        tools: List of tool IDs provided by this project
        enabled: Whether the project's tools are currently enabled
        description: Human-readable description
        version: Project version (if available)
        metadata: Additional project-specific metadata
        config: Project configuration (if available)
        source: Source of discovery (filesystem, entrypoint, remote, etc.)
        health: Health status (healthy, degraded, unavailable)
        health_message: Human-readable health status message
    """
    name: str
    path: str
    type: str = "unknown"
    tools: list[str] = field(default_factory=list)
    enabled: bool = False
    description: str = ""
    version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    source: str = "filesystem"
    health: str = "unknown"
    health_message: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "tools": self.tools,
            "enabled": self.enabled,
            "description": self.description,
            "version": self.version,
            "metadata": self.metadata,
            "config": self.config,
            "source": self.source,
            "health": self.health,
            "health_message": self.health_message,
        }
    
    def __repr__(self) -> str:
        return (f"ProjectInfo(name={self.name!r}, path={self.path!r}, "
                f"type={self.type!r}, enabled={self.enabled}, "
                f"tools={len(self.tools)})")

# Project type constants
PROJECT_TYPE_SKILL = "skill"
PROJECT_TYPE_TOOL_PACK = "tool-pack"
PROJECT_TYPE_PYTHON_PACKAGE = "python-package"
PROJECT_TYPE_MCP_SERVER = "mcp-server"
PROJECT_TYPE_DROPIN = "dropin"
PROJECT_TYPE_ENTRYPOINT = "entrypoint"
PROJECT_TYPE_REMOTE = "remote"
PROJECT_TYPE_UNKNOWN = "unknown"

# Project type detection markers
PROJECT_MARKERS = {
    PROJECT_TYPE_SKILL: ["SKILL.md"],
    PROJECT_TYPE_TOOL_PACK: ["tool.toml"],
    PROJECT_TYPE_PYTHON_PACKAGE: ["pyproject.toml", "setup.py", "setup.cfg"],
    PROJECT_TYPE_MCP_SERVER: [".mcp.json", "mcp.json", "mcp.config.json"],
}

# Valid project types
VALID_PROJECT_TYPES = {
    PROJECT_TYPE_SKILL,
    PROJECT_TYPE_TOOL_PACK,
    PROJECT_TYPE_PYTHON_PACKAGE,
    PROJECT_TYPE_MCP_SERVER,
    PROJECT_TYPE_DROPIN,
    PROJECT_TYPE_ENTRYPOINT,
    PROJECT_TYPE_REMOTE,
    PROJECT_TYPE_UNKNOWN,
}


# --------------------------------------------------------------------------- Project Indexer
class ProjectIndexer:
    """Index and manage external projects for integration with SkeletonKey.
    
    The ProjectIndexer scans specified directories for projects that can be
    integrated with SkeletonKey. It detects project types based on marker
    files and provides information about each project's capabilities.
    
    Example usage:
        >>> indexer = ProjectIndexer(roots=[".", "~/projects"])
        >>> projects = indexer.list_projects()
        >>> for project in projects:
        ...     print(f"{project.name}: {project.type} - {len(project.tools)} tools")
    """
    
    def __init__(
        self,
        roots: list[str] | None = None,
        *,
        include_disabled: bool = True,
        auto_refresh: bool = False,
        cache_enabled: bool = True,
    ):
        """Initialize the project indexer.
        
        Args:
            roots: List of directories to scan for projects. If None, uses
                  the current working directory.
            include_disabled: Whether to include disabled projects in results.
            auto_refresh: Whether to automatically refresh the index on access.
            cache_enabled: Whether to cache project information.
        """
        self.roots = [os.path.abspath(r) for r in (roots or [os.getcwd()])]
        self.include_disabled = include_disabled
        self.auto_refresh = auto_refresh
        self.cache_enabled = cache_enabled
        
        self._projects: dict[str, ProjectInfo] = {}
        self._cache: dict[str, Any] = {}
        self._last_scan: float | None = None
        self._scan_count = 0
        
        # Scan immediately on initialization
        self._scan()
    
    def _scan(self) -> dict[str, ProjectInfo]:
        """Scan all roots for projects.
        
        Returns:
            Dictionary mapping project names to ProjectInfo objects.
        """
        projects: dict[str, ProjectInfo] = {}
        
        for root in self.roots:
            if not os.path.isdir(root):
                logger.warning(f"Project root does not exist or is not a directory: {root}")
                continue
            
            self._scan_directory(root, projects)
        
        self._projects = projects
        self._last_scan = self._scan_count
        self._scan_count += 1
        
        logger.info(f"Scanned {len(self.roots)} roots, found {len(projects)} projects")
        return projects
    
    def _scan_directory(
        self,
        directory: str,
        projects: dict[str, ProjectInfo],
        depth: int = 0,
        max_depth: int = 5,
    ) -> None:
        """Recursively scan a directory for projects.
        
        Args:
            directory: Directory to scan.
            projects: Dictionary to populate with found projects.
            depth: Current recursion depth.
            max_depth: Maximum recursion depth.
        """
        if depth > max_depth:
            return
        
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        path = entry.path
                        
                        # Check if this directory is a project
                        project = self._inspect_project(path)
                        if project:
                            # Use a unique key (name + path to handle duplicates)
                            key = f"{project.name}@{path}"
                            projects[key] = project
                            continue
                        
                        # Recurse into subdirectories
                        self._scan_directory(path, projects, depth + 1, max_depth)
        except (OSError, PermissionError) as e:
            logger.warning(f"Error scanning directory {directory}: {e}")
    
    def _inspect_project(self, path: str) -> ProjectInfo | None:
        """Inspect a directory to determine if it's a project.
        
        Args:
            path: Directory path to inspect.
            
        Returns:
            ProjectInfo if the directory is a project, None otherwise.
        """
        # Check for known project markers
        for project_type, markers in PROJECT_MARKERS.items():
            for marker in markers:
                if os.path.exists(os.path.join(path, marker)):
                    return self._inspect_specific_project(path, project_type)
        
        # Check for tool.py files (drop-in tools)
        if any(
            f.endswith(".py") and not f.startswith(("_", "."))
            for f in os.listdir(path)
        ):
            return self._inspect_dropin_project(path)
        
        return None
    
    def _inspect_specific_project(
        self,
        path: str,
        project_type: str,
    ) -> ProjectInfo:
        """Inspect a project of a specific type.
        
        Args:
            path: Directory path.
            project_type: Known project type.
            
        Returns:
            ProjectInfo for the project.
        """
        name = os.path.basename(path)
        
        handlers = {
            PROJECT_TYPE_SKILL: self._inspect_skill_project,
            PROJECT_TYPE_TOOL_PACK: self._inspect_tool_pack_project,
            PROJECT_TYPE_PYTHON_PACKAGE: self._inspect_python_package,
            PROJECT_TYPE_MCP_SERVER: self._inspect_mcp_server_project,
        }
        
        handler = handlers.get(project_type)
        if handler:
            return handler(path, name)
        
        # Default handler
        return ProjectInfo(
            name=name,
            path=path,
            type=project_type,
            source="filesystem",
        )
    
    def _inspect_skill_project(self, path: str, name: str) -> ProjectInfo:
        """Inspect a skill project."""
        skill_file = os.path.join(path, "SKILL.md")
        tool_file = os.path.join(path, "tool.toml")
        
        description = ""
        version = ""
        tools: list[str] = []
        
        # Read SKILL.md for metadata
        try:
            with open(skill_file, encoding="utf-8") as f:
                content = f.read()
                # Extract frontmatter if present
                if content.startswith("---"):
                    end_marker = content.find("---", 3)
                    if end_marker != -1:
                        frontmatter = content[3:end_marker].strip()
                        # Simple YAML-like parsing (for basic fields)
                        for line in frontmatter.split("\n"):
                            if ":" in line:
                                key, _, value = line.partition(":")
                                key = key.strip().lower()
                                value = value.strip()
                                if key == "name":
                                    name = value
                                elif key == "description":
                                    description = value
                                elif key == "version":
                                    version = value
        except (OSError, UnicodeDecodeError):
            pass
        
        # Read tool.toml if present
        try:
            import tomli
            with open(tool_file, "rb") as f:
                tool_config = tomli.load(f)
                if "tools" in tool_config:
                    for tool in tool_config["tools"]:
                        if "id" in tool:
                            tools.append(tool["id"])
        except ImportError:
            # tomli not installed, try to parse as JSON or skip
            try:
                import json
                with open(tool_file, encoding="utf-8") as f:
                    tool_config = json.load(f)
                    if "tools" in tool_config:
                        for tool in tool_config["tools"]:
                            if "id" in tool:
                                tools.append(tool["id"])
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass
        except (OSError, Exception):
            pass
        
        return ProjectInfo(
            name=name,
            path=path,
            type=PROJECT_TYPE_SKILL,
            tools=tools,
            enabled=True,  # Skills are enabled by default
            description=description or f"Skill: {name}",
            version=version,
            source="filesystem",
            metadata={"skill_file": "SKILL.md"},
        )
    
    def _inspect_tool_pack_project(self, path: str, name: str) -> ProjectInfo:
        """Inspect a tool pack project."""
        tool_file = os.path.join(path, "tool.toml")
        
        tools: list[str] = []
        description = ""
        version = ""
        
        try:
            import tomli
            with open(tool_file, "rb") as f:
                tool_config = tomli.load(f)
                
                if "name" in tool_config:
                    name = tool_config["name"]
                if "description" in tool_config:
                    description = tool_config["description"]
                if "version" in tool_config:
                    version = tool_config["version"]
                
                if "tools" in tool_config:
                    for tool in tool_config["tools"]:
                        if "id" in tool:
                            tools.append(tool["id"])
        except ImportError:
            # tomli not installed, try to parse as JSON or skip
            try:
                import json
                with open(tool_file, encoding="utf-8") as f:
                    tool_config = json.load(f)
                    
                    if "name" in tool_config:
                        name = tool_config["name"]
                    if "description" in tool_config:
                        description = tool_config["description"]
                    if "version" in tool_config:
                        version = tool_config["version"]
                    
                    if "tools" in tool_config:
                        for tool in tool_config["tools"]:
                            if "id" in tool:
                                tools.append(tool["id"])
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                pass
        except (OSError, Exception):
            pass
        
        return ProjectInfo(
            name=name,
            path=path,
            type=PROJECT_TYPE_TOOL_PACK,
            tools=tools,
            enabled=True,
            description=description or f"Tool pack: {name}",
            version=version,
            source="filesystem",
            metadata={"tool_file": "tool.toml"},
        )
    
    def _inspect_python_package(self, path: str, name: str) -> ProjectInfo:
        """Inspect a Python package project."""
        pyproject_file = os.path.join(path, "pyproject.toml")
        setup_file = os.path.join(path, "setup.py")
        
        description = ""
        version = ""
        tools: list[str] = []
        
        # Try pyproject.toml first
        try:
            import tomli
            with open(pyproject_file, "rb") as f:
                project = tomli.load(f).get("project", {})
                
                if "name" in project:
                    name = project["name"]
                if "description" in project:
                    description = project["description"]
                if "version" in project:
                    version = project["version"]
                
                # Check for SkeletonKey entry points
                entry_points = project.get("entry-points", {})
                if "skeletonkey.tools" in entry_points:
                    tools.extend(entry_points["skeletonkey.tools"].keys())
        except ImportError:
            # tomli not installed, skip pyproject.toml
            pass
        except (OSError, Exception):
            pass
        
        # Try setup.py as fallback
        if not description:
            try:
                with open(setup_file, encoding="utf-8") as f:
                    content = f.read()
                    # Simple extraction (very basic)
                    for line in content.split("\n"):
                        if "name=" in line:
                            name = line.split("=")[1].strip().strip("'\"")
                        elif "description=" in line:
                            description = line.split("=")[1].strip().strip("'\"")
                        elif "version=" in line:
                            version = line.split("=")[1].strip().strip("'\"")
            except (OSError, UnicodeDecodeError):
                pass
        
        return ProjectInfo(
            name=name,
            path=path,
            type=PROJECT_TYPE_PYTHON_PACKAGE,
            tools=tools,
            enabled=bool(tools),  # Only enabled if it has SkeletonKey tools
            description=description or f"Python package: {name}",
            version=version,
            source="filesystem",
            metadata={"package_file": "pyproject.toml" if os.path.exists(pyproject_file) else "setup.py"},
        )
    
    def _inspect_mcp_server_project(self, path: str, name: str) -> ProjectInfo:
        """Inspect an MCP server project."""
        config_files = [".mcp.json", "mcp.json", "mcp.config.json"]
        
        config: dict[str, Any] = {}
        tools: list[str] = []
        
        for config_file in config_files:
            config_path = os.path.join(path, config_file)
            if os.path.exists(config_path):
                try:
                    with open(config_path, encoding="utf-8") as f:
                        config = json.load(f)
                    break
                except (OSError, json.JSONDecodeError):
                    continue
        
        # Extract tools from MCP config
        if "mcpServers" in config:
            for server_name, server_config in config["mcpServers"].items():
                if "command" in server_config or "url" in server_config:
                    tools.append(f"remote.{server_name}.*")
        
        # Extract from tools list if present
        if "tools" in config:
            tools.extend(config["tools"])
        
        return ProjectInfo(
            name=name,
            path=path,
            type=PROJECT_TYPE_MCP_SERVER,
            tools=tools,
            enabled=bool(tools),
            description=f"MCP server: {name}",
            source="filesystem",
            config=config,
            metadata={"config_file": next((f for f in config_files if os.path.exists(os.path.join(path, f))), None)},
        )
    
    def _inspect_dropin_project(self, path: str) -> ProjectInfo | None:
        """Inspect a drop-in tools directory."""
        tools: list[str] = []
        
        for filename in os.listdir(path):
            if filename.endswith(".py") and not filename.startswith(("_", ".")):
                tool_name = filename[:-3]  # Remove .py extension
                tools.append(f"dropin.{tool_name}")
        
        if not tools:
            return None
        
        return ProjectInfo(
            name=os.path.basename(path),
            path=path,
            type=PROJECT_TYPE_DROPIN,
            tools=tools,
            enabled=True,
            description=f"Drop-in tools: {os.path.basename(path)}",
            source="filesystem",
            metadata={"tool_count": len(tools)},
        )
    
    def list_projects(
        self,
        *,
        type_filter: str | list[str] | None = None,
        enabled_only: bool | None = None,
        name_pattern: str | None = None,
    ) -> list[ProjectInfo]:
        """List all indexed projects.
        
        Args:
            type_filter: Filter by project type(s).
            enabled_only: If True, only return enabled projects.
                         If False, only return disabled projects.
                         If None, return all projects.
            name_pattern: Filter by name pattern (substring match).
            
        Returns:
            List of ProjectInfo objects.
        """
        # Auto-refresh if enabled
        if self.auto_refresh and self._last_scan != self._scan_count - 1:
            self._scan()
        
        projects = list(self._projects.values())
        
        # Apply filters
        if type_filter:
            types = [type_filter] if isinstance(type_filter, str) else type_filter
            projects = [p for p in projects if p.type in types]
        
        if enabled_only is not None:
            projects = [p for p in projects if p.enabled == enabled_only]
        
        if name_pattern:
            pattern = name_pattern.lower()
            projects = [p for p in projects if pattern in p.name.lower()]
        
        return projects
    
    def get_project(self, name: str) -> ProjectInfo | None:
        """Get a specific project by name.
        
        Args:
            name: Project name to retrieve.
            
        Returns:
            ProjectInfo if found, None otherwise.
        """
        # Auto-refresh if enabled
        if self.auto_refresh and self._last_scan != self._scan_count - 1:
            self._scan()
        
        # Try exact match first
        for _key, project in self._projects.items():
            if project.name == name:
                return project
        
        # Try partial match
        matches = [p for p in self._projects.values() if name.lower() in p.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SkeletonKeyError(
                E.AMBIGUOUS_MATCH,
                f"Multiple projects match '{name}'",
                details={"matches": [p.name for p in matches]},
            )
        
        return None
    
    def get_project_by_path(self, path: str) -> ProjectInfo | None:
        """Get a project by its filesystem path.
        
        Args:
            path: Absolute filesystem path.
            
        Returns:
            ProjectInfo if found, None otherwise.
        """
        path = os.path.abspath(path)
        for project in self._projects.values():
            if project.path == path:
                return project
        return None
    
    def refresh(self) -> dict[str, Any]:
        """Manually refresh the project index.
        
        Returns:
            Dictionary with refresh statistics.
        """
        old_count = len(self._projects)
        self._scan()
        new_count = len(self._projects)
        
        return {
            "old_count": old_count,
            "new_count": new_count,
            "delta": new_count - old_count,
            "projects": list(self._projects.keys()),
        }
    
    def add_project(self, project: ProjectInfo) -> None:
        """Manually add a project to the index.
        
        Args:
            project: ProjectInfo to add.
        """
        key = f"{project.name}@{project.path}"
        self._projects[key] = project
        logger.info(f"Added project: {project.name} ({project.type})")
    
    def remove_project(self, name: str) -> ProjectInfo | None:
        """Remove a project from the index.
        
        Args:
            name: Project name to remove.
            
        Returns:
            The removed ProjectInfo, or None if not found.
        """
        for key, project in list(self._projects.items()):
            if project.name == name:
                del self._projects[key]
                logger.info(f"Removed project: {project.name}")
                return project
        return None
    
    def enable_project(self, name: str) -> ProjectInfo | None:
        """Enable a project.
        
        Args:
            name: Project name to enable.
            
        Returns:
            The updated ProjectInfo, or None if not found.
        """
        project = self.get_project(name)
        if project:
            project.enabled = True
            logger.info(f"Enabled project: {project.name}")
        return project
    
    def disable_project(self, name: str) -> ProjectInfo | None:
        """Disable a project.
        
        Args:
            name: Project name to disable.
            
        Returns:
            The updated ProjectInfo, or None if not found.
        """
        project = self.get_project(name)
        if project:
            project.enabled = False
            logger.info(f"Disabled project: {project.name}")
        return project
    
    def check_health(self, name: str) -> dict[str, Any]:
        """Check the health of a project.
        
        Args:
            name: Project name to check.
            
        Returns:
            Dictionary with health status information.
        """
        project = self.get_project(name)
        if not project:
            raise SkeletonKeyError(
                E.ENOENT,
                f"Project not found: {name}",
                details={"available_projects": list(self._projects.keys())},
            )
        
        # Default health check based on project type
        if project.type == PROJECT_TYPE_SKILL:
            return self._check_skill_health(project)
        if project.type == PROJECT_TYPE_PYTHON_PACKAGE:
            return self._check_package_health(project)
        if project.type == PROJECT_TYPE_MCP_SERVER:
            return self._check_mcp_health(project)
        return {
            "project": project.name,
            "health": "healthy",
            "message": "Project exists and is accessible",
        }
    
    def _check_skill_health(self, project: ProjectInfo) -> dict[str, Any]:
        """Check health of a skill project."""
        skill_file = os.path.join(project.path, "SKILL.md")
        
        if not os.path.exists(skill_file):
            return {
                "project": project.name,
                "health": "unavailable",
                "message": "SKILL.md file is missing",
            }
        
        try:
            with open(skill_file, encoding="utf-8") as f:
                content = f.read()
                if not content.strip():
                    return {
                        "project": project.name,
                        "health": "degraded",
                        "message": "SKILL.md file is empty",
                    }
        except (OSError, UnicodeDecodeError):
            return {
                "project": project.name,
                "health": "unavailable",
                "message": "Cannot read SKILL.md file",
            }
        
        return {
            "project": project.name,
            "health": "healthy",
            "message": "Skill project is accessible",
        }
    
    def _check_package_health(self, project: ProjectInfo) -> dict[str, Any]:
        """Check health of a Python package project."""
        pyproject_file = os.path.join(project.path, "pyproject.toml")
        setup_file = os.path.join(project.path, "setup.py")
        
        if not (os.path.exists(pyproject_file) or os.path.exists(setup_file)):
            return {
                "project": project.name,
                "health": "unavailable",
                "message": "No package configuration file found",
            }
        
        # Check if package can be imported
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                project.name, 
                os.path.join(project.path, project.name, "__init__.py")
            )
            if spec is None:
                # Try without __init__.py
                spec = importlib.util.spec_from_file_location(
                    project.name,
                    os.path.join(project.path, project.name + ".py")
                )
            
            if spec is None:
                return {
                    "project": project.name,
                    "health": "degraded",
                    "message": "Package cannot be imported (may not be installed)",
                }
        except Exception:
            return {
                "project": project.name,
                "health": "degraded",
                "message": "Package import check failed",
            }
        
        return {
            "project": project.name,
            "health": "healthy",
            "message": "Python package is accessible",
        }
    
    def _check_mcp_health(self, project: ProjectInfo) -> dict[str, Any]:
        """Check health of an MCP server project."""
        config_files = [".mcp.json", "mcp.json", "mcp.config.json"]
        
        config_path = None
        for filename in config_files:
            path = os.path.join(project.path, filename)
            if os.path.exists(path):
                config_path = path
                break
        
        if not config_path:
            return {
                "project": project.name,
                "health": "unavailable",
                "message": "No MCP configuration file found",
            }
        
        try:
            with open(config_path, encoding="utf-8") as f:
                config = json.load(f)
                if not config:
                    return {
                        "project": project.name,
                        "health": "degraded",
                        "message": "MCP configuration file is empty",
                    }
        except (OSError, json.JSONDecodeError):
            return {
                "project": project.name,
                "health": "unavailable",
                "message": "Cannot read MCP configuration file",
            }
        
        return {
            "project": project.name,
            "health": "healthy",
            "message": "MCP server configuration is accessible",
        }
    
    def get_tools_by_project(self, name: str) -> list[str]:
        """Get all tools provided by a project.
        
        Args:
            name: Project name.
            
        Returns:
            List of tool IDs.
        """
        project = self.get_project(name)
        if not project:
            raise SkeletonKeyError(
                E.ENOENT,
                f"Project not found: {name}",
                details={"available_projects": list(self._projects.keys())},
            )
        return project.tools
    
    def get_projects_by_type(self, project_type: str) -> list[ProjectInfo]:
        """Get all projects of a specific type.
        
        Args:
            project_type: Project type to filter by.
            
        Returns:
            List of ProjectInfo objects.
        """
        if project_type not in VALID_PROJECT_TYPES:
            raise SkeletonKeyError(
                E.BAD_ARGS,
                f"Invalid project type: {project_type}",
                details={"valid_types": list(VALID_PROJECT_TYPES)},
            )
        return [p for p in self._projects.values() if p.type == project_type]
    
    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about indexed projects.
        
        Returns:
            Dictionary with project statistics.
        """
        projects = list(self._projects.values())
        
        by_type: dict[str, int] = {}
        by_source: dict[str, int] = {}
        by_health: dict[str, int] = {}
        
        for project in projects:
            by_type[project.type] = by_type.get(project.type, 0) + 1
            by_source[project.source] = by_source.get(project.source, 0) + 1
            by_health[project.health] = by_health.get(project.health, 0) + 1
        
        total_tools = sum(len(p.tools) for p in projects)
        enabled_count = sum(1 for p in projects if p.enabled)
        
        return {
            "total_projects": len(projects),
            "total_tools": total_tools,
            "enabled_projects": enabled_count,
            "disabled_projects": len(projects) - enabled_count,
            "by_type": by_type,
            "by_source": by_source,
            "by_health": by_health,
            "roots": self.roots,
        }


# --------------------------------------------------------------------------- Convenience Functions
def scan_projects(
    roots: list[str] | None = None,
    *,
    include_disabled: bool = True,
) -> list[ProjectInfo]:
    """Convenience function to scan for projects.
    
    Args:
        roots: List of directories to scan.
        include_disabled: Whether to include disabled projects.
        
    Returns:
        List of ProjectInfo objects.
    """
    indexer = ProjectIndexer(
        roots=roots,
        include_disabled=include_disabled,
        auto_refresh=False,
    )
    return indexer.list_projects()


def get_project_info(path: str) -> ProjectInfo | None:
    """Convenience function to get project info for a directory.
    
    Args:
        path: Directory path to inspect.
        
    Returns:
        ProjectInfo if the directory is a project, None otherwise.
    """
    indexer = ProjectIndexer(
        roots=[os.path.dirname(path)],
        auto_refresh=False,
    )
    return indexer.get_project_by_path(path)


# --------------------------------------------------------------------------- Exports
__all__ = [
    "PROJECT_MARKERS",
    "PROJECT_TYPE_DROPIN",
    "PROJECT_TYPE_ENTRYPOINT",
    "PROJECT_TYPE_MCP_SERVER",
    "PROJECT_TYPE_PYTHON_PACKAGE",
    "PROJECT_TYPE_REMOTE",
    # Type constants
    "PROJECT_TYPE_SKILL",
    "PROJECT_TYPE_TOOL_PACK",
    "PROJECT_TYPE_UNKNOWN",
    "VALID_PROJECT_TYPES",
    "ProjectIndexer",
    "ProjectInfo",
    "get_project_info",
    "scan_projects",
]
