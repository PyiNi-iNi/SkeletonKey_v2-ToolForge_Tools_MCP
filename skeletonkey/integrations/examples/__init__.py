"""Example integrations for SkeletonKey.

This module contains example implementations of various integration types
to demonstrate the integration framework.

The integrations are registered automatically when this module is imported.
"""

from __future__ import annotations

import importlib.util

from .. import (
    Integration,
    IntegrationCapability,
    IntegrationCategory,
    IntegrationConfig,
    IntegrationDependency,
    IntegrationPlatform,
    IntegrationType,
    register_integration,
)

# Import simple demo (no external dependencies)
from .simple_demo import SimpleDemoIntegration

# Import integrations that carry optional third-party dependencies only when the
# dependency is actually importable (find_spec avoids importing the dependency
# merely to discover whether the integration can register).
if importlib.util.find_spec("httpx") is not None:
    from .rest_api import RESTAPIIntegration  # noqa: F401
if importlib.util.find_spec("boto3") is not None:
    from .aws_devkit import AWSDevKitIntegration  # noqa: F401

__all__ = [
    'Integration',
    'IntegrationCapability',
    'IntegrationCategory',
    'IntegrationConfig',
    'IntegrationDependency',
    'IntegrationPlatform',
    'IntegrationType',
    'SimpleDemoIntegration',
    'register_integration',
]
