"""Bridge: wire loaded integration instance tools into the engine Registry.

An integration's ``get_tools()`` returns lightweight dicts (id, schema, risk,
tags, ...). That is metadata only; nothing in the P5.4 merge connected those
dicts to the engine, so the tools were never callable. This module closes that
gap: for every tool a *loaded* integration declares, it builds an engine
``ToolManifest`` whose handler dispatches to the instance's ``tool_<name>``
method, and registers it into the toolkit ``Registry``.

Lifecycle contract (owned by ``IntegrationManager``):

- ``load``   -> ``register_instance_tools(...)``
- ``unload`` -> ``unregister_instance_tools(...)``

Only *loaded* integrations contribute tools: an integration's tools are
gated behind the same opt-in load step as the integration itself, and the
capability/sandbox model on the integration metadata stays the authority for
who may enable it.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.manifest import ToolManifest

logger = logging.getLogger(__name__)

_VALID_RISKS = {"none", "read", "write", "destructive", "network", "privileged"}
_VALID_TIERS = {"core", "task", "full"}


def tool_suffix(integration_name: str, tool_id: str) -> str | None:
    """Derive the ``tool_<suffix>`` method name from a tool id, or None.

    The convention is ``<integration-name>.<suffix>`` -> ``tool_<suffix>``,
    with dots and hyphens in the suffix normalized to underscores so nested
    namespaces map onto flat method names::

        sqlite.create_table         -> tool_create_table
        requests.get                -> tool_get
        github.repo.get             -> tool_repo_get
        stripe.payment_intents.list -> tool_payment_intents_list

    An id whose namespace does not match the integration name is unmappable
    (None) unless it is a single-dot id, in which case the suffix is taken from
    the final segment as a fallback.
    """
    prefix = f"{integration_name}."
    if tool_id.startswith(prefix):
        suffix = tool_id[len(prefix):]
    elif "." in tool_id and tool_id.count(".") == 1:
        suffix = tool_id.split(".", 1)[1]
    else:
        return None
    if not suffix:
        return None
    return suffix.replace(".", "_").replace("-", "_")


def build_manifest(
    instance: Any,
    integration_name: str,
    tool: dict[str, Any],
    integration_sandbox: Any = None,
) -> ToolManifest | None:
    """Build an engine ToolManifest for one integration tool dict.

    Returns None when the tool is unmappable to a handler (e.g. a legacy demo
    tool that declares metadata but no ``tool_*`` method). Both the modern dict
    shape (``title``/``capability``/``input_schema``/``risk``/``tier``) and the
    legacy P5.1 shape (``name``/``category``/``parameters``) are accepted.
    
    Args:
        instance: The integration instance
        integration_name: Name of the integration
        tool: Tool definition dict
        integration_sandbox: Optional IntegrationPathSandbox for sandbox enforcement
    """
    tool_id = tool.get("id")
    if not tool_id or not isinstance(tool_id, str):
        return None

    suffix = tool_suffix(integration_name, tool_id)
    if suffix is None:
        return None

    handler = _make_handler(instance, suffix, integration_sandbox)
    if handler is None:
        return None

    title = tool.get("title") or tool.get("name") or tool_id
    description = tool.get("description") or ""
    capability = tool.get("capability") or tool.get("category") or tool_id
    input_schema = tool.get("input_schema") or tool.get("parameters") or {
        "type": "object",
        "properties": {},
    }
    risk = tool.get("risk") if tool.get("risk") in _VALID_RISKS else "read"
    tier = tool.get("tier") if tool.get("tier") in _VALID_TIERS else "task"

    # Integration state is mutable and session-scoped to the loaded instance:
    # reads must never be served from the engine's idempotency cache (the DB /
    # remote may have changed), so mark the manifest stateful.
    return ToolManifest(
        id=tool_id,
        title=str(title),
        description=str(description),
        group=integration_name,
        capability=str(capability),
        risk=risk,
        tier=tier,
        tags=[str(t) for t in (tool.get("tags") or [])],
        input_schema=input_schema,
        secret_args=[str(s) for s in (tool.get("secret_args") or [])],
        typical_latency_ms=int(tool.get("typical_latency_ms") or 50),
        open_world=True,
        parallel_safe=False,
        stateful="session",
        session_scope=integration_name,
        source=f"integration:{integration_name}",
        provider=integration_name,
        handler=handler,
    )


def _make_handler(instance: Any, suffix: str, integration_sandbox: Any = None) -> Any | None:
    """Return a handler that dispatches to ``instance.tool_<suffix>(**args)``.

    Returns None when the instance has no such method, so a tool dict with no
    backing implementation is skipped rather than registered as a broken call.
    
    If integration_sandbox is provided, it wraps the handler to enforce
    per-integration sandbox constraints.
    """
    if not callable(getattr(instance, f"tool_{suffix}", None)):
        return None

    base_handler = getattr(instance, f"tool_{suffix}")
    
    if integration_sandbox is None:
        def handler(**kwargs: Any) -> Any:
            return instance.call_tool(suffix, kwargs)
        return handler
    
    def handler(**kwargs: Any) -> Any:
        # Check filesystem access for path-like arguments
        # Heuristic: check string args that look like paths
        for arg_name, arg_value in kwargs.items():
            if isinstance(arg_value, str) and _looks_like_path(arg_value):
                try:
                    integration_sandbox.resolve(arg_value, intent="read")
                except Exception as e:
                    raise SkeletonKeyError(
                        E.SANDBOX_VIOLATION,
                        f"Integration sandbox denied access to {arg_name}={arg_value!r}: {e}",
                        details={"integration": integration_sandbox.integration_name, "arg": arg_name}
                    ) from e
        
        # Check network access if the tool might make network calls
        # This is a heuristic - tools should declare network capability
        # For now, we'll skip automatic network checking and rely on capability declarations
        
        return instance.call_tool(suffix, kwargs)
    
    return handler


def _looks_like_path(s: str) -> bool:
    """Heuristic to detect if a string argument looks like a filesystem path."""
    if not isinstance(s, str) or len(s) > 4096:
        return False
    # Common path patterns
    if s.startswith(("/", "./", "../", "~")):
        return True
    if os.name == "nt" and (len(s) >= 2 and s[1] == ":" and s[0].isalpha()):
        return True
    if "\\" in s or "/" in s:
        # Has path separators
        return True
    return False


# Import for SkeletonKeyError
from ..core.errors import E, SkeletonKeyError
import os
