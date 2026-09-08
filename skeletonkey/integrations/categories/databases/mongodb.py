"""MongoDB Integration for SkeletonKey (P5.4).

This integration provides access to MongoDB databases with:
- Connection management
- Document CRUD operations
- Collection management
- Query building
- Aggregation pipelines
- Index management
- Health checks

Requires: pymongo
Install with: pip install pymongo
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
    name="mongodb",
    title="MongoDB Database",
    version="1.0.0",
    integration_type=IntegrationType.DATABASE,
    category=IntegrationCategory.DATABASE,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="MongoDB database integration for document-oriented database operations",
    author="SkeletonKey Team",
    license="MIT",
    keywords=["mongodb", "database", "nosql", "document", "json", "pymongo"],
    dependencies=[
        IntegrationDependency(
            name="pymongo",
            version=">=4.0.0",
            optional=False,
            description="MongoDB driver for Python",
        ),
    ],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access to MongoDB server",
            required=True,
            default_granted=False,
        ),
    ],
)
class MongoDBIntegration(Integration):
    """MongoDB database integration.
    
    This integration provides a full-featured MongoDB database client with
    support for:
    - Connection management
    - Document CRUD operations (create, read, update, delete)
    - Collection management
    - Query building with filters
    - Aggregation pipelines
    - Index management
    - Bulk operations
    - GridFS support
    """
    
    def __init__(self, config: IntegrationConfig):
        """Initialize the MongoDB integration."""
        super().__init__(config)
        self._client: Any = None
        self._database: Any = None
        self._initialized = False
        self._pymongo: Any = None
    
    def initialize(self) -> bool:
        """Initialize the MongoDB integration."""
        try:
            # Import pymongo
            import pymongo
            self._pymongo = pymongo
            
            # Get connection parameters from config
            uri = self.config.secrets.get("uri") or self.config.config.get("uri")
            if not uri:
                # Build URI from individual parameters
                host = self.config.config.get("host", "localhost")
                port = self.config.config.get("port", 27017)
                username = self.config.config.get("username")
                password = self.config.secrets.get("password") or self.config.config.get("password")
                auth_source = self.config.config.get("auth_source", "admin")
                
                if username and password:
                    uri = f"mongodb://{username}:{password}@{host}:{port}/?authSource={auth_source}"
                else:
                    uri = f"mongodb://{host}:{port}/"
            
            database_name = self.config.config.get("database", "test")
            
            # Connection options
            connect_timeout_ms = self.config.config.get("connect_timeout_ms", 10000)
            socket_timeout_ms = self.config.config.get("socket_timeout_ms", 30000)
            server_selection_timeout_ms = self.config.config.get("server_selection_timeout_ms", 30000)
            
            # Create client
            self._client = self._pymongo.MongoClient(
                uri,
                connectTimeoutMS=connect_timeout_ms,
                socketTimeoutMS=socket_timeout_ms,
                serverSelectionTimeoutMS=server_selection_timeout_ms,
            )
            
            # Get database
            self._database = self._client[database_name]
            
            self._initialized = True
            logger.info(f"MongoDB integration initialized: {uri}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize MongoDB integration: {e}")
            self._initialized = False
            return False
    
    def cleanup(self) -> None:
        """Clean up the MongoDB integration."""
        if self._client:
            try:
                self._client.close()
                self._client = None
                self._database = None
                logger.info("MongoDB connection closed")
            except Exception as e:
                logger.error(f"Error closing MongoDB connection: {e}")
        self._initialized = False
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the MongoDB integration."""
        try:
            if not self._initialized:
                return ("unhealthy", "Integration not initialized")
            
            # Test connection by listing databases
            self._client.admin.command("ping")
            return ("healthy", "MongoDB database is accessible")
        except Exception as e:
            return ("unhealthy", f"Health check failed: {e}")
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "mongodb.insert",
                "title": "Insert Document",
                "description": "Insert a document into a MongoDB collection",
                "capability": "mongodb.insert",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["mongodb", "database", "insert", "document"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "document": {
                            "type": "object",
                            "description": "Document to insert",
                        },
                        "many": {"type": "boolean", "default": False, "description": "Insert multiple documents"},
                    },
                    "required": ["collection", "document"],
                },
            },
            {
                "id": "mongodb.find",
                "title": "Find Documents",
                "description": "Find documents in a MongoDB collection",
                "capability": "mongodb.find",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["mongodb", "database", "find", "query"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "filter": {
                            "type": "object",
                            "description": "Query filter",
                        },
                        "projection": {
                            "type": "object",
                            "description": "Projection (fields to return)",
                        },
                        "sort": {
                            "type": ["object", "array"],
                            "description": "Sort specification",
                        },
                        "limit": {"type": "integer", "minimum": 1, "description": "Maximum number of documents"},
                        "skip": {"type": "integer", "minimum": 0, "description": "Number of documents to skip"},
                    },
                    "required": ["collection"],
                },
            },
            {
                "id": "mongodb.find_one",
                "title": "Find One Document",
                "description": "Find a single document in a MongoDB collection",
                "capability": "mongodb.find_one",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["mongodb", "database", "find", "query", "single"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "filter": {
                            "type": "object",
                            "description": "Query filter",
                        },
                        "projection": {
                            "type": "object",
                            "description": "Projection (fields to return)",
                        },
                    },
                    "required": ["collection"],
                },
            },
            {
                "id": "mongodb.update",
                "title": "Update Documents",
                "description": "Update documents in a MongoDB collection",
                "capability": "mongodb.update",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["mongodb", "database", "update", "modify"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "filter": {
                            "type": "object",
                            "description": "Query filter",
                        },
                        "update": {
                            "type": "object",
                            "description": "Update operations ($set, $unset, etc.)",
                        },
                        "upsert": {"type": "boolean", "default": False, "description": "Create document if not found"},
                        "many": {"type": "boolean", "default": False, "description": "Update multiple documents"},
                    },
                    "required": ["collection", "update"],
                },
            },
            {
                "id": "mongodb.delete",
                "title": "Delete Documents",
                "description": "Delete documents from a MongoDB collection",
                "capability": "mongodb.delete",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["mongodb", "database", "delete", "remove"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "filter": {
                            "type": "object",
                            "description": "Query filter",
                        },
                        "many": {"type": "boolean", "default": False, "description": "Delete multiple documents"},
                    },
                    "required": ["collection"],
                },
            },
            {
                "id": "mongodb.count",
                "title": "Count Documents",
                "description": "Count documents in a MongoDB collection",
                "capability": "mongodb.count",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["mongodb", "database", "count", "query"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "filter": {
                            "type": "object",
                            "description": "Query filter",
                        },
                    },
                    "required": ["collection"],
                },
            },
            {
                "id": "mongodb.aggregate",
                "title": "Aggregate",
                "description": "Run an aggregation pipeline on a MongoDB collection",
                "capability": "mongodb.aggregate",
                "risk": "read",
                "typical_latency_ms": 20,
                "tier": "task",
                "tags": ["mongodb", "database", "aggregate", "pipeline"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "pipeline": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Aggregation pipeline stages",
                        },
                        "limit": {"type": "integer", "minimum": 1, "description": "Maximum number of results"},
                    },
                    "required": ["collection", "pipeline"],
                },
            },
            {
                "id": "mongodb.collections",
                "title": "List Collections",
                "description": "List all collections in the MongoDB database",
                "capability": "mongodb.collections",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["mongodb", "database", "collections", "list"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "include_system": {"type": "boolean", "default": False, "description": "Include system collections"},
                    },
                },
            },
            {
                "id": "mongodb.create_index",
                "title": "Create Index",
                "description": "Create an index on a MongoDB collection",
                "capability": "mongodb.create_index",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["mongodb", "database", "index", "create"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "keys": {
                            "type": ["object", "array"],
                            "description": "Index keys (field: direction pairs or field list)",
                        },
                        "name": {"type": "string", "description": "Index name (optional)"},
                        "unique": {"type": "boolean", "default": False, "description": "Create unique index"},
                        "sparse": {"type": "boolean", "default": False, "description": "Create sparse index"},
                    },
                    "required": ["collection", "keys"],
                },
            },
            {
                "id": "mongodb.drop",
                "title": "Drop Collection",
                "description": "Drop a MongoDB collection",
                "capability": "mongodb.drop",
                "risk": "destructive",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["mongodb", "database", "drop", "delete"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                    },
                    "required": ["collection"],
                },
            },
        ]
    
    # Tool implementations
    def tool_insert(
        self,
        collection: str,
        document: dict[str, Any],
        many: bool = False,
    ) -> dict[str, Any]:
        """Insert a document or documents into a collection."""
        try:
            col = self._database[collection]
            
            if many:
                if not isinstance(document, list):
                    raise ValueError("document must be a list when many=True")
                result = col.insert_many(document)
                return {
                    "inserted_ids": list(result.inserted_ids),
                    "count": len(result.inserted_ids),
                }
            else:
                result = col.insert_one(document)
                return {
                    "inserted_id": str(result.inserted_id),
                    "count": 1,
                }
        except Exception as e:
            raise Exception(f"Insert failed: {e}")
    
    def tool_find(
        self,
        collection: str,
        filter: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
        sort: list | dict | None = None,
        limit: int | None = None,
        skip: int | None = None,
    ) -> dict[str, Any]:
        """Find documents in a collection."""
        try:
            col = self._database[collection]
            
            cursor = col.find(filter or {}, projection or {})
            
            if sort:
                cursor = cursor.sort(sort)
            if limit is not None:
                cursor = cursor.limit(limit)
            if skip is not None:
                cursor = cursor.skip(skip)
            
            results = list(cursor)
            
            # Convert ObjectId to string for JSON serialization
            for doc in results:
                if "_id" in doc:
                    from bson import ObjectId
                    if isinstance(doc["_id"], ObjectId):
                        doc["_id"] = str(doc["_id"])
            
            return {
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            raise Exception(f"Find failed: {e}")
    
    def tool_find_one(
        self,
        collection: str,
        filter: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Find a single document in a collection."""
        try:
            col = self._database[collection]
            
            result = col.find_one(filter or {}, projection or {})
            
            if result and "_id" in result:
                from bson import ObjectId
                if isinstance(result["_id"], ObjectId):
                    result["_id"] = str(result["_id"])
            
            return {
                "result": result,
                "found": result is not None,
            }
        except Exception as e:
            raise Exception(f"Find one failed: {e}")
    
    def tool_update(
        self,
        collection: str,
        update: dict[str, Any],
        filter: dict[str, Any] | None = None,
        upsert: bool = False,
        many: bool = False,
    ) -> dict[str, Any]:
        """Update documents in a collection."""
        try:
            col = self._database[collection]
            
            if many:
                result = col.update_many(filter or {}, update, upsert=upsert)
                return {
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                    "upserted_id": str(result.upserted_id) if result.upserted_id else None,
                }
            else:
                result = col.update_one(filter or {}, update, upsert=upsert)
                return {
                    "matched_count": result.matched_count,
                    "modified_count": result.modified_count,
                    "upserted_id": str(result.upserted_id) if result.upserted_id else None,
                }
        except Exception as e:
            raise Exception(f"Update failed: {e}")
    
    def tool_delete(
        self,
        collection: str,
        filter: dict[str, Any] | None = None,
        many: bool = False,
    ) -> dict[str, Any]:
        """Delete documents from a collection."""
        try:
            col = self._database[collection]
            
            if many:
                result = col.delete_many(filter or {})
                return {
                    "deleted_count": result.deleted_count,
                }
            else:
                result = col.delete_one(filter or {})
                return {
                    "deleted_count": result.deleted_count,
                }
        except Exception as e:
            raise Exception(f"Delete failed: {e}")
    
    def tool_count(
        self,
        collection: str,
        filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Count documents in a collection."""
        try:
            col = self._database[collection]
            count = col.count_documents(filter or {})
            return {
                "count": count,
            }
        except Exception as e:
            raise Exception(f"Count failed: {e}")
    
    def tool_aggregate(
        self,
        collection: str,
        pipeline: list[dict[str, Any]],
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Run an aggregation pipeline on a collection."""
        try:
            col = self._database[collection]
            
            cursor = col.aggregate(pipeline)
            
            if limit is not None:
                cursor = cursor.limit(limit)
            
            results = list(cursor)
            
            # Convert ObjectId to string for JSON serialization
            for doc in results:
                if "_id" in doc:
                    from bson import ObjectId
                    if isinstance(doc["_id"], ObjectId):
                        doc["_id"] = str(doc["_id"])
            
            return {
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            raise Exception(f"Aggregate failed: {e}")
    
    def tool_collections(
        self,
        include_system: bool = False,
    ) -> dict[str, Any]:
        """List all collections in the database."""
        try:
            names = self._database.list_collection_names()
            
            if not include_system:
                # Filter out system collections
                names = [n for n in names if not n.startswith("system.")]
            
            return {
                "collections": names,
                "count": len(names),
            }
        except Exception as e:
            raise Exception(f"Failed to list collections: {e}")
    
    def tool_create_index(
        self,
        collection: str,
        keys: list | dict,
        name: str | None = None,
        unique: bool = False,
        sparse: bool = False,
    ) -> dict[str, Any]:
        """Create an index on a collection."""
        try:
            col = self._database[collection]
            
            result = col.create_index(keys, name=name, unique=unique, sparse=sparse)
            
            return {
                "index_name": result,
                "collection": collection,
                "created": True,
            }
        except Exception as e:
            raise Exception(f"Failed to create index: {e}")
    
    def tool_drop(
        self,
        collection: str,
    ) -> dict[str, Any]:
        """Drop a collection."""
        try:
            col = self._database[collection]
            col.drop()
            
            return {
                "collection": collection,
                "dropped": True,
            }
        except Exception as e:
            raise Exception(f"Failed to drop collection: {e}")
