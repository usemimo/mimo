"""
app/db/base.py
--------------
Abstract async database interface.

This is the seam between application logic and the database engine.
Phase 2 plugs in SQLite (local file, survives restarts).
Phase 3 swaps in Postgres — zero changes to anything above this layer.

Rules
─────
• All methods are async — even if the Phase 2 implementation wraps
  synchronous SQLite, the interface is future-proof.
• No SQL leaks above this layer — callers use typed methods, not raw queries.
• `initialize()` is called once at startup; `close()` once at shutdown.
"""

from abc import ABC, abstractmethod
from typing import Any


class AsyncDatabase(ABC):
    """
    Minimal async database interface.

    Phase 3 note: When Postgres arrives, create a new subclass
    (e.g. AsyncPostgresDatabase) that implements these methods using asyncpg
    or SQLAlchemy async.  The idempotency layer and any other callers don't
    change — they depend on this ABC, not the concrete implementation.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """
        Create tables / run migrations on first start.
        Must be idempotent (safe to call on every startup).
        """

    @abstractmethod
    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        """Run a write query (INSERT, UPDATE, DELETE).  No return value."""

    @abstractmethod
    async def fetchone(
        self, query: str, params: tuple[Any, ...] = ()
    ) -> dict[str, Any] | None:
        """
        Run a SELECT and return the first row as a dict, or None if no rows.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release all connections / file handles."""
