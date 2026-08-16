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
from __future__ import annotations

from app.adapters.whatsapp import ButtonOption, WhatsAppAdapter
from app.core.logging import get_logger
from app.db.base import AsyncDatabase
from app.schemas.whatsapp import InboundMessage

logger = get_logger(__name__)

# ── Help menu content ─────────────────────────────────────────────────────────

_HELP_BODY = (
    "👋 *Here's what I can help you with:*\n\n"
    "• *Tasks* — create and track your to-dos\n"
    "  (e.g., 'task add buy milk', 'task list', 'task done 1')\n"
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
    db : AsyncDatabase
        The database client for task CRUD.
    """

    def __init__(self, adapter: WhatsAppAdapter, db: AsyncDatabase) -> None:
        self._adapter = adapter
        self._db = db

    async def _get_or_create_user(self, whatsapp_id: str) -> int:
        """Upsert user and return their internal ID."""
        await self._db.execute(
            "INSERT INTO users (whatsapp_id) VALUES (?) ON CONFLICT (whatsapp_id) DO NOTHING",
            (whatsapp_id,)
        )
        row = await self._db.fetchone("SELECT id FROM users WHERE whatsapp_id = ?", (whatsapp_id,))
        return row["id"]

    async def handle(self, msg: InboundMessage) -> None:
        """
        Entry point — route one normalised inbound message.
        """
        logger.info("Routing message", extra={"wamid": msg.wamid, "from_id": msg.from_id, "message_type": msg.message_type})

        # Save message to DB
        user_id = await self._get_or_create_user(msg.from_id)
        
        await self._db.execute(
            """
            INSERT INTO messages (wamid, user_id, direction, message_type, normalized_text, correlation_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (wamid) DO NOTHING
            """,
            (msg.wamid, user_id, "inbound", msg.message_type, msg.text, msg.wamid)
        )

        if msg.message_type != "text":
            await self._handle_non_text(msg)
            return

        text = (msg.text or "").strip().lower()

        if text in ("help", "/help", "hi", "hello", "hey", "start"):
            await self._handle_help(msg)
        elif text.startswith("task "):
            await self._handle_task_crud(msg, user_id, text)
        else:
            await self._handle_unknown_text(msg)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _handle_task_crud(self, msg: InboundMessage, user_id: int, text: str) -> None:
        """Basic Task CRUD for Phase 3 before LLM takes over."""
        parts = text.split(" ", 2)
        cmd = parts[1] if len(parts) > 1 else ""
        
        if cmd == "add" and len(parts) > 2:
            title = msg.text.strip()[9:] # preserve original case
            
            # Check for due date
            if " due:" in title.lower():
                import re
                from dateutil.parser import parse
                from dateutil.tz import gettz
                from datetime import timezone
                
                # Case insensitive split for " due:"
                match = re.search(r'(?i)\s+due:', title)
                main_title = title[:match.start()].strip()
                due_str = title[match.end():].strip()
                
                user_row = await self._db.fetchone("SELECT timezone FROM users WHERE id = ?", (user_id,))
                tz_str = user_row["timezone"] if user_row and user_row.get("timezone") else "UTC"
                tz = gettz(tz_str) or timezone.utc
                
                try:
                    dt = parse(due_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=tz)
                    dt_utc = dt.astimezone(timezone.utc)
                    
                    await self._db.execute(
                        "INSERT INTO tasks (user_id, title, due_time, timezone) VALUES (?, ?, ?, ?)", 
                        (user_id, main_title, dt_utc.isoformat(), tz_str)
                    )
                    
                    row = await self._db.fetchone("SELECT id FROM tasks WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
                    task_id = row["id"]
                    
                    await self._db.execute(
                        "INSERT INTO reminders (task_id, trigger_time) VALUES (?, ?)", 
                        (task_id, dt_utc.isoformat())
                    )
                    
                    await self._adapter.send_text(msg.from_id, f"✅ Task added: *{main_title}* (Due: {dt.strftime('%Y-%m-%d %H:%M %Z')})")
                except Exception as e:
                    logger.error("Failed to parse due date", exc_info=e)
                    await self._adapter.send_text(msg.from_id, f"❌ Could not understand date: {due_str}")
            else:
                await self._db.execute(
                    "INSERT INTO tasks (user_id, title) VALUES (?, ?)", 
                    (user_id, title)
                )
                await self._adapter.send_text(msg.from_id, f"✅ Task added: *{title}*")
            
        elif cmd == "list":
            rows = await self._db.fetchall("SELECT title, due_time FROM tasks WHERE user_id = ? AND status = 'pending' LIMIT 5", (user_id,))
            if rows:
                lines = []
                for r in rows:
                    if r.get("due_time"):
                        lines.append(f"• {r['title']} (Due: {r['due_time'][:16]})")
                    else:
                        lines.append(f"• {r['title']}")
                await self._adapter.send_text(msg.from_id, "📋 Pending tasks:\n" + "\n".join(lines))
            else:
                await self._adapter.send_text(msg.from_id, "📭 No pending tasks!")
                
        elif cmd == "done" and len(parts) > 2:
            # We will just mark the last task as done for simplicity of demonstration
            await self._db.execute(
                "UPDATE tasks SET status = 'completed' WHERE user_id = ? AND status = 'pending'", 
                (user_id,)
            )
            await self._adapter.send_text(msg.from_id, "✅ Marked pending tasks as completed!")
            
        elif cmd == "delete":
            await self._db.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
            await self._adapter.send_text(msg.from_id, "🗑️ Deleted all tasks!")
            
        else:
            await self._adapter.send_text(msg.from_id, "Command not recognized. Use: task add [name], task list, task done, task delete")

    async def _handle_help(self, msg: InboundMessage) -> None:
        logger.info("Sending help menu", extra={"to": msg.from_id})
        await self._adapter.send_buttons(to=msg.from_id, body=_HELP_BODY, buttons=_HELP_BUTTONS, footer=_HELP_FOOTER)

    async def _handle_non_text(self, msg: InboundMessage) -> None:
        logger.info("Sending non-text fallback", extra={"to": msg.from_id, "type": msg.message_type})
        await self._adapter.send_text(to=msg.from_id, text=_NON_TEXT_FALLBACK)

    async def _handle_unknown_text(self, msg: InboundMessage) -> None:
        logger.info("Sending fallback response", extra={"to": msg.from_id})
        await self._adapter.send_text(to=msg.from_id, text=_FALLBACK_TEXT)
