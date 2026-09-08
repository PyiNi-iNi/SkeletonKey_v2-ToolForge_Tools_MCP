"""Requests HTTP Client Integration for SkeletonKey (P5.4).

This integration provides HTTP client functionality using the requests library:
- GET, POST, PUT, DELETE, PATCH, HEAD methods
- Request/response handling
- Authentication support (Basic, Bearer, Digest)
- Proxy support
- SSL/TLS configuration
- Timeout configuration
- Session management
- Cookie handling
- Health checks

Requires: requests
Install with: pip install requests
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from skeletonkey.integrations import (
    Integration,
    IntegrationCapability,
    IntegrationCategory,
    IntegrationConfig,
    IntegrationDependency,
    IntegrationHealthCheck,
    IntegrationInfo,
    IntegrationPlatform,
    IntegrationSandbox,
    IntegrationType,
    register_integration,
)

logger = logging.getLogger(__name__)


@register_integration(
    name="requests",
    title="Requests HTTP Client",
    version="1.0.0",
    integration_type=IntegrationType.CLIENT_LIBRARY,
    category=IntegrationCategory.NETWORKING,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="HTTP client integration using the requests library",
    author="SkeletonKey Team",
    license="MIT",
    keywords=["http", "requests", "client", "api", "rest", "web"],
    dependencies=[
        IntegrationDependency(
            name="requests",
            version=">=2.28.0",
            optional=False,
            description="HTTP for Humans",
        ),
    ],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access for HTTP requests",
            required=True,
            default_granted=False,
        ),
    ],
)
class RequestsIntegration(Integration):
    """Requests HTTP client integration.
    
    This integration provides a full-featured HTTP client with support for:
    - All HTTP methods (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)
    - Request/response handling
    - Authentication (Basic, Bearer, Digest, OAuth)
    - Proxy support
    - SSL/TLS verification
    - Timeout configuration
    - Session management
    - Cookie handling
    - Redirect handling
    - Streaming responses
    """
    
    def __init__(self, config: IntegrationConfig):
        """Initialize the requests integration."""
        super().__init__(config)
        self._session: Any = None
        self._initialized = False
        self._requests: Any = None
    
    def initialize(self) -> bool:
        """Initialize the requests integration."""
        try:
            # Import requests
            import requests
            self._requests = requests
            
            # Create session
            self._session = requests.Session()
            
            # Configure session from config
            timeout = self.config.config.get("timeout", 30)
            self._session.timeout = timeout
            
            # Configure headers
            default_headers = self.config.config.get("default_headers", {})
            self._session.headers.update(default_headers)
            
            # Configure auth
            auth = self._get_auth()
            if auth:
                self._session.auth = auth
            
            # Configure proxies
            proxies = self.config.config.get("proxies", {})
            if proxies:
                self._session.proxies.update(proxies)
            
            # Configure SSL verification
            verify = self.config.config.get("verify_ssl", True)
            self._session.verify = verify
            
            self._initialized = True
            logger.info("Requests integration initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize requests integration: {e}")
            self._initialized = False
            return False
    
    def _get_auth(self) -> Any:
        """Get authentication handler from config."""
        auth_type = self.config.config.get("auth_type")
        
        if not auth_type:
            return None
        
        if auth_type == "basic":
            username = self.config.secrets.get("username") or self.config.config.get("username")
            password = self.config.secrets.get("password") or self.config.config.get("password")
            if username and password:
                from requests.auth import HTTPBasicAuth
                return HTTPBasicAuth(username, password)
        
        elif auth_type == "bearer":
            token = self.config.secrets.get("token") or self.config.config.get("token")
            if token:
                from requests.auth import HTTPBearerAuth
                return HTTPBearerAuth(token)
        
        elif auth_type == "digest":
            username = self.config.secrets.get("username") or self.config.config.get("username")
            password = self.config.secrets.get("password") or self.config.config.get("password")
            if username and password:
                from requests.auth import HTTPDigestAuth
                return HTTPDigestAuth(username, password)
        
        return None
    
    def cleanup(self) -> None:
        """Clean up the requests integration."""
        if self._session:
            try:
                self._session.close()
                self._session = None
                logger.info("Requests session closed")
            except Exception as e:
                logger.error(f"Error closing requests session: {e}")
        self._initialized = False
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the requests integration."""
        try:
            if not self._initialized:
                return ("unhealthy", "Integration not initialized")
            
            # Test with a simple request to a known endpoint
            test_url = self.config.config.get("health_check_url", "https://httpbin.org/get")
            response = self._session.get(test_url, timeout=5)
            
            if response.status_code == 200:
                return ("healthy", "Requests client is working")
            else:
                return ("degraded", f"Health check returned {response.status_code}")
        except Exception as e:
            return ("degraded", f"Health check failed (may be offline): {e}")
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "requests.get",
                "title": "HTTP GET",
                "description": "Send an HTTP GET request",
                "capability": "requests.get",
                "risk": "read",
                "typical_latency_ms": 100,
                "tier": "task",
                "tags": ["http", "requests", "get", "api"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to send request to", "format": "uri"},
                        "params": {
                            "type": "object",
                            "description": "Query parameters",
                            "additionalProperties": {"type": "string"},
                        },
                        "headers": {
                            "type": "object",
                            "description": "Request headers",
                            "additionalProperties": {"type": "string"},
                        },
                        "timeout": {"type": "number", "minimum": 0, "description": "Request timeout in seconds"},
                        "allow_redirects": {"type": "boolean", "default": True, "description": "Follow redirects"},
                        "stream": {"type": "boolean", "default": False, "description": "Stream response"},
                    },
                    "required": ["url"],
                },
            },
            {
                "id": "requests.post",
                "title": "HTTP POST",
                "description": "Send an HTTP POST request",
                "capability": "requests.post",
                "risk": "write",
                "typical_latency_ms": 100,
                "tier": "task",
                "tags": ["http", "requests", "post", "api"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to send request to", "format": "uri"},
                        "data": {
                            "type": "object",
                            "description": "Form data to send",
                        },
                        "json": {
                            "type": "object",
                            "description": "JSON data to send",
                        },
                        "params": {
                            "type": "object",
                            "description": "Query parameters",
                            "additionalProperties": {"type": "string"},
                        },
                        "headers": {
                            "type": "object",
                            "description": "Request headers",
                            "additionalProperties": {"type": "string"},
                        },
                        "timeout": {"type": "number", "minimum": 0, "description": "Request timeout in seconds"},
                        "allow_redirects": {"type": "boolean", "default": True, "description": "Follow redirects"},
                        "stream": {"type": "boolean", "default": False, "description": "Stream response"},
                    },
                    "required": ["url"],
                },
            },
            {
                "id": "requests.put",
                "title": "HTTP PUT",
                "description": "Send an HTTP PUT request",
                "capability": "requests.put",
                "risk": "write",
                "typical_latency_ms": 100,
                "tier": "task",
                "tags": ["http", "requests", "put", "api"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to send request to", "format": "uri"},
                        "data": {
                            "type": "object",
                            "description": "Data to send",
                        },
                        "json": {
                            "type": "object",
                            "description": "JSON data to send",
                        },
                        "params": {
                            "type": "object",
                            "description": "Query parameters",
                            "additionalProperties": {"type": "string"},
                        },
                        "headers": {
                            "type": "object",
                            "description": "Request headers",
                            "additionalProperties": {"type": "string"},
                        },
                        "timeout": {"type": "number", "minimum": 0, "description": "Request timeout in seconds"},
                        "allow_redirects": {"type": "boolean", "default": True, "description": "Follow redirects"},
                    },
                    "required": ["url"],
                },
            },
            {
                "id": "requests.delete",
                "title": "HTTP DELETE",
                "description": "Send an HTTP DELETE request",
                "capability": "requests.delete",
                "risk": "write",
                "typical_latency_ms": 100,
                "tier": "task",
                "tags": ["http", "requests", "delete", "api"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to send request to", "format": "uri"},
                        "params": {
                            "type": "object",
                            "description": "Query parameters",
                            "additionalProperties": {"type": "string"},
                        },
                        "headers": {
                            "type": "object",
                            "description": "Request headers",
                            "additionalProperties": {"type": "string"},
                        },
                        "timeout": {"type": "number", "minimum": 0, "description": "Request timeout in seconds"},
                        "allow_redirects": {"type": "boolean", "default": True, "description": "Follow redirects"},
                    },
                    "required": ["url"],
                },
            },
            {
                "id": "requests.patch",
                "title": "HTTP PATCH",
                "description": "Send an HTTP PATCH request",
                "capability": "requests.patch",
                "risk": "write",
                "typical_latency_ms": 100,
                "tier": "task",
                "tags": ["http", "requests", "patch", "api"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to send request to", "format": "uri"},
                        "data": {
                            "type": "object",
                            "description": "Data to send",
                        },
                        "json": {
                            "type": "object",
                            "description": "JSON data to send",
                        },
                        "params": {
                            "type": "object",
                            "description": "Query parameters",
                            "additionalProperties": {"type": "string"},
                        },
                        "headers": {
                            "type": "object",
                            "description": "Request headers",
                            "additionalProperties": {"type": "string"},
                        },
                        "timeout": {"type": "number", "minimum": 0, "description": "Request timeout in seconds"},
                        "allow_redirects": {"type": "boolean", "default": True, "description": "Follow redirects"},
                    },
                    "required": ["url"],
                },
            },
            {
                "id": "requests.head",
                "title": "HTTP HEAD",
                "description": "Send an HTTP HEAD request",
                "capability": "requests.head",
                "risk": "read",
                "typical_latency_ms": 50,
                "tier": "task",
                "tags": ["http", "requests", "head", "api"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to send request to", "format": "uri"},
                        "params": {
                            "type": "object",
                            "description": "Query parameters",
                            "additionalProperties": {"type": "string"},
                        },
                        "headers": {
                            "type": "object",
                            "description": "Request headers",
                            "additionalProperties": {"type": "string"},
                        },
                        "timeout": {"type": "number", "minimum": 0, "description": "Request timeout in seconds"},
                        "allow_redirects": {"type": "boolean", "default": True, "description": "Follow redirects"},
                    },
                    "required": ["url"],
                },
            },
        ]
    
    # Tool implementations
    def tool_get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Send an HTTP GET request."""
        try:
            response = self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else self._session.timeout,
                allow_redirects=allow_redirects,
                stream=stream,
            )
            
            return self._format_response(response, stream=stream)
        except Exception as e:
            raise Exception(f"GET request failed: {e}")
    
    def tool_post(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Send an HTTP POST request."""
        try:
            response = self._session.post(
                url,
                data=data,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else self._session.timeout,
                allow_redirects=allow_redirects,
                stream=stream,
            )
            
            return self._format_response(response, stream=stream)
        except Exception as e:
            raise Exception(f"POST request failed: {e}")
    
    def tool_put(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
    ) -> dict[str, Any]:
        """Send an HTTP PUT request."""
        try:
            response = self._session.put(
                url,
                data=data,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else self._session.timeout,
                allow_redirects=allow_redirects,
            )
            
            return self._format_response(response)
        except Exception as e:
            raise Exception(f"PUT request failed: {e}")
    
    def tool_delete(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
    ) -> dict[str, Any]:
        """Send an HTTP DELETE request."""
        try:
            response = self._session.delete(
                url,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else self._session.timeout,
                allow_redirects=allow_redirects,
            )
            
            return self._format_response(response)
        except Exception as e:
            raise Exception(f"DELETE request failed: {e}")
    
    def tool_patch(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
    ) -> dict[str, Any]:
        """Send an HTTP PATCH request."""
        try:
            response = self._session.patch(
                url,
                data=data,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else self._session.timeout,
                allow_redirects=allow_redirects,
            )
            
            return self._format_response(response)
        except Exception as e:
            raise Exception(f"PATCH request failed: {e}")
    
    def tool_head(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        allow_redirects: bool = True,
    ) -> dict[str, Any]:
        """Send an HTTP HEAD request."""
        try:
            response = self._session.head(
                url,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else self._session.timeout,
                allow_redirects=allow_redirects,
            )
            
            return self._format_response(response)
        except Exception as e:
            raise Exception(f"HEAD request failed: {e}")
    
    def _format_response(self, response: Any, stream: bool = False) -> dict[str, Any]:
        """Format a response for output."""
        result = {
            "status_code": response.status_code,
            "reason": response.reason,
            "url": response.url,
            "headers": dict(response.headers) if hasattr(response, 'headers') else {},
        }
        
        # Try to get response size
        if hasattr(response, 'headers') and 'content-length' in response.headers:
            result["content_length"] = int(response.headers['content-length'])
        
        # Get cookies
        if hasattr(response, 'cookies'):
            result["cookies"] = dict(response.cookies)
        
        # Get content
        if not stream:
            try:
                # Try JSON first
                try:
                    result["json"] = response.json()
                except (ValueError, TypeError):
                    # Try text
                    result["text"] = response.text
            except Exception:
                pass
        
        return result
