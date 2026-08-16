"""
app/db/postgres.py
------------------
PostgreSQL implementation of AsyncDatabase using asyncpg.

Uses a connection pool to manage concurrent requests efficiently.
Handles conversion of sqlite '?' parameter markers to postgres '$N' markers
for backward compatibility with existing callers, ensuring a drop-in replacement.
"""
from __future__ import annotations

import asyncpg
from typing import Any

from app.core.logging import get_logger
from app.db.base import AsyncDatabase

logger = get_logger(__name__)


def _convert_query(query: str) -> str:
    """
    Convert SQLite style '?' placeholders to PostgreSQL '$1, $2' placeholders,
    and convert 'INSERT OR IGNORE' to 'INSERT ... ON CONFLICT DO NOTHING'.
    This allows existing code (like IdempotencyStore) to run unmodified.
    """
    # Replace ? with $1, $2, etc.
    parts = query.split('?')
    if len(parts) > 1:
        query = "".join(f"{part}${i+1}" if i < len(parts) - 1 else part for i, part in enumerate(parts))
    
    # Very basic conversion for INSERT OR IGNORE
    if "INSERT OR IGNORE INTO" in query:
        query = query.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        if "ON CONFLICT" not in query:
            # We assume it's for idempotency table wamid PK
            query += " ON CONFLICT (wamid) DO NOTHING"
            
    return query


class AsyncPostgresDatabase(AsyncDatabase):
    """
    PostgreSQL-backed async database using asyncpg connection pooling.
    """

    def __init__(self, dsn: str, schema_path: str = "app/db/schema.sql") -> None:
        self._dsn = dsn
        self._schema_path = schema_path
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        """
        Create the connection pool and run the idempotent schema script.
        """
        logger.info("Initializing Postgres connection pool...")
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=1,
            max_size=10,
            command_timeout=60,
        )

        # Run schema script
        try:
            with open(self._schema_path, "r", encoding="utf-8") as f:
                schema_sql = f.read()
            
            async with self._pool.acquire() as conn:
                await conn.execute(schema_sql)
                logger.info("Postgres schema applied successfully")
        except FileNotFoundError:
            logger.warning(f"Schema file not found at {self._schema_path}. Skipping schema initialization.")

        logger.info("Postgres database initialized")

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        """Run a write query."""
        if self._pool is None:
            raise RuntimeError("Database not initialised — call initialize() first")
        
        pg_query = _convert_query(query)
        async with self._pool.acquire() as conn:
            await conn.execute(pg_query, *params)

    async def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        """Return the first row as a dict, or None."""
        if self._pool is None:
            raise RuntimeError("Database not initialised — call initialize() first")
        
        pg_query = _convert_query(query)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(pg_query, *params)
            if row is None:
                return None
            return dict(row)

    async def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Return all rows as a list of dicts."""
        if self._pool is None:
            raise RuntimeError("Database not initialised — call initialize() first")
        
        pg_query = _convert_query(query)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(pg_query, *params)
            return [dict(r) for r in rows]

    async def close(self) -> None:
        """Close the database connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Postgres connection pool closed")

    @property
    def is_ready(self) -> bool:
        """True if the connection pool is open and usable."""
        return self._pool is not None
