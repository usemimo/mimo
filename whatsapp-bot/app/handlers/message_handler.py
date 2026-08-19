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
from app.core.memory import MemoryStore

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

        text = (msg.text or "").strip()

        # Route all text messages through the LLM Gateway
        from app.core.llm import LLMGateway
        gateway = LLMGateway()
        
        extracted = await gateway.extract_intent(text)
        
        logger.info(f"LLM extracted intent: {extracted.intent}")
        
        if extracted.intent == "help":
            await self._handle_help(msg)
        elif extracted.intent == "unclear":
            if extracted.clarification_question:
                await self._adapter.send_text(msg.from_id, extracted.clarification_question)
            else:
                await self._handle_unknown_text(msg)
        elif extracted.intent.startswith("task_"):
            await self._handle_task_llm(msg, user_id, extracted)
        elif extracted.intent.startswith("memory_"):
            await self._handle_memory_llm(msg, user_id, extracted)
        else:
            await self._handle_unknown_text(msg)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _handle_task_llm(self, msg: InboundMessage, user_id: int, extracted: Any) -> None:
        """Task CRUD using LLM extracted intent and entities."""
        intent = extracted.intent.value
        entities = extracted.entities
        
        if intent == "task_add":
            title = entities.get("title", "New Task")
            due_str = entities.get("due_date")
            
            if due_str:
                from dateutil.parser import parse
                from dateutil.tz import gettz
                from datetime import timezone
                
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
                        (user_id, title, dt_utc.isoformat(), tz_str)
                    )
                    
                    row = await self._db.fetchone("SELECT id FROM tasks WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
                    task_id = row["id"]
                    
                    await self._db.execute(
                        "INSERT INTO reminders (task_id, trigger_time) VALUES (?, ?)", 
                        (task_id, dt_utc.isoformat())
                    )
                    
                    await self._adapter.send_text(msg.from_id, f"✅ Task added: *{title}* (Due: {dt.strftime('%Y-%m-%d %H:%M %Z')})")
                except Exception as e:
                    logger.error("Failed to parse due date from LLM", exc_info=e)
                    await self._adapter.send_text(msg.from_id, f"❌ Could not understand date: {due_str}")
            else:
                await self._db.execute(
                    "INSERT INTO tasks (user_id, title) VALUES (?, ?)", 
                    (user_id, title)
                )
                await self._adapter.send_text(msg.from_id, f"✅ Task added: *{title}*")
            
        elif intent == "task_list":
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
                
        elif intent == "task_done":
            await self._db.execute(
                "UPDATE tasks SET status = 'completed' WHERE user_id = ? AND status = 'pending'", 
                (user_id,)
            )
            await self._adapter.send_text(msg.from_id, "✅ Marked pending tasks as completed!")
            
        elif intent == "task_delete":
            await self._db.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
            await self._adapter.send_text(msg.from_id, "🗑️ Deleted all tasks!")

    async def _handle_memory_llm(self, msg: InboundMessage, user_id: int, extracted: Any) -> None:
        """Memory CRUD using LLM extracted intent and entities."""
        intent = extracted.intent.value
        entities = extracted.entities
        memory_store = MemoryStore(self._db)
        
        if intent == "memory_add":
            fact = entities.get("fact")
            if fact:
                await memory_store.save_fact(user_id=user_id, fact=fact, source="user_text")
                await self._adapter.send_text(msg.from_id, f"🧠 Memory saved: *{fact}*")
            else:
                await self._adapter.send_text(msg.from_id, "I couldn't figure out what fact you wanted me to remember.")
            
        elif intent == "memory_view":
            facts = await memory_store.get_active_facts(user_id=user_id)
            if facts:
                lines = [f"• {f['fact']}" for f in facts]
                await self._adapter.send_text(msg.from_id, "🧠 Here is what I remember about you:\n" + "\n".join(lines))
            else:
                await self._adapter.send_text(msg.from_id, "🧠 I don't have any memories saved for you yet.")
                
        elif intent == "memory_forget":
            await memory_store.forget_all(user_id=user_id)
            await self._adapter.send_text(msg.from_id, "🗑️ I have forgotten all memories about you.")

    async def _handle_help(self, msg: InboundMessage) -> None:
        logger.info("Sending help menu", extra={"to": msg.from_id})
        await self._adapter.send_buttons(to=msg.from_id, body=_HELP_BODY, buttons=_HELP_BUTTONS, footer=_HELP_FOOTER)

    async def _handle_non_text(self, msg: InboundMessage) -> None:
        logger.info("Sending non-text fallback", extra={"to": msg.from_id, "type": msg.message_type})
        await self._adapter.send_text(to=msg.from_id, text=_NON_TEXT_FALLBACK)

    async def _handle_unknown_text(self, msg: InboundMessage) -> None:
        logger.info("Sending fallback response", extra={"to": msg.from_id})
        await self._adapter.send_text(to=msg.from_id, text=_FALLBACK_TEXT)
