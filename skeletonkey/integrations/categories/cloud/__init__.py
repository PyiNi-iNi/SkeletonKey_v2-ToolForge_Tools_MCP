"""Cloud Integrations for SkeletonKey (P5.4).

This module provides integrations with various cloud platforms:
- AWS (S3, EC2, Lambda, etc.)
- Google Cloud (Storage, Compute, Functions, etc.)
- Azure (Blob Storage, VMs, Functions, etc.)
- And more...

Each cloud integration provides:
- Authentication management
- Resource operations
- Service-specific features
- Error handling
- Health checks
"""

from __future__ import annotations

# Import all cloud integrations to register them
try:
    from . import aws_s3  # noqa: F401
except ImportError:
    pass

try:
    from . import gcp_storage  # noqa: F401
except ImportError:
    pass

try:
    from . import azure_blob  # noqa: F401
except ImportError:
    pass
