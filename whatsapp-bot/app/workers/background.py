"""
app/workers/background.py
--------------------------
Durable Postgres-backed background worker for Scheduler & Reminders.

Phase 4:
- Replaces in-memory queue with atomic PostgreSQL queries using `SKIP LOCKED`.
- Timezone aware (all DB times UTC).
- Dispatches reminders using WhatsApp adapter.
- Recalculates recurrences (RRULE) when jobs succeed.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from dateutil.rrule import rrulestr

from app.adapters.whatsapp import WhatsAppAdapter
from app.core.logging import get_logger
from app.db.base import AsyncDatabase
from app.db.postgres import AsyncPostgresDatabase

logger = get_logger(__name__)


async def _worker_loop(db: AsyncDatabase, adapter: WhatsAppAdapter) -> None:
    """
    Background worker loop.
    Polls the database for due reminders, atomically claims them,
    delivers them, and computes recurrence.
    """
    logger.info("Background worker loop started (Durable Postgres Worker)")
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"

    while True:
        try:
            # 1. Claim due reminders atomically
            # Note: FOR UPDATE SKIP LOCKED is Postgres-specific. 
            # If we are on SQLite (tests), we do a simpler graceful fallback.
            if isinstance(db, AsyncPostgresDatabase):
                claim_sql = """
                    UPDATE reminders 
                    SET delivery_status = 'claimed', claimed_by = $1, claimed_at = CURRENT_TIMESTAMP
                    WHERE id IN (
                        SELECT id FROM reminders 
                        WHERE delivery_status = 'pending' AND trigger_time <= CURRENT_TIMESTAMP
                        ORDER BY trigger_time ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 10
                    ) 
                    RETURNING *;
                """
                claimed = await db.fetchall(claim_sql, (worker_id,))
            else:
                # SQLite fallback for tests
                select_sql = """
                    SELECT id FROM reminders 
                    WHERE delivery_status = 'pending' AND trigger_time <= CURRENT_TIMESTAMP
                    ORDER BY trigger_time ASC
                    LIMIT 10;
                """
                pending = await db.fetchall(select_sql)
                claimed = []
                if pending:
                    ids = [str(r["id"]) for r in pending]
                    placeholders = ",".join(["?"] * len(ids))
                    update_sql = f"""
                        UPDATE reminders 
                        SET delivery_status = 'claimed', claimed_by = ?, claimed_at = CURRENT_TIMESTAMP
                        WHERE id IN ({placeholders})
                    """
                    await db.execute(update_sql, (worker_id, *ids))
                    
                    fetch_claimed_sql = f"SELECT * FROM reminders WHERE id IN ({placeholders})"
                    claimed = await db.fetchall(fetch_claimed_sql, tuple(ids))

            if claimed:
                logger.info("Claimed due reminders", extra={"count": len(claimed), "worker_id": worker_id})
                for reminder in claimed:
                    await _process_reminder(db, adapter, reminder)

        except asyncio.CancelledError:
            logger.info("Worker loop cancelled.")
            break
        except Exception as exc:
            logger.error("Background worker encountered an error", exc_info=exc)

        # Sleep before next poll
        await asyncio.sleep(5.0)


async def _process_reminder(db: AsyncDatabase, adapter: WhatsAppAdapter, reminder: dict[str, Any]) -> None:
    """Deliver the reminder and compute the next recurrence."""
    reminder_id = reminder["id"]
    task_id = reminder["task_id"]

    try:
        # Fetch task and user details
        task_sql = """
            SELECT t.title, t.recurrence_rule, u.whatsapp_id, u.timezone 
            FROM tasks t 
            JOIN users u ON t.user_id = u.id 
            WHERE t.id = ?
        """
        task = await db.fetchone(task_sql, (task_id,))
        if not task:
            logger.warning("Task not found for reminder", extra={"reminder_id": reminder_id})
            await db.execute("UPDATE reminders SET delivery_status = 'failed' WHERE id = ?", (reminder_id,))
            return

        # 1. Send the message
        title = task["title"]
        to = task["whatsapp_id"]
        
        success = await adapter.send_text(to=to, text=f"⏰ *Reminder*: {title}")
        
        # 2. Update status
        status = "delivered" if success else "failed"
        await db.execute("UPDATE reminders SET delivery_status = ? WHERE id = ?", (status, reminder_id))
        logger.info(f"Reminder {reminder_id} marked as {status}")

        # 3. Recurrence Logic
        if success and task["recurrence_rule"]:
            await _schedule_next_recurrence(db, task_id, task["recurrence_rule"], reminder["trigger_time"])

    except Exception as exc:
        logger.error("Failed to process reminder", extra={"reminder_id": reminder_id}, exc_info=exc)
        await db.execute("UPDATE reminders SET delivery_status = 'failed' WHERE id = ?", (reminder_id,))


async def _schedule_next_recurrence(db: AsyncDatabase, task_id: int, rrule_str: str, last_trigger_time: datetime | str) -> None:
    """Calculate and insert the next reminder based on an RRULE."""
    # Ensure datetime object
    if isinstance(last_trigger_time, str):
        # Try to parse ISO string
        try:
            last_trigger_time = datetime.fromisoformat(last_trigger_time.replace('Z', '+00:00'))
        except ValueError:
            # Fallback if the string is just simple datetime string in sqlite
            from dateutil.parser import parse
            last_trigger_time = parse(last_trigger_time)

    # Ensure UTC timezone
    if last_trigger_time.tzinfo is None:
        last_trigger_time = last_trigger_time.replace(tzinfo=timezone.utc)

    try:
        rule = rrulestr(rrule_str)
        naive_last = last_trigger_time.replace(tzinfo=None)
        next_naive = rule.after(naive_last)
        if next_naive:
            next_trigger = next_naive.replace(tzinfo=timezone.utc)
            await db.execute(
                "INSERT INTO reminders (task_id, trigger_time, delivery_status) VALUES (?, ?, 'pending')",
                (task_id, next_trigger.isoformat())
            )
            logger.info("Scheduled next recurrence", extra={"task_id": task_id, "next_trigger": next_trigger.isoformat()})
    except ValueError as exc:
        logger.error("Invalid RRULE", extra={"rrule": rrule_str, "task_id": task_id}, exc_info=exc)


def start_worker(db: AsyncDatabase, adapter: WhatsAppAdapter) -> asyncio.Task:
    """
    Start the worker loop as a background asyncio Task.
    Called once from the lifespan startup.
    """
    task = asyncio.ensure_future(_worker_loop(db, adapter))
    logger.info("Background worker task created")
    return task
