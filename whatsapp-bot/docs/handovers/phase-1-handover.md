# Phase 1 Handover — Core FastAPI Scaffold

## What was built

- **FastAPI app factory** (`app/main.py`) — `create_app()` pattern with async lifespan context manager; docs only exposed in `development` env.
- **Environment config** (`app/core/config.py`) — `pydantic-settings` `Settings` class; all secrets loaded from env vars / `.env`; singleton via `@lru_cache`; hard startup failure if required vars are missing.
- **Structured logging** (`app/core/logging.py`) — JSON formatter (production) + human-readable formatter (development); **`correlation_id` injected via `contextvars.ContextVar`** at request entry so every downstream log line carries it automatically — no Phase 3 retrofit needed.
- **HMAC-SHA256 signature verification** (`app/core/security.py`) — reads raw bytes before JSON parsing; `hmac.compare_digest` for constant-time comparison; rejects missing, malformed-prefix, and wrong-secret headers.
- **Webhook routes** (`app/api/webhook.py`):
  - `GET /webhook` — Meta verification handshake; echoes `hub.challenge` on token match, 403 otherwise.
  - `POST /webhook` — Full pipeline: correlation ID → raw body read → HMAC verify → JSON parse → schema validate → normalise → stub processor.
- **Message normalisation** (`app/schemas/whatsapp.py`) — `RawWebhookPayload` mirrors WA Cloud API JSON exactly; `InboundMessage` is the clean internal contract; `normalize_payload()` never raises (unknown types → `"unknown"`, status-only events → empty list).
- **Test suite** (`tests/test_webhook.py`) — 17 tests covering all acceptance criteria.

## Interfaces exposed to later phases

### API Endpoints

| Method | Path | Request | Response |
|--------|------|---------|----------|
| `GET` | `/webhook` | Query params: `hub.mode`, `hub.verify_token`, `hub.challenge` | `200 text/plain` (challenge echo) or `403` |
| `POST` | `/webhook` | Raw JSON body + `X-Hub-Signature-256` header | `200 {"status":"ok","correlation_id":"<uuid>"}` or `400`/`401` |

### Internal Message Schema (primary contract for all later phases)

```python
class InboundMessage(BaseModel):
    wamid: str           # WhatsApp message ID — used for idempotency in Phase 2
    from_id: str         # Sender phone number string
    message_type: str    # "text"|"image"|"audio"|"video"|"document"|"location"|"contacts"|"sticker"|"reaction"|"unknown"
    text: str | None     # Body text — None for non-text types
    timestamp: datetime  # UTC datetime
    raw_type: str        # Original WA type field, preserved for debugging
```

### Extension Point (Phase 2 replaces this)

```python
# app/api/webhook.py
async def process_message(msg: InboundMessage) -> None:
    """Stub — Phase 2 replaces with real adapter (idempotency + reply dispatch)."""
```

### Logging Context API

```python
# app/core/logging.py
set_correlation_id(cid: str) -> None   # called at request entry
get_correlation_id() -> str            # called by every log formatter automatically
```

### Config

```python
# app/core/config.py
get_settings() -> Settings   # cached singleton; inject into any module
```

## Config / environment variables added

| Variable | Purpose | Example value |
|----------|---------|---------------|
| `WHATSAPP_APP_SECRET` | HMAC key for signature verification | `abcdef1234...` |
| `WHATSAPP_VERIFY_TOKEN` | Token matched during GET handshake | `my_secret_token` |
| `WHATSAPP_PHONE_NUMBER_ID` | WA Business phone number ID | `123456789012345` |
| `WHATSAPP_ACCESS_TOKEN` | API access token for outbound calls | `EAABxxxxxx...` |
| `LOG_LEVEL` | Python logging level | `INFO` |
| `APP_ENV` | `development` (human logs + docs) or `production` (JSON logs) | `development` |

Copy `.env.example` → `.env` and fill in real values. `.env` is in `.gitignore`.

## How to run & test locally

```bash
# 1. Install dependencies (--prefer-binary required on Python 3.14 — no MSVC needed)
pip install -r requirements.txt --prefer-binary

# 2. Set up environment
copy .env.example .env
# Edit .env with your real credentials

# 3. Run the server
python -m uvicorn app.main:app --reload --port 8000

# 4. Run tests (no .env required — tests inject their own env vars)
python -m pytest tests/test_webhook.py -v
```

**Expose locally via ngrok (for Meta webhook registration):**
```bash
ngrok http 8000
# Register https://<ngrok-id>.ngrok.io/webhook in Meta Developer Portal
```

## Acceptance criteria — status

- [x] **AC-1: Valid signed payload is accepted and normalised** — PASS
  - `TestValidPayload` (5 tests): 200 response, `{"status":"ok"}`, `correlation_id` in response, unknown message type gracefully normalised, status-only payload returns 200.
- [x] **AC-2: Invalid/missing signature rejected (401/403)** — PASS
  - `TestInvalidSignature` (4 tests): missing header → 401, wrong secret → 401, tampered body → 401, missing `sha256=` prefix → 401.
- [x] **AC-3: Malformed payloads return 400, no crash** — PASS
  - `TestMalformedPayloads` (5 tests): invalid JSON → 400, empty body → 400, JSON array → 400, wrong shape → handled (200 or 400, no crash), error response always carries `correlation_id`.
- [x] **AC-4 (bonus): GET verification handshake** — PASS
  - `TestVerificationHandshake` (3 tests): valid handshake echoes challenge, wrong token → 403, wrong mode → 403.

**Total: 17/17 tests passed**

## Known limitations / TODOs for later phases

1. **`process_message()` is a no-op stub** — Phase 2 replaces it with the real WhatsApp adapter (idempotency check → reply dispatch).
2. **No outbound API calls yet** — `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` are loaded but unused. Phase 2 uses them.
3. **No `/health` endpoint** — Phase 2 adds it.
4. **No idempotency** — Phase 2 adds `wamid`-based deduplication backed by SQLite (real Postgres in Phase 3).
5. **No background worker** — Phase 2 adds the skeleton; real jobs start Phase 4.
6. **`asyncio.iscoroutinefunction` deprecation warnings** from FastAPI/Starlette on Python 3.14 — cosmetic only, not a bug. Will be fixed by framework updates; no action needed in Phase 2.
7. **Python 3.14 compatibility note** — `pydantic-core` wheels for Python 3.14 must be installed with `--prefer-binary`; source compilation requires MSVC Build Tools. Document this in team onboarding.

---

## What's coming next — full project roadmap

### 🟡 Immediate next — Block A

#### Phase 2 — WhatsApp Adapter & Plumbing *(next)*
> Replaces the `process_message()` stub with a real end-to-end pipeline.
- **WhatsApp adapter** — `send_text`, `send_buttons`, `send_template`, `mark_read` via Cloud API
- **Idempotency layer** — `wamid` deduplication backed by SQLite (survives restarts)
- **Correlation IDs** — threaded through every log line for each inbound event
- **`GET /health`** — structured status check for all components
- **Help command + fallback** — first real message routing logic
- **Async scaffolding** — DB interface stub and background worker skeleton

---

### 🔵 Block B — Database & Memory *(future sessions)*

#### Phase 3 — PostgreSQL Schema & Task CRUD
- Swap SQLite for real Postgres (`asyncpg` / SQLAlchemy async)
- Tables: `users`, `conversations`, `messages`, `tasks`, `reminders`, `preferences`, `permissions`, `audit_events`
- Full task CRUD wired into the Phase 1/2 webhook endpoints
- Permissions and audit trail design

#### Phase 4 — Scheduler & Reminders
- Background scheduler for time-based reminders
- Atomic reminder claims (no double-fire across multiple workers)
- Recurrence rules (daily, weekly, custom)
- Timezone-aware due-date checking

#### Phase 5 — Long-Term Memory
- Supermemory (or equivalent) integration
- Structured facts with `source`, `confidence`, and `expiry` fields
- Retrieval filters (by user, topic, recency)
- User-facing view and delete workflows

---

### 🔴 Block C — AI Orchestration *(future sessions)*

#### Phase 6 — LLM Gateway
- Intent extraction from raw message text
- Entity parsing (dates, names, task descriptions)
- Clarification-question generation for ambiguous messages
- Plugs into the `_handle_unknown_text()` seam left in Phase 2

#### Phase 7 — Tool Planning & Response Drafting
- Typed, allowlisted action plans (no free-form tool calls)
- Schema validation before any tool executes
- Structured response drafting with LLM outputs

#### Phase 8 — Safety Layer
- Pre-generation content classification
- Post-generation response validation
- Age-gate and persona policy enforcement
- Sits on top of Blocks A/B — zero changes to earlier code

#### Phase 9 — Extensions *(if time allows)*
- Multi-step planning and task chaining
- Calendar integration (Google Calendar / Outlook)
- Email integration
- Never blocks project completion — purely additive
