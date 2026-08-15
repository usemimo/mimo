"""
app/api/webhook.py
------------------
GET  /webhook — WhatsApp verification handshake
POST /webhook — Inbound message events

Phase 2 changes vs Phase 1
────────────────────────────
• process_message() is no longer a stub — it now:
    1. Checks idempotency (wamid) — returns early on duplicate
    2. Routes through MessageHandler (help / fallback)
    3. Sends read receipt via the adapter
    4. Records wamid in the idempotency store
• app.state is used to access adapter, idempotency store, and handler.

The HTTP pipeline (signature verify → parse → normalise) is unchanged.
"""

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import get_correlation_id, get_logger, set_correlation_id
from app.core.security import verify_whatsapp_signature
from app.handlers.message_handler import MessageHandler
from app.schemas.whatsapp import InboundMessage, RawWebhookPayload, normalize_payload

router = APIRouter()
logger = get_logger(__name__)


# ── GET /webhook — verification handshake ─────────────────────────────────────

@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(request: Request) -> PlainTextResponse:
    """
    WhatsApp Cloud API verification handshake.

    Query params:  hub.mode | hub.verify_token | hub.challenge
    Success:       200 + plain-text hub.challenge
    Failure:       403
    """
    settings = get_settings()
    params = request.query_params

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        logger.info("Webhook verification succeeded")
        return PlainTextResponse(content=challenge or "", status_code=200)

    logger.warning(
        "Webhook verification failed",
        extra={"mode": mode, "token_match": token == settings.whatsapp_verify_token},
    )
    raise HTTPException(status_code=403, detail="Verification failed")


# ── POST /webhook — inbound events ────────────────────────────────────────────

@router.post("/webhook")
async def receive_webhook(request: Request) -> JSONResponse:
    """
    Full inbound processing pipeline.

    Steps
    ─────
    1.  Generate correlation ID → inject into log context
    2.  Read raw body bytes (before JSON parsing — needed for HMAC)
    3.  Verify X-Hub-Signature-256 → 401 on failure
    4.  Parse JSON → 400 on malformed
    5.  Validate RawWebhookPayload schema → 400 on schema error
    6.  Normalise → list[InboundMessage]
    7.  For each message:
        a. Idempotency check (wamid) → skip if duplicate, return 200
        b. Route through MessageHandler (help / fallback / Phase 6 seam)
        c. Mark message as read
        d. Record wamid in idempotency store
    8.  Return 200 — always (WhatsApp retries on non-2xx)
    """
    settings = get_settings()

    # 1. Correlation ID
    correlation_id = str(uuid.uuid4())
    set_correlation_id(correlation_id)
    logger.info("Inbound webhook received")

    # 2. Raw body
    try:
        raw_body = await request.body()
    except Exception as exc:
        logger.error("Failed to read request body", exc_info=exc)
        return JSONResponse(
            status_code=400,
            content={"error": "Could not read request body", "correlation_id": correlation_id},
        )

    # 3. Signature verification
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_whatsapp_signature(raw_body, signature, settings.whatsapp_app_secret):
        logger.warning("Rejected — invalid signature")
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid signature", "correlation_id": correlation_id},
        )

    # 4. JSON parse
    try:
        body_dict: Any = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.warning("Rejected — malformed JSON", extra={"detail": str(exc)})
        return JSONResponse(
            status_code=400,
            content={"error": "Malformed JSON", "correlation_id": correlation_id},
        )

    # 5. Schema validation
    try:
        payload = RawWebhookPayload.model_validate(body_dict)
    except ValidationError as exc:
        logger.warning("Rejected — schema validation failed", extra={"errors": exc.errors()})
        return JSONResponse(
            status_code=400,
            content={"error": "Payload validation failed", "correlation_id": correlation_id},
        )

    # 6. Normalise
    messages = normalize_payload(payload)
    logger.info(f"Normalised {len(messages)} message(s)")

    # 7. Process each message
    for msg in messages:
        await _process_message(request, msg, correlation_id)

    # 8. Always ACK
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "correlation_id": correlation_id},
    )


async def _process_message(
    request: Request, msg: InboundMessage, correlation_id: str
) -> None:
    """
    Full per-message pipeline: idempotency → route → receipt → record.

    Pulling app.state components from the request avoids module-level
    singleton state — makes testing easier (inject mocks via app.state).
    """
    idempotency = getattr(request.app.state, "idempotency", None)
    adapter = getattr(request.app.state, "adapter", None)

    logger.info(
        "Processing message",
        extra={
            "wamid": msg.wamid,
            "from_id": msg.from_id,
            "type": msg.message_type,
        },
    )

    # a. Idempotency check
    if idempotency is not None:
        if await idempotency.is_duplicate(msg.wamid):
            logger.info(
                "Duplicate message — returning 200, skipping side effects",
                extra={"wamid": msg.wamid},
            )
            return

    # b. Route through message handler
    if adapter is not None:
        handler = MessageHandler(adapter)
        try:
            await handler.handle(msg)
        except Exception as exc:
            logger.error(
                "MessageHandler raised an unexpected error",
                extra={"wamid": msg.wamid},
                exc_info=exc,
            )
            # Don't re-raise: we still want to mark as read + record idempotency

    # c. Send read receipt (best-effort)
    if adapter is not None:
        await adapter.mark_read(msg.wamid)

    # d. Record wamid (after all side effects are done)
    if idempotency is not None:
        await idempotency.record(
            wamid=msg.wamid,
            from_id=msg.from_id,
            correlation_id=correlation_id,
        )
