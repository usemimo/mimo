"""
app/handlers/message_handler.py
--------------------------------
First-level message routing — help command and fallback responses.

This is the ONLY place in Phase 2 that decides what reply to send.
It sits between the idempotency layer and the WhatsApp adapter.

Extension point for Phase 6 (LLM Gateway)
──────────────────────────────────────────
The `_handle_unknown_text()` method currently sends a hardcoded fallback.
Phase 6 replaces that single call site with the LLM orchestrator:

    # Phase 6 will do something like:
    intent = await llm_gateway.extract_intent(msg.text)
    reply  = await tool_router.execute(intent)
    await adapter.send_text(msg.from_id, reply)

No other method in this file needs to change.
"""

from app.adapters.whatsapp import ButtonOption, WhatsAppAdapter
from app.core.logging import get_logger
from app.schemas.whatsapp import InboundMessage

logger = get_logger(__name__)

# ── Help menu content ─────────────────────────────────────────────────────────

_HELP_BODY = (
    "👋 *Here's what I can help you with:*\n\n"
    "• *Tasks* — create and track your to-dos\n"
    "• *Reminders* — set time-based reminders\n"
    "• *Settings* — manage your preferences\n\n"
    "Tap a button below or type your request."
)

_HELP_BUTTONS = [
    ButtonOption(id="btn_tasks",     title="📋 My Tasks"),
    ButtonOption(id="btn_reminders", title="⏰ Reminders"),
    ButtonOption(id="btn_settings",  title="⚙️ Settings"),
]

_HELP_FOOTER = "WhatsApp AI Assistant"

# ── Fallback messages ─────────────────────────────────────────────────────────

_FALLBACK_TEXT = (
    "🤔 I didn't quite catch that.\n\n"
    "Type *help* to see what I can do, or describe what you need!"
)

_NON_TEXT_FALLBACK = (
    "📝 I can only handle text messages for now.\n\n"
    "Type *help* to see all available commands."
)


class MessageHandler:
    """
    Routes inbound messages and dispatches replies via the WhatsApp adapter.

    Parameters
    ----------
    adapter : WhatsAppAdapter
        The outbound WhatsApp API client.
    """

    def __init__(self, adapter: WhatsAppAdapter) -> None:
        self._adapter = adapter

    async def handle(self, msg: InboundMessage) -> None:
        """
        Entry point — route one normalised inbound message.

        Routing table
        ─────────────
        1. Non-text message type           → non-text fallback
        2. text == "help" or "/help"       → interactive help menu (buttons)
        3. Any other text                  → Phase 6 seam / fallback for now
        """
        logger.info(
            "Routing message",
            extra={
                "wamid": msg.wamid,
                "from_id": msg.from_id,
                "message_type": msg.message_type,
            },
        )

        if msg.message_type != "text":
            await self._handle_non_text(msg)
            return

        text = (msg.text or "").strip().lower()

        if text in ("help", "/help", "hi", "hello", "hey", "start"):
            await self._handle_help(msg)
        else:
            await self._handle_unknown_text(msg)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _handle_help(self, msg: InboundMessage) -> None:
        """Send the interactive help menu with buttons."""
        logger.info("Sending help menu", extra={"to": msg.from_id})
        await self._adapter.send_buttons(
            to=msg.from_id,
            body=_HELP_BODY,
            buttons=_HELP_BUTTONS,
            footer=_HELP_FOOTER,
        )

    async def _handle_non_text(self, msg: InboundMessage) -> None:
        """Send a friendly nudge when a non-text message is received."""
        logger.info(
            "Sending non-text fallback",
            extra={"to": msg.from_id, "type": msg.message_type},
        )
        await self._adapter.send_text(to=msg.from_id, text=_NON_TEXT_FALLBACK)

    async def _handle_unknown_text(self, msg: InboundMessage) -> None:
        """
        Fallback for unrecognised text input.

        ── Phase 6 EXTENSION POINT ──────────────────────────────────────────
        Replace the send_text call below with the LLM orchestrator call:

            intent = await llm_gateway.extract_intent(msg.text)
            reply  = await tool_router.execute(intent, msg)
            await self._adapter.send_text(msg.from_id, reply)

        This method signature stays the same; only its body changes.
        ─────────────────────────────────────────────────────────────────────
        """
        logger.info(
            "Sending fallback response (Phase 6 will replace this)",
            extra={"to": msg.from_id, "text_preview": (msg.text or "")[:40]},
        )
        await self._adapter.send_text(to=msg.from_id, text=_FALLBACK_TEXT)
