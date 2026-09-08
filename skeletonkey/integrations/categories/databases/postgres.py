"""PostgreSQL Integration for SkeletonKey (P5.4).

This integration provides access to PostgreSQL databases with:
- Connection management
- Query execution
- Transaction support
- Connection pooling
- Result formatting
- Health checks

Requires: psycopg2 or psycopg2-binary
Install with: pip install psycopg2-binary
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
    name="postgresql",
    title="PostgreSQL Database",
    version="1.0.0",
    integration_type=IntegrationType.DATABASE,
    category=IntegrationCategory.DATABASE,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="PostgreSQL database integration for relational database operations",
    author="SkeletonKey Team",
    license="MIT",
    keywords=["postgresql", "postgres", "database", "sql", "relational", "psycopg2"],
    dependencies=[
        IntegrationDependency(
            name="psycopg2",
            version=">=2.9.0",
            optional=False,
            description="PostgreSQL adapter for Python",
        ),
    ],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access to PostgreSQL server",
            required=True,
            default_granted=False,
        ),
    ],
)
class PostgreSQLIntegration(Integration):
    """PostgreSQL database integration.
    
    This integration provides a full-featured PostgreSQL database client with
    support for:
    - Connection management with connection pooling
    - DDL operations (CREATE, ALTER, DROP)
    - DML operations (SELECT, INSERT, UPDATE, DELETE)
    - Transactions
    - Batch operations
    - Result formatting (dict, list, json)
    - SSL support
    """
    
    def __init__(self, config: IntegrationConfig):
        """Initialize the PostgreSQL integration."""
        super().__init__(config)
        self._connection: Any = None
        self._pool: Any = None
        self._initialized = False
        self._psycopg2: Any = None
    
    def initialize(self) -> bool:
        """Initialize the PostgreSQL integration."""
        try:
            # Import psycopg2
            try:
                import psycopg2
                self._psycopg2 = psycopg2
            except ImportError:
                try:
                    import psycopg2_binary as psycopg2
                    self._psycopg2 = psycopg2
                except ImportError:
                    raise ImportError(
                        "psycopg2 or psycopg2-binary is required for PostgreSQL integration. "
                        "Install with: pip install psycopg2-binary"
                    )
            
            # Connect to database
            self._connect()
            self._initialized = True
            logger.info("PostgreSQL integration initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL integration: {e}")
            self._initialized = False
            return False
    
    def cleanup(self) -> None:
        """Clean up the PostgreSQL integration."""
        if self._connection:
            try:
                self._connection.close()
                self._connection = None
                logger.info("PostgreSQL connection closed")
            except Exception as e:
                logger.error(f"Error closing PostgreSQL connection: {e}")
        
        if self._pool:
            try:
                self._pool.closeall()
                self._pool = None
                logger.info("PostgreSQL connection pool closed")
            except Exception as e:
                logger.error(f"Error closing PostgreSQL connection pool: {e}")
        
        self._initialized = False
    
    def _connect(self) -> None:
        """Establish database connection."""
        if self._connection:
            return
        
        # Get connection parameters from config
        host = self.config.config.get("host", "localhost")
        port = self.config.config.get("port", 5432)
        database = self.config.config.get("database", "postgres")
        user = self.config.config.get("user", "postgres")
        password = self.config.secrets.get("password", "") or self.config.config.get("password", "")
        
        # SSL configuration
        sslmode = self.config.config.get("sslmode", "prefer")
        sslrootcert = self.config.config.get("sslrootcert")
        sslcert = self.config.config.get("sslcert")
        sslkey = self.config.config.get("sslkey")
        
        # Connection timeout
        connect_timeout = self.config.config.get("connect_timeout", 5)
        
        # Build connection string
        conn_params = {
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "password": password,
            "sslmode": sslmode,
            "connect_timeout": connect_timeout,
        }
        
        # Add SSL files if specified
        if sslrootcert:
            conn_params["sslrootcert"] = sslrootcert
        if sslcert:
            conn_params["sslcert"] = sslcert
        if sslkey:
            conn_params["sslkey"] = sslkey
        
        # Connect
        self._connection = self._psycopg2.connect(**conn_params)
        
        # Set autocommit to False for transaction control
        self._connection.autocommit = False
    
    def _execute(
        self,
        sql: str,
        params: tuple | list | dict | None = None,
        commit: bool = False,
        fetch: bool = False,
    ) -> Any:
        """Execute a SQL statement."""
        if not self._connection or self._connection.closed:
            self._connect()
        
        cursor = self._connection.cursor()
        
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            
            if commit:
                self._connection.commit()
            
            if fetch:
                return cursor
            
            return cursor
        except Exception as e:
            self._connection.rollback()
            raise e
    
    def check_health(self) -> tuple[str, str]:
        """Check the health of the PostgreSQL integration."""
        try:
            if not self._initialized:
                return ("unhealthy", "Integration not initialized")
            
            # Test connection
            cursor = self._execute("SELECT 1", fetch=True)
            result = cursor.fetchone()
            
            if result and result[0] == 1:
                return ("healthy", "PostgreSQL database is accessible")
            else:
                return ("unhealthy", "PostgreSQL query test failed")
        except Exception as e:
            return ("unhealthy", f"Health check failed: {e}")
    
    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "postgresql.execute",
                "title": "Execute SQL",
                "description": "Execute a SQL statement on PostgreSQL",
                "capability": "postgresql.execute",
                "risk": "write",
                "typical_latency_ms": 20,
                "tier": "task",
                "tags": ["postgresql", "database", "query", "sql"],
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
                "id": "postgresql.query",
                "title": "Query Database",
                "description": "Execute a SELECT query on PostgreSQL",
                "capability": "postgresql.query",
                "risk": "read",
                "typical_latency_ms": 20,
                "tier": "task",
                "tags": ["postgresql", "database", "query", "select"],
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
                "id": "postgresql.tables",
                "title": "List Tables",
                "description": "List all tables in the PostgreSQL database",
                "capability": "postgresql.tables",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["postgresql", "database", "schema", "tables"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "schema": {"type": "string", "description": "Schema name (default: public)"},
                        "include_system": {"type": "boolean", "default": False, "description": "Include system tables"},
                    },
                },
            },
            {
                "id": "postgresql.schema",
                "title": "Get Table Schema",
                "description": "Get the schema of a specific table in PostgreSQL",
                "capability": "postgresql.schema",
                "risk": "read",
                "typical_latency_ms": 10,
                "tier": "task",
                "tags": ["postgresql", "database", "schema", "structure"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "table": {"type": "string", "description": "Table name"},
                        "schema": {"type": "string", "default": "public", "description": "Schema name"},
                    },
                    "required": ["table"],
                },
            },
            {
                "id": "postgresql.transaction",
                "title": "Execute Transaction",
                "description": "Execute multiple SQL statements in a PostgreSQL transaction",
                "capability": "postgresql.transaction",
                "risk": "write",
                "typical_latency_ms": 30,
                "tier": "task",
                "tags": ["postgresql", "database", "transaction", "batch"],
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
            cursor = self._execute(sql, params, commit=commit, fetch=fetch)
            
            if fetch:
                rows = cursor.fetchall()
                # Get column names
                col_names = [desc[0] for desc in cursor.description] if cursor.description else []
                result = {
                    "columns": col_names,
                    "rows": [dict(zip(col_names, row)) for row in rows] if rows else [],
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
            cursor = self._execute(sql, params, fetch=True)
            rows = cursor.fetchall()
            
            if limit:
                rows = rows[:limit]
            
            col_names = [desc[0] for desc in cursor.description] if cursor.description else []
            
            if format == "dict":
                result = [dict(zip(col_names, row)) for row in rows]
            elif format == "list":
                result = [list(row) for row in rows]
            elif format == "json":
                result = [json.loads(json.dumps(dict(zip(col_names, row)))) for row in rows]
            else:  # raw
                result = [tuple(row) for row in rows]
            
            return {
                "columns": col_names,
                "results": result,
                "count": len(result),
            }
        except Exception as e:
            raise Exception(f"Query failed: {e}")
    
    def tool_tables(
        self,
        schema: str = "public",
        include_system: bool = False,
    ) -> dict[str, Any]:
        """List all tables in the database."""
        try:
            if include_system:
                sql = """
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = %s 
                    ORDER BY table_name
                """
                params = (schema,)
            else:
                sql = """
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = %s 
                    AND table_name NOT LIKE 'pg_%' 
                    AND table_name NOT LIKE 'sqlite_%' 
                    ORDER BY table_name
                """
                params = (schema,)
            
            cursor = self._execute(sql, params, fetch=True)
            rows = cursor.fetchall()
            
            tables = [row[0] for row in rows]
            
            return {
                "schema": schema,
                "tables": tables,
                "count": len(tables),
            }
        except Exception as e:
            raise Exception(f"Failed to list tables: {e}")
    
    def tool_schema(
        self,
        table: str,
        schema: str = "public",
    ) -> dict[str, Any]:
        """Get the schema of a specific table."""
        try:
            sql = """
                SELECT column_name, data_type, is_nullable, column_default 
                FROM information_schema.columns 
                WHERE table_schema = %s AND table_name = %s 
                ORDER BY ordinal_position
            """
            params = (schema, table)
            
            cursor = self._execute(sql, params, fetch=True)
            rows = cursor.fetchall()
            
            columns = []
            for row in rows:
                columns.append({
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2] == "YES",
                    "default": row[3],
                })
            
            return {
                "schema": schema,
                "table": table,
                "columns": columns,
                "column_count": len(columns),
            }
        except Exception as e:
            raise Exception(f"Failed to get schema: {e}")
    
    def tool_transaction(
        self,
        statements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute multiple SQL statements in a transaction."""
        try:
            if not self._connection or self._connection.closed:
                self._connect()
            
            cursor = self._connection.cursor()
            results = []
            
            for stmt in statements:
                sql = stmt.get("sql", "")
                params = stmt.get("params")
                
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)
                
                # Fetch results if this is a SELECT
                if sql.strip().upper().startswith("SELECT"):
                    rows = cursor.fetchall()
                    col_names = [desc[0] for desc in cursor.description] if cursor.description else []
                    results.append({
                        "sql": sql,
                        "columns": col_names,
                        "rows": [dict(zip(col_names, row)) for row in rows],
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
