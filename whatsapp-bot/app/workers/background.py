"""
app/workers/background.py
--------------------------
Background worker skeleton.

Phase 2 scope: define the interface and enqueue stub only.
Real jobs start in Phase 4 (Scheduler & Reminders).

Design
──────
Jobs are represented as typed dicts with a `job_type` discriminator.
The worker loop (Phase 4) will pattern-match on `job_type` to dispatch
to the correct handler.

Extension point for Phase 4
────────────────────────────
1. Replace `enqueue_job` body with a real queue push (e.g. asyncio.Queue,
   Redis RPUSH, or a Postgres-backed table).
2. Start a `process_jobs()` coroutine in the lifespan startup.
3. No other code needs to change — callers already use `enqueue_job()`.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_correlation_id, get_logger

logger = get_logger(__name__)

# In-memory queue placeholder — Phase 4 replaces with durable queue
_job_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


async def enqueue_job(job_type: str, payload: dict[str, Any]) -> None:
    """
    Enqueue a background job.

    Phase 2: puts job into an in-memory asyncio.Queue (not durable).
    Phase 4: replaces implementation with a durable queue.

    Parameters
    ----------
    job_type : str
        Discriminator string, e.g. "send_reminder", "sync_tasks".
    payload : dict
        Job-specific data.  Must be JSON-serialisable.
    """
    job = {
        "job_type": job_type,
        "payload": payload,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "correlation_id": get_correlation_id(),
    }
    await _job_queue.put(job)
    logger.debug(
        "Job enqueued (in-memory — not durable, Phase 4 makes this durable)",
        extra={"job_type": job_type, "queue_size": _job_queue.qsize()},
    )


async def _worker_loop() -> None:
    """
    Background worker loop skeleton.

    Phase 2: drains jobs and logs them (no real work done).
    Phase 4: replace the log call with real job dispatch.
    """
    logger.info("Background worker loop started (skeleton)")
    while True:
        job = await _job_queue.get()
        try:
            logger.info(
                "Background job received (no-op in Phase 2)",
                extra={
                    "job_type": job["job_type"],
                    "correlation_id": job.get("correlation_id"),
                },
            )
            # Phase 4 dispatches here:
            # await dispatch_job(job)
        except Exception as exc:
            logger.error(
                "Background job failed",
                extra={"job": job},
                exc_info=exc,
            )
        finally:
            _job_queue.task_done()


def start_worker(loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Task:
    """
    Start the worker loop as a background asyncio Task.
    Called once from the lifespan startup.

    Returns the Task so lifespan can cancel it on shutdown.
    """
    task = asyncio.ensure_future(_worker_loop())
    logger.info("Background worker task created")
    return task
