"""
app/core/idempotency.py
-----------------------
Idempotency layer — deduplicates inbound WhatsApp messages on wamid.

Pattern: check-before-act + record-after-act
────────────────────────────────────────────
1. Before processing: check if wamid already exists in the store.
   → If yes: log and return True (caller skips all side effects).
   → If no:  return False (caller proceeds normally).

2. After processing successfully: record the wamid in the store.

This pattern is durable — the SQLite WAL file survives restarts, so a
message processed before a crash won't be re-processed on restart.

WhatsApp retry behaviour
────────────────────────
WhatsApp retries webhook delivery until it receives a 2xx response.
The idempotency layer ensures that retried webhooks:
  • Return 200 immediately (WhatsApp stops retrying).
  • Do NOT re-send a reply to the user.

Thread safety
─────────────
aiosqlite serialises writes, so there is no TOCTOU race condition
within a single process.  Cross-process deduplication (Phase 3+) will
rely on Postgres advisory locks or INSERT ... ON CONFLICT.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.db.base import AsyncDatabase

logger = get_logger(__name__)


class IdempotencyStore:
    """
    Wraps an AsyncDatabase to provide wamid-based deduplication.

    Usage
    ─────
    store = IdempotencyStore(db)

    # Before processing:
    if await store.is_duplicate(wamid):
        return  # skip — already handled

    # ... process the message ...

    # After processing:
    await store.record(wamid, from_id, correlation_id)
    """

    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def is_duplicate(self, wamid: str) -> bool:
        """
        Return True if this wamid has already been processed.

        Logs a warning (with correlation_id in context) so duplicates
        are always visible in the log stream.
        """
        row = await self._db.fetchone(
            "SELECT wamid, processed_at FROM processed_messages WHERE wamid = ?",
            (wamid,),
        )
        if row is not None:
            logger.warning(
                "Duplicate wamid detected — skipping re-processing",
                extra={
                    "wamid": wamid,
                    "first_processed_at": row["processed_at"],
                },
            )
            return True
        return False

    async def record(
        self,
        wamid: str,
        from_id: str,
        correlation_id: str,
    ) -> None:
        """
        Record that a wamid has been successfully processed.

        Called AFTER the message has been fully handled so that a crash
        mid-processing doesn't silently swallow the message.
        """
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT OR IGNORE INTO processed_messages
                (wamid, processed_at, from_id, correlation_id)
            VALUES (?, ?, ?, ?)
            """,
            (wamid, now, from_id, correlation_id),
        )
        logger.debug(
            "Recorded processed wamid",
            extra={"wamid": wamid, "from_id": from_id},
        )
