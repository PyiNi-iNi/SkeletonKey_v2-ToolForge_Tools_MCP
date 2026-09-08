"""Example integrations for SkeletonKey.

This module contains example implementations of various integration types
to demonstrate the integration framework.

The integrations are registered automatically when this module is imported.
"""

from __future__ import annotations

from typing import Any

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

# Try to import integrations with external dependencies
try:
    from .rest_api import RESTAPIIntegration
    _has_rest = True
except ImportError:
    _has_rest = False

try:
    from .aws_devkit import AWSDevKitIntegration
    _has_aws = True
except ImportError:
    _has_aws = False

__all__ = [
    'Integration',
    'IntegrationCapability',
    'IntegrationCategory',
    'IntegrationConfig',
    'IntegrationDependency',
    'IntegrationPlatform',
    'IntegrationType',
    'register_integration',
    'SimpleDemoIntegration',
]

if _has_rest:
    __all__.append('RESTAPIIntegration')
if _has_aws:
    __all__.append('AWSDevKitIntegration')
