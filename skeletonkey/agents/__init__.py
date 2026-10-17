"""Purpose-driven autonomous agent profiles.

The agent directory is a *projection* of the live tool registry, not another
registry.  Profiles describe a mission and a safety envelope; the current
registry decides which tools are actually available for that profile.  Runtime
execution goes through :mod:`skeletonkey.core.subagent`, so the same tier,
read-only, approval, budget, and ledger contracts apply to every agent.
"""

from __future__ import annotations

from .catalog import AGENT_PROFILES, AgentAction, AgentProfile, get_agent_profile
from .model import AgentModel, AgentTool, PurposeAgent, build_agent_model
from .render import render_agent_html
from .runtime import create_agent

__all__ = [
    "AGENT_PROFILES",
    "AgentAction",
    "AgentModel",
    "AgentProfile",
    "AgentTool",
    "PurposeAgent",
    "build_agent_model",
    "create_agent",
    "get_agent_profile",
    "render_agent_html",
]
