"""SQLite Integration for SkeletonKey (P5.4).

This integration provides access to SQLite databases with:
- Connection management
- Query execution (SELECT, INSERT, UPDATE, DELETE)
- Transaction support
- Table management
- Result formatting
- Health checks

SQLite is built into Python, so this integration has no external dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
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
    name="sqlite",
    title="SQLite Database",
    version="1.0.0",
    integration_type=IntegrationType.DATABASE,
    category=IntegrationCategory.DATABASE,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="SQLite database integration for embedded database operations",
    author="SkeletonKey Team",
    license="MIT",
    keywords=["sqlite", "database", "embedded", "sql", "storage"],
    dependencies=[],
    capabilities=[
        IntegrationCapability(
            name="filesystem",
            description="Requires filesystem access for database files",
            required=True,
            default_granted=True,
        ),
    ],
)
class SQLiteIntegration(Integration):
    """SQLite database integration.
    
    This integration provides a full-featured SQLite database client with
    support for:
    - Connection management (in-memory or file-based)
    - DDL operations (CREATE, ALTER, DROP)
    - DML operations (SELECT, INSERT, UPDATE, DELETE)
    - Transactions
    - Batch operations
    - Result formatting (dict, list, json)
    """
    
    def __init__(self, config: IntegrationConfig):
        """Initialize the SQLite integration."""
        super().__init__(config)
        self._connection: sqlite3.Connection | None = None
        self._database_path: str | None = None
        self._in_memory: bool = False
        self._initialized = False
    
    def initialize(self) -> bool:
        """Initialize the SQLite integration."""
        try:
            # Get database path from config
            db_path = self.config.config.get("database_path", ":memory:")
            
            if db_path == ":memory:":
                self._in_memory = True
                self._database_path = db_path
            else:
                self._in_memory = False
                self._database_path = str(Path(db_path).expanduser().absolute())
                
                # Ensure directory exists
                db_dir = Path(self._database_path).parent
                if db_dir.exists() and not db_dir.is_dir():
                    raise ValueError(f"Database path parent is not a directory: {db_dir}")
                if not db_dir.exists():
                    db_dir.mkdir(parents=True, exist_ok=True)
            
            # Connect to database
            self._connect()
            
            # Enable foreign keys
            self._execute("PRAGMA foreign_keys = ON")
            
            # Set journal mode
            journal_mode = self.config.config.get("journal_mode", "WAL")
            self._execute(f"PRAGMA journal_mode = {journal_mode}")
            
            # Set synchronous mode
            synchronous = self.config.config.get("synchronous", "NORMAL")
            self._execute(f"PRAGMA synchronous = {synchronous}")
            
            self._initialized = True
            logger.info(f"SQLite integration initialized: {self._database_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize SQLite integration: {e}")
            self._initialized = False
            return False
    
    def cleanup(self) -> None:
        """Clean up the SQLite integration."""
        if self._connection:
            try:
                self._connection.close()
                self._connection = None
                logger.info("SQLite connection closed")
            except Exception as e:
                logger.error(f"Error closing SQLite connection: {e}")
        self._initialized = False
    
    def _connect(self) -> None:
        """Establish database connection."""
        if self._connection:
            return
        
        if self._in_memory:
            self._connection = sqlite3.connect(":memory:")
        else:
            self._connection = sqlite3.connect(self._database_path)
        
        # Set row factory to return dicts
        self._connection.row_factory = sqlite3.Row
    
    def _execute(
        self,
        sql: str,
        params: tuple | list | dict | None = None,
        commit: bool = False,
    ) -> sqlite3.Cursor:
        """Execute a SQL statement."""
        if not self._connection:
            self._connect()
        
        cursor = self._connection.cursor()
        
        try:
            if params:
                if isinstance(params, dict):
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            
            if commit:
                self._connection.commit()
            
            return cursor
        except Exception as e:
            self._connection.rollback()
            raise e
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the SQLite integration."""
        try:
            if not self._initialized:
                return ("unhealthy", "Integration not initialized")
            
            # Test connection
            cursor = self._execute("SELECT 1")
            result = cursor.fetchone()
            
            if result and result[0] == 1:
                return ("healthy", "SQLite database is accessible")
            else:
                return ("unhealthy", "SQLite query test failed")
        except Exception as e:
            return ("unhealthy", f"Health check failed: {e}")
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "sqlite.execute",
                "title": "Execute SQL",
                "description": "Execute a SQL statement (SELECT, INSERT, UPDATE, DELETE, etc.)",
                "capability": "sqlite.execute",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["sqlite", "database", "query", "sql"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SQL statement to execute"},
                        "params": {
                            "type": ["array", "object", "null"],
                            "description": "Parameters for the SQL statement",
                        },
                        "commit": {"type": "boolean", "default": False, "description": "Commit the transaction"},
                        "fetch": {"type": "boolean", "default": True, "description": "Fetch results"},
                    },
                    "required": ["sql"],
                },
            },
            {
                "id": "sqlite.query",
                "title": "Query Database",
                "description": "Execute a SELECT query and return results",
                "capability": "sqlite.query",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["sqlite", "database", "query", "select"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SELECT SQL statement"},
                        "params": {
                            "type": ["array", "object", "null"],
                            "description": "Parameters for the SQL statement",
                        },
                        "format": {
                            "type": "string",
                            "enum": ["dict", "list", "json", "raw"],
                            "default": "dict",
                            "description": "Result format",
                        },
                        "limit": {"type": "integer", "minimum": 1, "description": "Maximum number of rows"},
                    },
                    "required": ["sql"],
                },
            },
            {
                "id": "sqlite.create_table",
                "title": "Create Table",
                "description": "Create a new table in the database",
                "capability": "sqlite.create_table",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["sqlite", "database", "ddl", "create"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                        "schema": {
                            "type": "object",
                            "description": "Table schema with column definitions",
                            "additionalProperties": {
                                "type": "string",
                                "description": "Column type (e.g., 'INTEGER', 'TEXT', 'REAL')",
                            },
                        },
                        "if_not_exists": {"type": "boolean", "default": True, "description": "Only create if table doesn't exist"},
                    },
                    "required": ["table", "schema"],
                },
            },
            {
                "id": "sqlite.insert",
                "title": "Insert Data",
                "description": "Insert data into a table",
                "capability": "sqlite.insert",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["sqlite", "database", "insert", "dml"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                        "data": {
                            "type": "object",
                            "description": "Data to insert (column: value pairs)",
                        },
                        "many": {"type": "boolean", "default": False, "description": "Insert multiple rows"},
                        "return_id": {"type": "boolean", "default": False, "description": "Return the inserted row ID"},
                    },
                    "required": ["table", "data"],
                },
            },
            {
                "id": "sqlite.select",
                "title": "Select Data",
                "description": "Select data from a table",
                "capability": "sqlite.select",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["sqlite", "database", "select", "query"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                        "columns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Columns to select (default: all)",
                        },
                        "where": {
                            "type": "object",
                            "description": "WHERE conditions (column: value pairs)",
                        },
                        "order_by": {"type": "string", "description": "ORDER BY clause"},
                        "limit": {"type": "integer", "minimum": 1, "description": "Maximum number of rows"},
                        "offset": {"type": "integer", "minimum": 0, "description": "Number of rows to skip"},
                        "format": {
                            "type": "string",
                            "enum": ["dict", "list", "json", "raw"],
                            "default": "dict",
                            "description": "Result format",
                        },
                    },
                    "required": ["table"],
                },
            },
            {
                "id": "sqlite.update",
                "title": "Update Data",
                "description": "Update data in a table",
                "capability": "sqlite.update",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["sqlite", "database", "update", "dml"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                        "data": {
                            "type": "object",
                            "description": "Data to update (column: value pairs)",
                        },
                        "where": {
                            "type": "object",
                            "description": "WHERE conditions (column: value pairs)",
                        },
                        "row_count": {"type": "boolean", "default": False, "description": "Return the number of rows updated"},
                    },
                    "required": ["table", "data", "where"],
                },
            },
            {
                "id": "sqlite.delete",
                "title": "Delete Data",
                "description": "Delete data from a table",
                "capability": "sqlite.delete",
                "risk": "write",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["sqlite", "database", "delete", "dml"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                        "where": {
                            "type": "object",
                            "description": "WHERE conditions (column: value pairs)",
                        },
                        "row_count": {"type": "boolean", "default": False, "description": "Return the number of rows deleted"},
                    },
                    "required": ["table", "where"],
                },
            },
            {
                "id": "sqlite.transaction",
                "title": "Execute Transaction",
                "description": "Execute multiple SQL statements in a transaction",
                "capability": "sqlite.transaction",
                "risk": "write",
                "typical_latency_ms": 20,
                "tier": "task",
                "tags": ["sqlite", "database", "transaction", "batch"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "statements": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sql": {"type": "string"},
                                    "params": {"type": ["array", "object", "null"]},
                                },
                                "required": ["sql"],
                            },
                            "description": "List of SQL statements to execute",
                        },
                    },
                    "required": ["statements"],
                },
            },
            {
                "id": "sqlite.tables",
                "title": "List Tables",
                "description": "List all tables in the database",
                "capability": "sqlite.tables",
                "risk": "read",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["sqlite", "database", "schema", "tables"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "include_system": {"type": "boolean", "default": False, "description": "Include system tables"},
                    },
                },
            },
            {
                "id": "sqlite.schema",
                "title": "Get Table Schema",
                "description": "Get the schema of a specific table",
                "capability": "sqlite.schema",
                "risk": "read",
                "typical_latency_ms": 5,
                "tier": "task",
                "tags": ["sqlite", "database", "schema", "structure"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                    },
                    "required": ["table"],
                },
            },
        ]
    
    # Tool implementations
    def tool_execute(
        self,
        sql: str,
        params: tuple | list | dict | None = None,
        commit: bool = False,
        fetch: bool = True,
    ) -> dict[str, Any]:
        """Execute a SQL statement."""
        try:
            cursor = self._execute(sql, params, commit=commit)
            
            if fetch:
                rows = cursor.fetchall()
                result = {
                    "rows": [dict(row) for row in rows] if rows else [],
                    "row_count": len(rows) if rows else 0,
                }
            else:
                result = {"row_count": cursor.rowcount}
            
            if commit:
                result["committed"] = True
            
            return result
        except Exception as e:
            raise Exception(f"SQL execution failed: {e}")
    
    def tool_query(
        self,
        sql: str,
        params: tuple | list | dict | None = None,
        format: str = "dict",
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Execute a SELECT query."""
        try:
            cursor = self._execute(sql, params)
            rows = cursor.fetchall()
            
            if limit:
                rows = rows[:limit]
            
            if format == "dict":
                result = [dict(row) for row in rows]
            elif format == "list":
                result = [list(row) for row in rows]
            elif format == "json":
                result = [json.loads(json.dumps(dict(row))) for row in rows]
            else:  # raw
                result = [tuple(row) for row in rows]
            
            return {
                "results": result,
                "count": len(result),
            }
        except Exception as e:
            raise Exception(f"Query failed: {e}")
    
    def tool_create_table(
        self,
        table: str,
        schema: dict[str, str],
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create a new table."""
        try:
            # Build CREATE TABLE statement
            columns = [f"{name} {type_}" for name, type_ in schema.items()]
            sql = f"CREATE TABLE {'IF NOT EXISTS ' if if_not_exists else ''}{table} ({', '.join(columns)})"
            
            self._execute(sql, commit=True)
            
            return {
                "table": table,
                "created": True,
            }
        except Exception as e:
            raise Exception(f"Failed to create table: {e}")
    
    def tool_insert(
        self,
        table: str,
        data: dict[str, Any],
        many: bool = False,
        return_id: bool = False,
    ) -> dict[str, Any]:
        """Insert data into a table."""
        try:
            if many:
                if not isinstance(data, list):
                    raise ValueError("data must be a list when many=True")
                
                # Batch insert
                if data:
                    columns = list(data[0].keys())
                    placeholders = ", ".join(["?"] * len(columns))
                    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                    params_list = [tuple(row[col] for col in columns) for row in data]
                    
                    cursor = self._connection.cursor()
                    cursor.executemany(sql, params_list)
                    self._connection.commit()
                    
                    return {
                        "inserted": len(data),
                        "row_ids": list(cursor.lastrowid + i for i in range(len(data))) if return_id else None,
                    }
            else:
                # Single insert
                columns = list(data.keys())
                placeholders = ", ".join(["?"] * len(columns))
                sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                
                cursor = self._execute(sql, tuple(data.values()), commit=True)
                
                result = {"inserted": 1}
                if return_id:
                    result["row_id"] = cursor.lastrowid
                
                return result
        except Exception as e:
            raise Exception(f"Insert failed: {e}")
    
    def tool_select(
        self,
        table: str,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
        order_by: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        format: str = "dict",
    ) -> dict[str, Any]:
        """Select data from a table."""
        try:
            # Build SELECT statement
            cols = "*" if not columns else ", ".join(columns)
            sql = f"SELECT {cols} FROM {table}"
            
            params: list | dict = []
            
            # Add WHERE clause
            if where:
                conditions = []
                for col, val in where.items():
                    if isinstance(val, (list, tuple)):
                        placeholders = ", ".join(["?"] * len(val))
                        conditions.append(f"{col} IN ({placeholders})")
                        params.extend(val)
                    else:
                        conditions.append(f"{col} = ?")
                        params.append(val)
                sql += " WHERE " + " AND ".join(conditions)
            
            # Add ORDER BY
            if order_by:
                sql += f" ORDER BY {order_by}"
            
            # Add LIMIT and OFFSET
            if limit is not None:
                sql += f" LIMIT {limit}"
            if offset is not None:
                sql += f" OFFSET {offset}"
            
            cursor = self._execute(sql, tuple(params) if isinstance(params, list) else params)
            rows = cursor.fetchall()
            
            # Format results
            if format == "dict":
                result = [dict(row) for row in rows]
            elif format == "list":
                result = [list(row) for row in rows]
            elif format == "json":
                result = [json.loads(json.dumps(dict(row))) for row in rows]
            else:  # raw
                result = [tuple(row) for row in rows]
            
            return {
                "results": result,
                "count": len(result),
            }
        except Exception as e:
            raise Exception(f"Select failed: {e}")
    
    def tool_update(
        self,
        table: str,
        data: dict[str, Any],
        where: dict[str, Any],
        row_count: bool = False,
    ) -> dict[str, Any]:
        """Update data in a table."""
        try:
            # Build UPDATE statement
            sets = [f"{col} = ?" for col in data.keys()]
            sql = f"UPDATE {table} SET {', '.join(sets)}"
            
            params: list = list(data.values())
            
            # Add WHERE clause
            if where:
                conditions = []
                for col, val in where.items():
                    if isinstance(val, (list, tuple)):
                        placeholders = ", ".join(["?"] * len(val))
                        conditions.append(f"{col} IN ({placeholders})")
                        params.extend(val)
                    else:
                        conditions.append(f"{col} = ?")
                        params.append(val)
                sql += " WHERE " + " AND ".join(conditions)
            
            cursor = self._execute(sql, tuple(params), commit=True)
            
            result = {"updated": True}
            if row_count:
                result["row_count"] = cursor.rowcount
            
            return result
        except Exception as e:
            raise Exception(f"Update failed: {e}")
    
    def tool_delete(
        self,
        table: str,
        where: dict[str, Any],
        row_count: bool = False,
    ) -> dict[str, Any]:
        """Delete data from a table."""
        try:
            # Build DELETE statement
            sql = f"DELETE FROM {table}"
            
            params: list = []
            
            # Add WHERE clause
            if where:
                conditions = []
                for col, val in where.items():
                    if isinstance(val, (list, tuple)):
                        placeholders = ", ".join(["?"] * len(val))
                        conditions.append(f"{col} IN ({placeholders})")
                        params.extend(val)
                    else:
                        conditions.append(f"{col} = ?")
                        params.append(val)
                sql += " WHERE " + " AND ".join(conditions)
            
            cursor = self._execute(sql, tuple(params), commit=True)
            
            result = {"deleted": True}
            if row_count:
                result["row_count"] = cursor.rowcount
            
            return result
        except Exception as e:
            raise Exception(f"Delete failed: {e}")
    
    def tool_transaction(
        self,
        statements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute multiple SQL statements in a transaction."""
        try:
            cursor = self._connection.cursor()
            
            results = []
            for stmt in statements:
                sql = stmt.get("sql", "")
                params = stmt.get("params")
                
                if params:
                    if isinstance(params, dict):
                        cursor.execute(sql, params)
                    else:
                        cursor.execute(sql, params)
                else:
                    cursor.execute(sql)
                
                # Fetch results if this is a SELECT
                if sql.strip().upper().startswith("SELECT"):
                    rows = cursor.fetchall()
                    results.append({
                        "sql": sql,
                        "rows": [dict(row) for row in rows],
                        "row_count": len(rows),
                    })
                else:
                    results.append({
                        "sql": sql,
                        "row_count": cursor.rowcount,
                    })
            
            self._connection.commit()
            
            return {
                "statements": results,
                "committed": True,
            }
        except Exception as e:
            self._connection.rollback()
            raise Exception(f"Transaction failed: {e}")
    
    def tool_tables(
        self,
        include_system: bool = False,
    ) -> dict[str, Any]:
        """List all tables in the database."""
        try:
            if include_system:
                cursor = self._execute("SELECT name FROM sqlite_master WHERE type='table'")
            else:
                cursor = self._execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            
            rows = cursor.fetchall()
            tables = [row["name"] for row in rows]
            
            return {
                "tables": tables,
                "count": len(tables),
            }
        except Exception as e:
            raise Exception(f"Failed to list tables: {e}")
    
    def tool_schema(
        self,
        table: str,
    ) -> dict[str, Any]:
        """Get the schema of a specific table."""
        try:
            cursor = self._execute(f"PRAGMA table_info({table})")
            rows = cursor.fetchall()
            
            columns = []
            for row in rows:
                columns.append({
                    "cid": row["cid"],
                    "name": row["name"],
                    "type": row["type"],
                    "notnull": bool(row["notnull"]),
                    "default_value": row["dflt_value"],
                    "primary_key": bool(row["pk"]),
                })
            
            return {
                "table": table,
                "columns": columns,
                "column_count": len(columns),
            }
        except Exception as e:
            raise Exception(f"Failed to get schema: {e}")
