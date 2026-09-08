"""Integration Categories for SkeletonKey (P5.4).

This package contains implementations of specific integration categories:
- Databases (SQLite, PostgreSQL, MongoDB, Redis, etc.)
- Cloud Services (AWS, GCP, Azure, etc.)
- HTTP Clients (requests, httpx, aiohttp, etc.)
- APIs (REST, GraphQL, gRPC, etc.)
- CLIs (git, docker, kubectl, etc.)
- Frameworks (Django, Flask, FastAPI, etc.)
- Toolchains (poetry, pip, npm, etc.)
- And more...

Each category has its own submodule with specific integrations.
"""

from __future__ import annotations

# Import all category modules to register their integrations
# This ensures all integrations are registered when this module is imported

# Database integrations
try:
    from . import databases  # noqa: F401
except ImportError:
    pass

# Cloud integrations
try:
    from . import cloud  # noqa: F401
except ImportError:
    pass

# HTTP client integrations
try:
    from . import http_clients  # noqa: F401
except ImportError:
    pass

# API integrations
try:
    from . import apis  # noqa: F401
except ImportError:
    pass

# CLI integrations
try:
    from . import clis  # noqa: F401
except ImportError:
    pass

# Framework integrations
try:
    from . import frameworks  # noqa: F401
except ImportError:
    pass

# Toolchain integrations
try:
    from . import toolchains  # noqa: F401
except ImportError:
    pass
