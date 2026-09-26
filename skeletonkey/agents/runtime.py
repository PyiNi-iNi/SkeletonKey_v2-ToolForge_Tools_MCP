"""Runtime bridge from a purpose profile to the existing subagent boundary."""

from __future__ import annotations

from typing import Any

from skeletonkey.core.subagent import Subagent, create_subagent

from .catalog import get_agent_profile


def create_agent(engine: Any, agent_id: str) -> Subagent:
    """Create a bounded purpose agent backed by the real engine.

    The allowlist is derived from the current registry at creation time. Calls
    still go through ``call_as_subagent`` and therefore remain subject to the
    engine's gate, policy, approval, journal, and ledger contracts.
    """
    profile = get_agent_profile(agent_id)
    tool_ids = [m.id for m in engine.registry.all() if profile.matches(m)]
    return create_subagent(engine, spec=profile.subagent_spec(tool_ids))


__all__ = ["create_agent"]
