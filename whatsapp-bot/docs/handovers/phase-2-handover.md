# Phase 2 Handover — WhatsApp Adapter & Plumbing

## What was built

- **WhatsApp Cloud API adapter** (`app/adapters/whatsapp.py`)
  - `send_text()` — free-form text (within 24h window)
  - `send_buttons()` — interactive button message (max 3 buttons enforced)
  - `send_template()` — approved template send (works outside 24h window) — **separate code path from send_text, intentionally**
  - `mark_read()` — delivery/read receipt, best-effort
  - Shared `httpx.AsyncClient` connection pool; never raises — all failures logged with correlation_id

- **Async DB interface** (`app/db/base.py`) — `AsyncDatabase` ABC with `initialize / execute / fetchone / close`

- **SQLite implementation** (`app/db/sqlite.py`) — WAL mode, durable writes, `processed_messages` table

- **Idempotency layer** (`app/core/idempotency.py`) — `IdempotencyStore.is_duplicate(wamid)` + `record(wamid, from_id, correlation_id)` — check-before-act + record-after-act pattern, survives restarts

- **Message handler** (`app/handlers/message_handler.py`) — `MessageHandler.handle(msg: InboundMessage)`
  - `help` / `/help` / `hi` / `hello` / `hey` / `start` → interactive button menu
  - Non-text message types → plain-text fallback
  - All other text → plain-text fallback (**Phase 6 extension point clearly marked**)

- **Background worker skeleton** (`app/workers/background.py`) — `enqueue_job(job_type, payload)` interface + in-memory `asyncio.Queue`; Phase 4 replaces the implementation body

- **Health endpoint** (`app/api/health.py`) — `GET /health` with structured checks for idempotency store, adapter, and worker

- **Updated webhook pipeline** (`app/api/webhook.py`) — replaced Phase 1 stub with: idempotency check → `MessageHandler` → mark_read → record wamid

- **Updated main.py** — lifespan initialises DB → adapter → idempotency store → worker task; graceful shutdown in reverse order

- **Test infrastructure** (`tests/conftest.py`) — no-op lifespan patch + env var injection so all tests run without real credentials or network

## Interfaces exposed to later phases

### API Endpoints

| Method | Path | Response shape |
|--------|------|----------------|
| `GET` | `/webhook` | `200 text/plain` (challenge) or `403` |
| `POST` | `/webhook` | `200 {"status":"ok","correlation_id":"<uuid>"}` or `400`/`401` |
| `GET` | `/health` | `200/503 {"status":"healthy\|degraded\|unhealthy","version":"0.2.0","timestamp":"…","checks":{…}}` |

### app.state (available in every route via `request.app.state`)

| Attribute | Type | Set by |
|-----------|------|--------|
| `db` | `SQLiteDatabase` | lifespan startup |
| `adapter` | `WhatsAppAdapter` | lifespan startup |
| `idempotency` | `IdempotencyStore` | lifespan startup |
| `worker_task` | `asyncio.Task` | lifespan startup |

### Extension Points for Later Phases

```python
# Phase 3: swap this line in lifespan:
db = SQLiteDatabase(...)          # → db = AsyncPostgresDatabase(...)
# IdempotencyStore and all callers unchanged

# Phase 4: replace worker enqueue body:
async def enqueue_job(job_type, payload): ...   # same signature, durable queue backend

# Phase 6: replace this one call site in MessageHandler:
async def _handle_unknown_text(self, msg):
    await self._adapter.send_text(...)   # → await llm_gateway.process(msg)
```

### WhatsApp Adapter public interface

```python
adapter = WhatsAppAdapter(phone_number_id, access_token)
await adapter.send_text(to: str, text: str) -> bool
await adapter.send_buttons(to: str, body: str, buttons: list[ButtonOption], header?, footer?) -> bool
await adapter.send_template(to: str, template_name: str, language_code?: str, components?) -> bool
await adapter.mark_read(wamid: str) -> bool
await adapter.close()
```

## Config / environment variables added

| Variable | Purpose | Example |
|----------|---------|---------|
| `IDEMPOTENCY_DB_PATH` | SQLite file path for wamid deduplication | `data/idempotency.db` |

*(All Phase 1 variables unchanged)*

## How to run & test locally

```bash
# Install (--prefer-binary required on Python 3.14)
pip install -r requirements.txt --prefer-binary

# Set up .env
copy .env.example .env
# Fill in real WHATSAPP_* credentials

# Run server
python -m uvicorn app.main:app --reload --port 8000

# Health check
curl http://localhost:8000/health

# Run all tests (no credentials needed — conftest injects mocks)
python -m pytest tests/ -v
```

## Acceptance criteria — status

- [x] **AC-1: Bot reliably receives a WhatsApp message and sends a reply** — PASS
  - `TestEndToEndReply` (6 tests): text → reply, help → buttons, hello → buttons, mark_read called, non-text → fallback, unknown text → fallback.
- [x] **AC-2: Resending the same webhook event does not produce a duplicate reply** — PASS
  - `TestIdempotency` (3 tests): same wamid twice → one reply only, different wamids → each gets reply, duplicate returns 200.
- [x] **AC-3: /health returns a meaningful status** — PASS
  - `TestHealthEndpoint` (5 tests): 200 response, required fields present, idempotency_store=ok, whatsapp_adapter=configured, status is valid enum.
- [x] **AC-4: Help command + fallback for unrecognised input** — PASS (bonus)
  - `TestHelpAndFallback` (6 tests): help/slash-help/start → buttons, case-insensitive, fallback text non-empty, non-text fallback non-empty.

**Phase 1 tests still passing: 17/17**
**Phase 2 tests: 20/20**
**Total: 37/37 passed**

## Known limitations / TODOs for later phases

1. **SQLite → Postgres (Phase 3)** — swap `SQLiteDatabase` for `AsyncPostgresDatabase` in lifespan; `IdempotencyStore` and all callers are unchanged.
2. **In-memory worker queue (Phase 4)** — `enqueue_job()` currently uses `asyncio.Queue` (lost on restart); Phase 4 replaces with Redis/Postgres-backed queue.
3. **No 24-hour window detection** — `send_text` and `send_template` are separate code paths (by design); Phase 5+ adds the window-detection logic that calls one or the other.
4. **Adapter not integration-tested** — tested with mocked HTTP; a staging smoke-test against real Meta sandbox is recommended before production.
5. **`asyncio.iscoroutinefunction` deprecation warnings** — cosmetic, from FastAPI/Starlette on Python 3.14; no action needed until framework updates.
6. **`_handle_unknown_text` is the Phase 6 LLM seam** — one call site in `MessageHandler`, clearly documented. No structural changes needed when Phase 6 arrives.

---

## Block A milestone — complete ✅

> "A bot that reliably receives and replies."
>
> Both Phase 1 and Phase 2 acceptance criteria pass. All 37 tests green.
> Handovers written. Ready for Block B (Phase 3 — PostgreSQL Schema & Task CRUD).

---

## What's coming next — full project roadmap

### ✅ Block A — Backend Foundation *(this session — DONE)*

| Phase | Summary | Status |
|-------|---------|--------|
| Phase 1 | FastAPI scaffold, HMAC verification, message normalisation | ✅ Complete |
| Phase 2 | WA adapter, idempotency, health check, help/fallback, worker skeleton | ✅ Complete |

---

### 🔵 Block B — Database & Memory *(next sessions)*

#### Phase 3 — PostgreSQL Schema & Task CRUD
> **Immediate next phase.** Replaces the SQLite idempotency store with a real production database.
- Swap `SQLiteDatabase` → `AsyncPostgresDatabase` (one line change in lifespan — everything else unchanged)
- **New tables**: `users`, `conversations`, `messages`, `tasks`, `reminders`, `preferences`, `permissions`, `audit_events`
- Full **task CRUD** (create / read / update / delete) wired into the webhook endpoints
- Permissions model and audit trail for every write operation
- Needs: Postgres DSN in env vars, `asyncpg` or SQLAlchemy async dependency

#### Phase 4 — Scheduler & Reminders
> Turns the background worker skeleton (Phase 2) into a real job runner.
- Replace in-memory `asyncio.Queue` in `background.py` with a durable Postgres-backed or Redis queue
- **Background scheduler** for time-based reminder delivery
- **Atomic reminder claims** — prevents double-fire when multiple workers run in parallel
- Recurrence rules (daily / weekly / custom cron-style)
- Timezone-aware due-date checking (stores all times in UTC, renders in user's local timezone)

#### Phase 5 — Long-Term Memory
> Gives the bot persistent memory across conversations.
- **Supermemory** (or equivalent vector store) integration
- Structured facts stored with `source`, `confidence`, and `expiry` metadata
- Retrieval filters: by user, topic, recency, confidence threshold
- User-facing **view** and **delete** workflows ("forget everything about me")
- Needs: Section 10 of fuller product spec (memory retrieval/deletion design)

---

### 🔴 Block C — AI Orchestration *(future sessions)*

#### Phase 6 — LLM Gateway
> Replaces the `_handle_unknown_text()` stub in `MessageHandler` — the seam is already there.
- **Intent extraction** from raw message text (classify what the user wants)
- **Entity parsing** — dates, task descriptions, contact names, amounts
- **Clarification question generation** for ambiguous or incomplete messages
- Structured output — always returns typed objects, never free-form strings to downstream code

#### Phase 7 — Tool Planning & Response Drafting
> Sits between the LLM gateway and the WhatsApp adapter.
- **Typed, allowlisted action plans** — the LLM cannot call arbitrary tools
- Schema validation before any action executes (guard against hallucinated tool calls)
- **Response drafting** — LLM composes replies from structured tool results, not raw API data
- Fallback to human-readable error message if any step fails

#### Phase 8 — Safety Layer
> Wraps Blocks A + B without requiring changes to either.
- **Pre-generation content classification** — screen incoming messages before LLM processes them
- **Post-generation response validation** — screen outgoing replies before they are sent
- Age-gate enforcement (refuses certain content categories based on user profile)
- Persona policy enforcement (bot stays in character, refuses off-topic roleplay)
- Zero changes required to Phases 1–7 code — purely additive layer

#### Phase 9 — Extensions *(if time allows — never blocks completion)*
- **Multi-step planning** — chains of dependent tasks ("book a meeting, then send a summary")
- **Google Calendar / Outlook integration** — read and write calendar events
- **Email integration** — send summaries and follow-ups
- Fully additive — each extension is an independent plugin, not a rewrite

