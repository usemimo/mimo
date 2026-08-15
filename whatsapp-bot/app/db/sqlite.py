"""
app/db/sqlite.py
----------------
SQLite implementation of AsyncDatabase.

Uses aiosqlite — a thin asyncio wrapper around Python's built-in sqlite3.
This gives us a proper async interface while keeping zero external dependencies
beyond aiosqlite itself (no Rust compilation, no server to run).

Durability guarantee
────────────────────
WAL mode is enabled at connection time.  This means:
  • Writes survive a process crash (WAL is flushed before acknowledging commits).
  • Readers don't block writers.

Phase 3 note
────────────
When Postgres arrives, this file is replaced with AsyncPostgresDatabase.
The idempotency layer calls only AsyncDatabase methods — it never touches
SQLite-specific APIs — so the swap is mechanical.
"""

import aiosqlite

from app.core.logging import get_logger
from app.db.base import AsyncDatabase

logger = get_logger(__name__)


class SQLiteDatabase(AsyncDatabase):
    """
    SQLite-backed async database.

    Parameters
    ----------
    db_path : str
        Path to the SQLite file.  Relative paths are resolved from the cwd
        of the running process.  The parent directory must already exist.
        Default: ``data/idempotency.db``
    """

    def __init__(self, db_path: str = "data/idempotency.db") -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """
        Open the connection, enable WAL mode, and create tables.
        Must be called once at application startup (lifespan).
        """
        import os
        os.makedirs(os.path.dirname(self._db_path) if os.path.dirname(self._db_path) else ".", exist_ok=True)

        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row  # rows behave like dicts

        # WAL mode: durable writes, readers don't block writers
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")

        # ── Schema ────────────────────────────────────────────────────────────
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_messages (
                wamid           TEXT PRIMARY KEY,
                processed_at    TEXT NOT NULL,
                from_id         TEXT NOT NULL,
                correlation_id  TEXT NOT NULL
            )
        """)
        await self._conn.commit()

        logger.info("SQLite database initialised", extra={"db_path": self._db_path})

    async def execute(self, query: str, params: tuple = ()) -> None:
        """Run a write query and commit."""
        if self._conn is None:
            raise RuntimeError("Database not initialised — call initialize() first")
        await self._conn.execute(query, params)
        await self._conn.commit()

    async def fetchone(self, query: str, params: tuple = ()) -> dict | None:
        """Return the first row as a plain dict, or None."""
        if self._conn is None:
            raise RuntimeError("Database not initialised — call initialize() first")
        async with self._conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("SQLite database connection closed")

    @property
    def is_ready(self) -> bool:
        """True if the connection is open and usable."""
        return self._conn is not None
