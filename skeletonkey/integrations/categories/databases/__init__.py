"""Database Integrations for SkeletonKey (P5.4).

This module provides integrations with various database systems:
- SQLite (built-in)
- PostgreSQL
- MySQL
- MongoDB
- Redis
- Elasticsearch
- And more...

Each database integration provides:
- Connection management
- Query execution
- Result formatting
- Error handling
- Health checks
"""

from __future__ import annotations

# Import all database integrations to register them
try:
    from . import sqlite  # noqa: F401
except ImportError:
    pass

try:
    from . import postgres  # noqa: F401
except ImportError:
    pass

try:
    from . import mysql  # noqa: F401
except ImportError:
    pass

try:
    from . import mongodb  # noqa: F401
except ImportError:
    pass

try:
    from . import redis  # noqa: F401
except ImportError:
    pass
