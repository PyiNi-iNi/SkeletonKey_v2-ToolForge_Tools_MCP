"""DevTools Integrations for SkeletonKey (P5.5).

Developer-tool and source-hosting platform SDKs:

- GitHub (repositories, issues, pull requests, search) - stdlib urllib, no
  mandatory deps; PyGithub is an optional accelerator.

Each integration follows the same contract: ``initialize`` / ``cleanup`` /
``check_health`` plus ``get_tools()`` returning the tool metadata whose
``<integration>.<suffix>`` ids map to ``tool_<suffix>`` handlers.
"""

from __future__ import annotations

# Import all devtools integrations to register them
try:
    from . import github  # noqa: F401
except ImportError:
    pass

try:
    from . import stripe  # noqa: F401
except ImportError:
    pass
