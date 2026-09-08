"""Simple Demo Integration.

This is a simple integration that doesn't require any external dependencies.
It demonstrates the basic integration structure.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import (
    Integration,
    IntegrationCapability,
    IntegrationCategory,
    IntegrationConfig,
    IntegrationType,
    register_integration,
)

logger = logging.getLogger(__name__)


@register_integration(
    name="simple-demo",
    title="Simple Demo Integration",
    version="1.0.0",
    integration_type=IntegrationType.SDK,
    category=IntegrationCategory.DEVTOOLS,
    platform=None,  # Cross-platform
    description="A simple demo integration that provides basic utilities",
    author="SkeletonKey Team",
    license="MIT",
    keywords=["demo", "example", "simple", "utilities"],
)
class SimpleDemoIntegration(Integration):
    """A simple demo integration.
    
    This integration provides tools for:
    - Echo (repeat back input)
    - Greet (say hello)
    - Calculate (basic math)
    """
    
    def __init__(self, config: IntegrationConfig):
        super().__init__(config)
        self._initialized = False
        self._greeting = config.config.get("greeting", "Hello")
    
    def initialize(self) -> bool:
        """Initialize the simple demo integration."""
        try:
            self._initialized = True
            logger.info(f"Simple Demo integration initialized (greeting={self._greeting})")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Simple Demo: {e}")
            self._error = str(e)
            return False
    
    def cleanup(self) -> None:
        """Clean up the simple demo integration."""
        self._initialized = False
        logger.info("Simple Demo integration cleaned up")
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the simple demo integration."""
        if not self._initialized:
            return ("unhealthy", "Integration not initialized")
        return ("healthy", "Simple Demo is ready")
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "simple.echo",
                "name": "Echo",
                "description": "Repeat back the input",
                "category": "utilities",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message to echo back",
                        },
                    },
                    "required": ["message"],
                },
            },
            {
                "id": "simple.greet",
                "name": "Greet",
                "description": "Say hello with a custom greeting",
                "category": "utilities",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Name to greet",
                            "default": "World",
                        },
                    },
                    "required": [],
                },
            },
            {
                "id": "simple.add",
                "name": "Add Numbers",
                "description": "Add two numbers together",
                "category": "utilities",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "a": {
                            "type": "number",
                            "description": "First number",
                        },
                        "b": {
                            "type": "number",
                            "description": "Second number",
                        },
                    },
                    "required": ["a", "b"],
                },
            },
        ]
    
    def get_config_schema(self) -> dict[str, Any] | None:
        """Get the configuration schema for this integration."""
        return {
            "type": "object",
            "properties": {
                "greeting": {
                    "type": "string",
                    "description": "Default greeting message",
                    "default": "Hello",
                },
            },
            "required": [],
        }
    
    def validate_config(self, config: dict[str, Any]) -> tuple[bool, str]:
        """Validate configuration for this integration."""
        if "greeting" in config:
            greeting = config["greeting"]
            if not isinstance(greeting, str) or not greeting.strip():
                return (False, "greeting must be a non-empty string")
        return (True, "")
