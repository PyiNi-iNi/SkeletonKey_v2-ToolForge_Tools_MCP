"""SkeletonKey / ToolForge v2 - adaptive toolset, skills, and MCP for autonomous agents.

Public surface (see PLAN.md):
    Config, Engine, Registry, CapabilityProfile, Prober, ToolResult, ToolManifest
"""

from __future__ import annotations

from .core.config import Config
from .core.engine import ApprovalRequired, CallContext, Engine
from .core.envelope import Artifact, Metrics, ToolResult
from .core.errors import E, ErrorClass, SkeletonKeyError, ToolError
from .core.manifest import Requirement, ToolManifest
from .core.profile import CapabilityProfile, Prober
from .core.registry import Registry
from .version import __version__

__all__ = [
    "ApprovalRequired",
    "Artifact",
    "CallContext",
    "CapabilityProfile",
    "Config",
    "E",
    "Engine",
    "ErrorClass",
    "Metrics",
    "Prober",
    "Registry",
    "Requirement",
    "SkeletonKeyError",
    "ToolError",
    "ToolManifest",
    "ToolResult",
    "__version__",
]
