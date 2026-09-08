"""HTTP Client Integrations for SkeletonKey (P5.4).

This module provides integrations with various HTTP client libraries:
- requests (synchronous)
- httpx (synchronous and asynchronous)
- aiohttp (asynchronous)
- urllib3 (low-level)

Each HTTP client integration provides:
- GET, POST, PUT, DELETE, PATCH methods
- Request/response handling
- Authentication support
- Proxy support
- SSL/TLS configuration
- Timeout configuration
- Health checks
"""

from __future__ import annotations

# Import all HTTP client integrations to register them
try:
    from . import requests_client  # noqa: F401
except ImportError:
    pass

try:
    from . import httpx_client  # noqa: F401
except ImportError:
    pass

try:
    from . import aiohttp_client  # noqa: F401
except ImportError:
    pass
