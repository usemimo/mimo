"""
app/core/memory.py
------------------
Phase 5 Long-Term Memory Service.

Provides a clean interface for saving, retrieving, and forgetting user facts
stored in the `memory_facts` PostgreSQL table. Ensures that expired or deleted
facts are not retrieved.
"""
from __future__ import annotations

import datetime
from typing import Any, List

from app.db.base import AsyncDatabase
from app.core.logging import get_logger

logger = get_logger(__name__)

class MemoryStore:
    def __init__(self, db: AsyncDatabase) -> None:
        self._db = db

    async def save_fact(
        self,
        user_id: int,
        fact: str,
        category: str | None = None,
        source: str | None = None,
        confidence: float = 1.0,
        expiry_time: datetime.datetime | None = None
    ) -> int:
        """
        Saves a new fact for the user. Returns the inserted row ID.
        """
        expiry_str = expiry_time.isoformat() if expiry_time else None
        
        await self._db.execute(
            """
            INSERT INTO memory_facts (user_id, fact, category, source, confidence, expiry_time)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, fact, category, source, confidence, expiry_str)
        )
        
        row = await self._db.fetchone(
            "SELECT id FROM memory_facts WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        logger.info(f"Saved fact for user {user_id}", extra={"fact_id": row["id"]})
        return row["id"]

    async def get_active_facts(
        self,
        user_id: int,
        category: str | None = None
    ) -> List[dict[str, Any]]:
        """
        Retrieves all active facts for a user.
        Excludes facts that have the deletion_marker = TRUE or have expired.
        """
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        query = """
            SELECT id, fact, category, source, confidence, expiry_time, created_at
            FROM memory_facts
            WHERE user_id = ? 
              AND deletion_marker = FALSE
              AND (expiry_time IS NULL OR expiry_time > ?)
        """
        params = [user_id, now]
        
        if category:
            query += " AND category = ?"
            params.append(category)
            
        query += " ORDER BY created_at ASC"
        
        rows = await self._db.fetchall(query, tuple(params))
        return rows

    async def forget_all(self, user_id: int) -> int:
        """
        Marks all facts for a user as deleted.
        Returns the number of facts marked as deleted (we'll just return success since we can't easily get rowcount from custom execute).
        """
        await self._db.execute(
            """
            UPDATE memory_facts
            SET deletion_marker = TRUE, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND deletion_marker = FALSE
            """,
            (user_id,)
        )
        logger.info(f"Marked all facts as deleted for user {user_id}")
        return True
