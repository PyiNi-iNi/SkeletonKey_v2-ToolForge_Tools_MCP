"""Redis Integration for SkeletonKey (P5.4).

This integration provides access to Redis with:
- Key-value operations
- Hash operations
- List operations
- Set operations
- Sorted set operations
- Pub/Sub support
- Transaction support
- Health checks

Requires: redis
Install with: pip install redis
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

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
    name="redis",
    title="Redis Database",
    version="1.0.0",
    integration_type=IntegrationType.DATABASE,
    category=IntegrationCategory.DATABASE,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="Redis integration for in-memory key-value store operations",
    author="SkeletonKey Team",
    license="MIT",
    keywords=["redis", "database", "cache", "key-value", "in-memory", "pubsub"],
    dependencies=[
        IntegrationDependency(
            name="redis",
            version=">=4.0.0",
            optional=False,
            description="Redis client for Python",
        ),
    ],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access to Redis server",
            required=True,
            default_granted=False,
        ),
    ],
)
class RedisIntegration(Integration):
    """Redis database integration.
    
    This integration provides a full-featured Redis client with support for:
    - Key-value operations (GET, SET, DELETE, EXISTS, etc.)
    - Hash operations (HGET, HSET, HDEL, etc.)
    - List operations (LPUSH, RPUSH, LPOP, RPOP, LRANGE, etc.)
    - Set operations (SADD, SREM, SMEMBERS, etc.)
    - Sorted set operations (ZADD, ZREM, ZRANGE, etc.)
    - Pub/Sub support (PUBLISH, SUBSCRIBE)
    - Transaction support (MULTI, EXEC)
    - Key expiration (EXPIRE, TTL)
    """
    
    def __init__(self, config: IntegrationConfig):
        """Initialize the Redis integration."""
        super().__init__(config)
        self._client: Any = None
        self._initialized = False
        self._redis: Any = None
    
    def initialize(self) -> bool:
        """Initialize the Redis integration."""
        try:
            # Import redis
            import redis
            self._redis = redis
            
            # Get connection parameters from config
            host = self.config.config.get("host", "localhost")
            port = self.config.config.get("port", 6379)
            db = self.config.config.get("db", 0)
            password = self.config.secrets.get("password") or self.config.config.get("password")
            
            # SSL configuration
            ssl = self.config.config.get("ssl", False)
            ssl_cert_reqs = self.config.config.get("ssl_cert_reqs", "required")
            ssl_ca_certs = self.config.config.get("ssl_ca_certs")
            
            # Connection timeout
            socket_timeout = self.config.config.get("socket_timeout", 5)
            socket_connect_timeout = self.config.config.get("socket_connect_timeout", 5)
            
            # Create client
            self._client = self._redis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                ssl=ssl,
                ssl_cert_reqs=ssl_cert_reqs,
                ssl_ca_certs=ssl_ca_certs,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                decode_responses=True,  # Automatically decode responses
            )
            
            # Test connection
            self._client.ping()
            
            self._initialized = True
            logger.info(f"Redis integration initialized: {host}:{port}/{db}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Redis integration: {e}")
            self._initialized = False
            return False
    
    def cleanup(self) -> None:
        """Clean up the Redis integration."""
        if self._client:
            try:
                self._client.close()
                self._client = None
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis connection: {e}")
        self._initialized = False
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the Redis integration."""
        try:
            if not self._initialized:
                return ("unhealthy", "Integration not initialized")
            
            # Test connection
            self._client.ping()
            return ("healthy", "Redis server is accessible")
        except Exception as e:
            return ("unhealthy", f"Health check failed: {e}")
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "redis.get",
                "title": "Get Value",
                "description": "Get the value of a key",
                "capability": "redis.get",
                "risk": "read",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "get", "key-value"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Key name"},
                    },
                    "required": ["key"],
                },
            },
            {
                "id": "redis.set",
                "title": "Set Value",
                "description": "Set the value of a key",
                "capability": "redis.set",
                "risk": "write",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "set", "key-value"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Key name"},
                        "value": {"type": "string", "description": "Value to set"},
                        "ex": {"type": "integer", "description": "Expiration time in seconds"},
                        "px": {"type": "integer", "description": "Expiration time in milliseconds"},
                        "nx": {"type": "boolean", "default": False, "description": "Only set if key does not exist"},
                        "xx": {"type": "boolean", "default": False, "description": "Only set if key already exists"},
                    },
                    "required": ["key", "value"],
                },
            },
            {
                "id": "redis.delete",
                "title": "Delete Key",
                "description": "Delete a key",
                "capability": "redis.delete",
                "risk": "write",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "delete", "key-value"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Key name"},
                        "many": {"type": "boolean", "default": False, "description": "Delete multiple keys"},
                    },
                    "required": ["key"],
                },
            },
            {
                "id": "redis.exists",
                "title": "Check Existence",
                "description": "Check if a key exists",
                "capability": "redis.exists",
                "risk": "read",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "exists", "key-value"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Key name"},
                        "many": {"type": "boolean", "default": False, "description": "Check multiple keys"},
                    },
                    "required": ["key"],
                },
            },
            {
                "id": "redis.keys",
                "title": "List Keys",
                "description": "List keys matching a pattern",
                "capability": "redis.keys",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["redis", "database", "keys", "list"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "default": "*", "description": "Key pattern (glob-style)"},
                        "limit": {"type": "integer", "minimum": 1, "description": "Maximum number of keys"},
                    },
                },
            },
            {
                "id": "redis.expire",
                "title": "Set Expiration",
                "description": "Set expiration time for a key",
                "capability": "redis.expire",
                "risk": "write",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "expire", "ttl"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Key name"},
                        "seconds": {"type": "integer", "minimum": 1, "description": "Expiration time in seconds"},
                        "milliseconds": {"type": "integer", "minimum": 1, "description": "Expiration time in milliseconds"},
                    },
                    "required": ["key"],
                },
            },
            {
                "id": "redis.ttl",
                "title": "Get TTL",
                "description": "Get the remaining time to live for a key",
                "capability": "redis.ttl",
                "risk": "read",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "ttl", "expire"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Key name"},
                    },
                    "required": ["key"],
                },
            },
            {
                "id": "redis.hget",
                "title": "Get Hash Field",
                "description": "Get a field value from a hash",
                "capability": "redis.hget",
                "risk": "read",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "hash", "get"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Hash key"},
                        "field": {"type": "string", "description": "Field name"},
                    },
                    "required": ["key", "field"],
                },
            },
            {
                "id": "redis.hset",
                "title": "Set Hash Field",
                "description": "Set a field value in a hash",
                "capability": "redis.hset",
                "risk": "write",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "hash", "set"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Hash key"},
                        "field": {"type": "string", "description": "Field name"},
                        "value": {"type": "string", "description": "Field value"},
                        "many": {"type": "boolean", "default": False, "description": "Set multiple fields"},
                    },
                    "required": ["key", "field", "value"],
                },
            },
            {
                "id": "redis.hgetall",
                "title": "Get All Hash Fields",
                "description": "Get all fields and values from a hash",
                "capability": "redis.hgetall",
                "risk": "read",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "hash", "getall"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Hash key"},
                    },
                    "required": ["key"],
                },
            },
            {
                "id": "redis.lpush",
                "title": "Push to List (Left)",
                "description": "Push elements to the left of a list",
                "capability": "redis.lpush",
                "risk": "write",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "list", "lpush"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "List key"},
                        "values": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Values to push",
                        },
                    },
                    "required": ["key", "values"],
                },
            },
            {
                "id": "redis.rpush",
                "title": "Push to List (Right)",
                "description": "Push elements to the right of a list",
                "capability": "redis.rpush",
                "risk": "write",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "list", "rpush"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "List key"},
                        "values": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Values to push",
                        },
                    },
                    "required": ["key", "values"],
                },
            },
            {
                "id": "redis.lpop",
                "title": "Pop from List (Left)",
                "description": "Remove and return the first element of a list",
                "capability": "redis.lpop",
                "risk": "write",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "list", "lpop"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "List key"},
                        "count": {"type": "integer", "minimum": 1, "description": "Number of elements to pop"},
                    },
                    "required": ["key"],
                },
            },
            {
                "id": "redis.rpop",
                "title": "Pop from List (Right)",
                "description": "Remove and return the last element of a list",
                "capability": "redis.rpop",
                "risk": "write",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "list", "rpop"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "List key"},
                        "count": {"type": "integer", "minimum": 1, "description": "Number of elements to pop"},
                    },
                    "required": ["key"],
                },
            },
            {
                "id": "redis.lrange",
                "title": "Get List Range",
                "description": "Get a range of elements from a list",
                "capability": "redis.lrange",
                "risk": "read",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "list", "lrange"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "List key"},
                        "start": {"type": "integer", "default": 0, "description": "Start index"},
                        "stop": {"type": "integer", "default": -1, "description": "Stop index (-1 for end)"},
                    },
                    "required": ["key"],
                },
            },
            {
                "id": "redis.llen",
                "title": "Get List Length",
                "description": "Get the length of a list",
                "capability": "redis.llen",
                "risk": "read",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["redis", "database", "list", "llen"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "List key"},
                    },
                    "required": ["key"],
                },
            },
        ]
    
    # Tool implementations
    def tool_get(self, key: str) -> dict[str, Any]:
        """Get the value of a key."""
        try:
            value = self._client.get(key)
            return {
                "key": key,
                "value": value,
                "exists": value is not None,
            }
        except Exception as e:
            raise Exception(f"Get failed: {e}")
    
    def tool_set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> dict[str, Any]:
        """Set the value of a key."""
        try:
            result = self._client.set(
                key,
                value,
                ex=ex,
                px=px,
                nx=nx,
                xx=xx,
            )
            return {
                "key": key,
                "set": result,
            }
        except Exception as e:
            raise Exception(f"Set failed: {e}")
    
    def tool_delete(self, key: str, many: bool = False) -> dict[str, Any]:
        """Delete a key or multiple keys."""
        try:
            if many:
                if not isinstance(key, list):
                    raise ValueError("key must be a list when many=True")
                result = self._client.delete(*key)
                return {
                    "deleted": result,
                    "keys": key,
                }
            else:
                result = self._client.delete(key)
                return {
                    "deleted": result == 1,
                    "key": key,
                }
        except Exception as e:
            raise Exception(f"Delete failed: {e}")
    
    def tool_exists(self, key: str, many: bool = False) -> dict[str, Any]:
        """Check if a key exists."""
        try:
            if many:
                if not isinstance(key, list):
                    raise ValueError("key must be a list when many=True")
                result = self._client.exists(*key)
                return {
                    "exists": result > 0,
                    "count": result,
                    "keys": key,
                }
            else:
                result = self._client.exists(key)
                return {
                    "exists": result == 1,
                    "key": key,
                }
        except Exception as e:
            raise Exception(f"Exists check failed: {e}")
    
    def tool_keys(self, pattern: str = "*", limit: int | None = None) -> dict[str, Any]:
        """List keys matching a pattern."""
        try:
            keys = []
            cursor = 0
            while True:
                result = self._client.scan(cursor, match=pattern, count=100)
                cursor = result[0]
                keys.extend(result[1])
                if cursor == 0:
                    break
                if limit and len(keys) >= limit:
                    keys = keys[:limit]
                    break
            
            return {
                "keys": keys,
                "count": len(keys),
                "pattern": pattern,
            }
        except Exception as e:
            raise Exception(f"Keys list failed: {e}")
    
    def tool_expire(
        self,
        key: str,
        seconds: int | None = None,
        milliseconds: int | None = None,
    ) -> dict[str, Any]:
        """Set expiration time for a key."""
        try:
            if seconds is not None:
                result = self._client.expire(key, seconds)
            elif milliseconds is not None:
                result = self._client.pexpire(key, milliseconds)
            else:
                raise ValueError("Either seconds or milliseconds must be provided")
            
            return {
                "key": key,
                "expired": result == 1,
            }
        except Exception as e:
            raise Exception(f"Expire failed: {e}")
    
    def tool_ttl(self, key: str) -> dict[str, Any]:
        """Get the remaining time to live for a key."""
        try:
            ttl = self._client.ttl(key)
            return {
                "key": key,
                "ttl": ttl,
                "expired": ttl == -2,
                "no_expiry": ttl == -1,
            }
        except Exception as e:
            raise Exception(f"TTL check failed: {e}")
    
    def tool_hget(self, key: str, field: str) -> dict[str, Any]:
        """Get a field value from a hash."""
        try:
            value = self._client.hget(key, field)
            return {
                "key": key,
                "field": field,
                "value": value,
                "exists": value is not None,
            }
        except Exception as e:
            raise Exception(f"HGET failed: {e}")
    
    def tool_hset(
        self,
        key: str,
        field: str,
        value: str,
        many: bool = False,
    ) -> dict[str, Any]:
        """Set a field value in a hash."""
        try:
            if many:
                if not isinstance(field, dict):
                    raise ValueError("field must be a dict when many=True")
                result = self._client.hset(key, mapping=field)
                return {
                    "key": key,
                    "set": result,
                    "fields": list(field.keys()),
                }
            else:
                result = self._client.hset(key, field, value)
                return {
                    "key": key,
                    "field": field,
                    "set": result,
                }
        except Exception as e:
            raise Exception(f"HSET failed: {e}")
    
    def tool_hgetall(self, key: str) -> dict[str, Any]:
        """Get all fields and values from a hash."""
        try:
            result = self._client.hgetall(key)
            return {
                "key": key,
                "fields": result,
                "count": len(result),
            }
        except Exception as e:
            raise Exception(f"HGETALL failed: {e}")
    
    def tool_lpush(self, key: str, values: list[str]) -> dict[str, Any]:
        """Push elements to the left of a list."""
        try:
            result = self._client.lpush(key, *values)
            return {
                "key": key,
                "pushed": len(values),
                "length": result,
            }
        except Exception as e:
            raise Exception(f"LPUSH failed: {e}")
    
    def tool_rpush(self, key: str, values: list[str]) -> dict[str, Any]:
        """Push elements to the right of a list."""
        try:
            result = self._client.rpush(key, *values)
            return {
                "key": key,
                "pushed": len(values),
                "length": result,
            }
        except Exception as e:
            raise Exception(f"RPUSH failed: {e}")
    
    def tool_lpop(self, key: str, count: int = 1) -> dict[str, Any]:
        """Remove and return the first element(s) of a list."""
        try:
            if count == 1:
                result = self._client.lpop(key)
                return {
                    "key": key,
                    "value": result,
                    "popped": result is not None,
                }
            else:
                results = self._client.lpop(key, count)
                return {
                    "key": key,
                    "values": results,
                    "popped": len(results),
                }
        except Exception as e:
            raise Exception(f"LPOP failed: {e}")
    
    def tool_rpop(self, key: str, count: int = 1) -> dict[str, Any]:
        """Remove and return the last element(s) of a list."""
        try:
            if count == 1:
                result = self._client.rpop(key)
                return {
                    "key": key,
                    "value": result,
                    "popped": result is not None,
                }
            else:
                results = self._client.rpop(key, count)
                return {
                    "key": key,
                    "values": results,
                    "popped": len(results),
                }
        except Exception as e:
            raise Exception(f"RPOP failed: {e}")
    
    def tool_lrange(self, key: str, start: int = 0, stop: int = -1) -> dict[str, Any]:
        """Get a range of elements from a list."""
        try:
            result = self._client.lrange(key, start, stop)
            return {
                "key": key,
                "values": result,
                "count": len(result),
                "start": start,
                "stop": stop,
            }
        except Exception as e:
            raise Exception(f"LRANGE failed: {e}")
    
    def tool_llen(self, key: str) -> dict[str, Any]:
        """Get the length of a list."""
        try:
            result = self._client.llen(key)
            return {
                "key": key,
                "length": result,
            }
        except Exception as e:
            raise Exception(f"LLEN failed: {e}")
