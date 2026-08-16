"""
app/main.py
-----------
FastAPI application factory — updated for Phase 2.

Phase 2 additions vs Phase 1
─────────────────────────────
• SQLite database initialised in lifespan startup, closed on shutdown.
• WhatsAppAdapter instantiated and stored on app.state.
• IdempotencyStore instantiated and stored on app.state.
• Background worker task started in lifespan.
• Health router mounted.
• All components accessible via request.app.state in route handlers.

Extension seams left open
──────────────────────────
• Phase 3 — swap SQLiteDatabase for AsyncPostgresDatabase in lifespan.
• Phase 4 — replace in-memory worker queue with durable queue.
• Phase 6+ — no changes to this file needed.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from app.adapters.whatsapp import WhatsAppAdapter
from app.api.health import router as health_router
from app.api.webhook import router as webhook_router
from app.core.config import get_settings
from app.core.idempotency import IdempotencyStore
from app.core.logging import get_logger, setup_logging
from app.db.postgres import AsyncPostgresDatabase
from app.workers.background import start_worker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Startup / shutdown lifecycle.

    Startup order (dependencies first):
      1. Logging
      2. Database (idempotency store)
      3. WhatsApp adapter
      4. IdempotencyStore wrapper
      5. Background worker task

    Shutdown order (reverse):
      5. Cancel worker
      3. Close adapter HTTP pool
      2. Close database
    """
    settings = get_settings()
    setup_logging(log_level=settings.log_level, app_env=settings.app_env)
    logger = get_logger(__name__)

    logger.info(
        "WhatsApp AI bot starting — Phase 2",
        extra={"env": settings.app_env},
    )

    # ── 1. Database ───────────────────────────────────────────────────────────
    db = AsyncPostgresDatabase(dsn=settings.postgres_dsn)
    await db.initialize()
    app.state.db = db

    # ── 2. WhatsApp adapter ───────────────────────────────────────────────────
    adapter = WhatsAppAdapter(
        phone_number_id=settings.whatsapp_phone_number_id,
        access_token=settings.whatsapp_access_token,
    )
    app.state.adapter = adapter

    # ── 3. Idempotency store ──────────────────────────────────────────────────
    app.state.idempotency = IdempotencyStore(db)

    # ── 4. Background worker ──────────────────────────────────────────────────
    worker_task = start_worker(db=db, adapter=adapter)
    app.state.worker_task = worker_task

    logger.info("All components initialised — ready to receive messages")

    # ── Startup complete ──────────────────────────────────────────────────────
    yield
    # ── Begin shutdown ────────────────────────────────────────────────────────

    logger.info("Shutting down — stopping components")

    # Cancel background worker
    worker_task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(worker_task), timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    # Close HTTP pool
    await adapter.close()

    # Close database
    await db.close()

    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """
    Application factory — returns a fully configured FastAPI instance.
    Import and call in tests and in the ASGI server entrypoint.
    """
    settings = get_settings()

    app = FastAPI(
        title="WhatsApp AI Assistant Bot",
        description=(
            "Backend for a WhatsApp AI assistant. "
            "Phase 2: full adapter, idempotency, health check, help/fallback."
        ),
        version="0.2.0",
        docs_url="/docs" if settings.app_env == "development" else None,
        redoc_url="/redoc" if settings.app_env == "development" else None,
        lifespan=lifespan,
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(webhook_router, tags=["Webhook"])
    app.include_router(health_router, tags=["Health"])

    return app


# ASGI entrypoint:
#   python -m uvicorn app.main:app --reload --port 8000
app = create_app()
