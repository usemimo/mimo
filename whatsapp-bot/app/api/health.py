"""
app/api/health.py
-----------------
GET /health — liveness and readiness check.

Returns a structured status payload indicating:
  • Overall health (healthy / degraded / unhealthy)
  • Individual component checks
  • App version and current timestamp

Design
──────
• The endpoint never raises — a crashing health check is worse than a
  degraded one.  Component failures lower the overall status but always
  return a response.
• HTTP status codes:
    200 → healthy or degraded (app is running, some components may be slow)
    503 → unhealthy (critical components down)
• Degraded vs unhealthy distinction lets load balancers keep a degraded
  instance in rotation while alerting on duty.

Extension point for Phase 3+
────────────────────────────
Add more component checks (postgres, redis, external APIs) by appending
to the `checks` dict inside `health_check()`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)

APP_VERSION = "0.2.0"


@router.get("/health", tags=["Health"])
async def health_check(request: Request) -> JSONResponse:
    """
    Returns the current health status of the bot and its dependencies.

    Response shape
    ──────────────
    {
      "status": "healthy" | "degraded" | "unhealthy",
      "version": "0.2.0",
      "timestamp": "<ISO-8601 UTC>",
      "checks": {
        "idempotency_store": "ok" | "error: <reason>",
        "whatsapp_adapter":  "configured" | "not_configured",
        "background_worker": "running" | "not_started"
      }
    }
    """
    checks: dict[str, str] = {}
    overall = "healthy"

    # ── 1. Idempotency store (SQLite) ─────────────────────────────────────────
    try:
        db = getattr(request.app.state, "db", None)
        if db is not None and getattr(db, "is_ready", False):
            # Quick smoke-test: run a trivial query
            result = await db.fetchone("SELECT 1 AS ok")
            checks["idempotency_store"] = "ok" if result else "no_result"
        else:
            checks["idempotency_store"] = "not_initialised"
            overall = "degraded"
    except Exception as exc:
        checks["idempotency_store"] = f"error: {exc!s:.80}"
        overall = "degraded"
        logger.error("Health check: idempotency_store failed", exc_info=exc)

    # ── 2. WhatsApp adapter ───────────────────────────────────────────────────
    try:
        adapter = getattr(request.app.state, "adapter", None)
        checks["whatsapp_adapter"] = "configured" if adapter is not None else "not_configured"
        if adapter is None:
            overall = "degraded"
    except Exception as exc:
        checks["whatsapp_adapter"] = f"error: {exc!s:.80}"
        overall = "degraded"

    # ── 3. Background worker ──────────────────────────────────────────────────
    try:
        worker_task = getattr(request.app.state, "worker_task", None)
        if worker_task is not None and not worker_task.done():
            checks["background_worker"] = "running"
        else:
            checks["background_worker"] = "not_started"
    except Exception:
        checks["background_worker"] = "unknown"

    payload = {
        "status": overall,
        "version": APP_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }

    http_status = 200 if overall in ("healthy", "degraded") else 503
    logger.info("Health check", extra={"status": overall, "checks": checks})
    return JSONResponse(content=payload, status_code=http_status)
