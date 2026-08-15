"""
app/schemas/whatsapp.py
-----------------------
Pydantic models for WhatsApp Cloud API payloads and our internal message schema.

Two layers
──────────
1.  Raw* models — mirror the WhatsApp Cloud API JSON structure exactly.
    Used only inside the webhook route for parsing; never passed downstream.

2.  InboundMessage — the internal, normalised representation.
    This is the contract that ALL downstream code (Phase 2 adapter,
    Phase 3+ orchestrator, Phase 6 LLM gateway) speaks.
    Change carefully; add fields, don't remove or rename existing ones.

WhatsApp payload reference
──────────────────────────
https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples
"""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Raw payload models (WhatsApp Cloud API shape) ─────────────────────────────

class RawTextBody(BaseModel):
    body: str


class RawMessage(BaseModel):
    id: str                          # wamid — the unique message ID
    from_: str = Field(alias="from") # sender's WA phone number (string)
    timestamp: str                   # unix epoch string from WA
    type: str                        # "text" | "image" | "audio" | "document" | …
    text: RawTextBody | None = None  # only present when type == "text"

    model_config = {"populate_by_name": True}


class RawContact(BaseModel):
    profile: dict[str, Any] = {}
    wa_id: str


class RawValue(BaseModel):
    messaging_product: str = ""
    metadata: dict[str, Any] = {}
    contacts: list[RawContact] = []
    messages: list[RawMessage] = []
    statuses: list[dict[str, Any]] = []   # delivery/read receipts — ignored in Phase 1


class RawChange(BaseModel):
    value: RawValue
    field: str = ""


class RawEntry(BaseModel):
    id: str = ""
    changes: list[RawChange] = []


class RawWebhookPayload(BaseModel):
    """Top-level WhatsApp Cloud API webhook payload."""
    object: str = ""
    entry: list[RawEntry] = []


# ── Internal normalised schema ─────────────────────────────────────────────────

class InboundMessage(BaseModel):
    """
    Internal representation of a normalised inbound WhatsApp message.

    Extension notes (for later phases)
    ────────────────────────────────────
    • Phase 2  — adds `correlation_id` before handing to the adapter.
    • Phase 3  — DB layer persists this object verbatim.
    • Phase 6  — LLM gateway reads `text` and `message_type`.
    • Never remove or rename existing fields; add new ones as Optional.
    """

    wamid: str
    """Unique WhatsApp message ID — used for idempotency deduplication."""

    from_id: str
    """Sender's WhatsApp phone number (string, e.g. '15550001234')."""

    message_type: str
    """
    One of: text | image | audio | video | document | location |
            contacts | sticker | reaction | unknown
    """

    text: str | None = None
    """Plain-text body — populated only when message_type == 'text'."""

    timestamp: datetime
    """UTC datetime of when WhatsApp recorded the message."""

    raw_type: str = ""
    """Original 'type' field from WA, preserved for debugging / future use."""


# ── Normalisation function ────────────────────────────────────────────────────

_KNOWN_TYPES = frozenset({
    "text", "image", "audio", "video", "document",
    "location", "contacts", "sticker", "reaction",
})


def normalize_payload(payload: RawWebhookPayload) -> list[InboundMessage]:
    """
    Extract and normalise every message from a raw WhatsApp webhook payload.

    Returns an empty list if the payload carries no messages (e.g. status-only
    events), which is a normal occurrence — do not treat it as an error.

    Raises
    ------
    Never raises — unknown fields are coerced to safe defaults so that
    malformed-but-parseable payloads produce a graceful result rather than
    crashing the webhook handler.
    """
    messages: list[InboundMessage] = []

    for entry in payload.entry:
        for change in entry.changes:
            if change.field != "messages":
                continue
            for raw_msg in change.value.messages:
                ts_unix = int(raw_msg.timestamp) if raw_msg.timestamp.isdigit() else 0
                msg_ts = datetime.fromtimestamp(ts_unix, tz=timezone.utc)

                msg_type = raw_msg.type if raw_msg.type in _KNOWN_TYPES else "unknown"
                text_body = raw_msg.text.body if raw_msg.text else None

                messages.append(
                    InboundMessage(
                        wamid=raw_msg.id,
                        from_id=raw_msg.from_,
                        message_type=msg_type,
                        text=text_body,
                        timestamp=msg_ts,
                        raw_type=raw_msg.type,
                    )
                )

    return messages
