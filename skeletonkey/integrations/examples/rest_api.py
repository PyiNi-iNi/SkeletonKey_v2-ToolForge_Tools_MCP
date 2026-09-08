"""Example REST API Integration.

This demonstrates how to create a REST API integration that provides
tools for interacting with RESTful APIs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

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
    name="rest-api",
    title="REST API Integration",
    version="1.0.0",
    integration_type=IntegrationType.API,
    category=IntegrationCategory.WEB,
    platform=None,  # Cross-platform
    description="Generic REST API integration for making HTTP requests",
    author="SkeletonKey Team",
    license="MIT",
    keywords=["rest", "api", "http", "https", "request", "endpoint"],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access to make HTTP requests",
            required=True,
        ),
    ],
)
class RESTAPIIntegration(Integration):
    """Integration for making REST API calls.
    
    This integration provides tools for:
    - GET requests
    - POST requests
    - PUT requests
    - DELETE requests
    - PATCH requests
    """
    
    def __init__(self, config: IntegrationConfig):
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._base_url = config.config.get("base_url", "")
        self._default_headers = config.config.get("headers", {})
        self._timeout = float(config.config.get("timeout", 30.0))
        self._follow_redirects = config.config.get("follow_redirects", True)
    
    def initialize(self) -> bool:
        """Initialize the REST API integration."""
        try:
            # Create async HTTP client
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._default_headers,
                timeout=self._timeout,
                follow_redirects=self._follow_redirects,
            )
            self._initialized = True
            logger.info(f"REST API integration initialized (base_url={self._base_url})")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize REST API integration: {e}")
            self._error = str(e)
            return False
    
    def cleanup(self) -> None:
        """Clean up the REST API integration."""
        if self._client:
            try:
                import asyncio
                asyncio.run(self._client.aclose())
            except Exception as e:
                logger.warning(f"Error closing HTTP client: {e}")
            self._client = None
        self._initialized = False
        logger.info("REST API integration cleaned up")
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the REST API integration."""
        if not self._initialized or not self._client:
            return ("unhealthy", "Integration not initialized")
        
        # Try a simple health check
        try:
            # If there's a health check endpoint configured, use it
            health_endpoint = self.config.config.get("health_endpoint")
            if health_endpoint:
                import asyncio
                async def check():
                    try:
                        response = await self._client.get(health_endpoint, timeout=5.0)
                        if response.status_code < 400:
                            return ("healthy", f"Health check passed: {response.status_code}")
                        else:
                            return ("unhealthy", f"Health check failed: {response.status_code}")
                    except Exception as e:
                        return ("unhealthy", f"Health check error: {e}")
                
                result = asyncio.run(check())
                return result
            
            return ("healthy", "Integration is ready")
        except Exception as e:
            return ("unhealthy", str(e))
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "rest.get",
                "name": "REST GET Request",
                "description": "Make a GET request to a REST API endpoint",
                "category": "api",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL to request (relative to base_url if configured)",
                        },
                        "params": {
                            "type": "object",
                            "description": "Query parameters",
                            "additionalProperties": {"type": "string"},
                        },
                        "headers": {
                            "type": "object",
                            "description": "Additional headers",
                            "additionalProperties": {"type": "string"},
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Request timeout in seconds",
                            "default": 30.0,
                        },
                    },
                    "required": ["url"],
                },
            },
            {
                "id": "rest.post",
                "name": "REST POST Request",
                "description": "Make a POST request to a REST API endpoint",
                "category": "api",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL to request",
                        },
                        "body": {
                            "type": ["object", "string"],
                            "description": "Request body",
                        },
                        "headers": {
                            "type": "object",
                            "description": "Additional headers",
                            "additionalProperties": {"type": "string"},
                        },
                        "timeout": {
                            "type": "number",
                            "description": "Request timeout in seconds",
                            "default": 30.0,
                        },
                    },
                    "required": ["url"],
                },
            },
            {
                "id": "rest.put",
                "name": "REST PUT Request",
                "description": "Make a PUT request to a REST API endpoint",
                "category": "api",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL to request",
                        },
                        "body": {
                            "type": ["object", "string"],
                            "description": "Request body",
                        },
                        "headers": {
                            "type": "object",
                            "description": "Additional headers",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["url"],
                },
            },
            {
                "id": "rest.delete",
                "name": "REST DELETE Request",
                "description": "Make a DELETE request to a REST API endpoint",
                "category": "api",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL to request",
                        },
                        "headers": {
                            "type": "object",
                            "description": "Additional headers",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["url"],
                },
            },
            {
                "id": "rest.patch",
                "name": "REST PATCH Request",
                "description": "Make a PATCH request to a REST API endpoint",
                "category": "api",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL to request",
                        },
                        "body": {
                            "type": ["object", "string"],
                            "description": "Request body",
                        },
                        "headers": {
                            "type": "object",
                            "description": "Additional headers",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["url"],
                },
            },
        ]
    
    def get_config_schema(self) -> dict[str, Any] | None:
        """Get the configuration schema for this integration."""
        return {
            "type": "object",
            "properties": {
                "base_url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Base URL for all requests",
                    "examples": ["https://api.example.com/v1"],
                },
                "headers": {
                    "type": "object",
                    "description": "Default headers for all requests",
                    "additionalProperties": {"type": "string"},
                },
                "timeout": {
                    "type": "number",
                    "description": "Default timeout in seconds",
                    "default": 30.0,
                    "minimum": 0.1,
                },
                "follow_redirects": {
                    "type": "boolean",
                    "description": "Whether to follow redirects",
                    "default": True,
                },
                "health_endpoint": {
                    "type": "string",
                    "description": "Endpoint to use for health checks",
                    "examples": ["/health", "/status"],
                },
                "authentication": {
                    "type": "object",
                    "description": "Authentication configuration",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["none", "bearer", "basic", "api-key", "oauth2"],
                            "default": "none",
                        },
                        "token": {
                            "type": "string",
                            "description": "Bearer token for authentication",
                        },
                        "api_key": {
                            "type": "string",
                            "description": "API key for authentication",
                        },
                        "username": {
                            "type": "string",
                            "description": "Username for basic auth",
                        },
                        "password": {
                            "type": "string",
                            "description": "Password for basic auth",
                        },
                    },
                },
            },
            "required": [],
        }
    
    def validate_config(self, config: dict[str, Any]) -> tuple[bool, str]:
        """Validate configuration for this integration."""
        # Check base_url format if provided
        if "base_url" in config:
            base_url = config["base_url"]
            if not base_url.startswith("http://") and not base_url.startswith("https://"):
                return (False, "base_url must start with http:// or https://")
        
        # Check timeout
        if "timeout" in config:
            timeout = config["timeout"]
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                return (False, "timeout must be a positive number")
        
        # Check authentication
        auth = config.get("authentication", {})
        auth_type = auth.get("type", "none")
        
        if auth_type == "bearer" and not auth.get("token"):
            return (False, "Bearer authentication requires a token")
        
        if auth_type == "basic":
            if not auth.get("username") or not auth.get("password"):
                return (False, "Basic authentication requires username and password")
        
        if auth_type == "api-key" and not auth.get("api_key"):
            return (False, "API key authentication requires an api_key")
        
        return (True, "")
