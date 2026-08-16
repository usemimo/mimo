# Phase 3 Handover — PostgreSQL Schema & Task CRUD

## What was built
- **PostgreSQL Database Implementation** (`app/db/postgres.py`) — `AsyncPostgresDatabase` implements the abstract interface using `asyncpg` pools for high-concurrency cloud deployments (no ORMs).
- **SQL-to-Postgres compatibility layer** — The database layer intercepts queries and maps SQLite `?` bindings to Postgres `$1` bindings and simple `INSERT OR IGNORE` to Postgres syntax, enabling a seamless transition for previous SQLite callers (like the `IdempotencyStore`).
- **PostgreSQL Database Schema** (`app/db/schema.sql`) — Idempotent pure SQL file that runs on startup creating all domain model tables (`users`, `conversations`, `messages`, `tasks`, `reminders`, `preferences`, `memory_facts`, `permissions`, `safety_events`, `audit_events`, `processed_messages`).
- **Python 3.9 Backport Compatibility** — Used `from __future__ import annotations` and installed `eval_type_backport` so that Pydantic models using new Type syntaxes run successfully across Python versions without rewriting types.
- **Task CRUD integration** (`app/handlers/message_handler.py`) — Before Phase 6 arrives, a basic text parser was added that upserts users to the DB and allows `task add [title]`, `task list`, `task done [id]`, and `task delete`. This writes and reads directly from the new `tasks` table.

## Interfaces exposed to later phases
- **DB tables added**:
  - `users`: Core user profiles and state.
  - `conversations`: Ongoing context logic.
  - `messages`: Historical normalized messages.
  - `tasks`: Actionable to-do items.
  - `reminders`: Time-based alerts tied to tasks.
  - `preferences`: User settings.
  - `memory_facts`: Long-term fact store.
  - `permissions`: OAuth/integration scopes.
  - `safety_events`: Violations and filters.
  - `audit_events`: System audit log.
- **AsyncPostgresDatabase (app/db/postgres.py)**:
  - `await db.initialize()`
  - `await db.execute(query, params)`
  - `await db.fetchone(query, params)`

## Config / environment variables added
| Variable | Purpose | Example value |
|----------|---------|---------------|
| `POSTGRES_DSN` | Connection string for the PostgreSQL database pool. Supports local or cloud DBs. | `postgresql://user:password@localhost:5432/mimo` |

## How to run & test locally
```bash
# 1. Install dependencies (now includes asyncpg and eval_type_backport)
pip install -r requirements.txt
pip install eval_type_backport

# 2. Set up environment (add your POSTGRES_DSN)
# You can use a cloud DB like Neon.tech or Supabase if you don't have Postgres locally.
# The app_env defaults to development.
export POSTGRES_DSN="postgresql://[user]:[password]@[host]:5432/[db]"

# 3. Run the server
python -m uvicorn app.main:app --reload --port 8000

# 4. Run tests
# Note: Tests still run against an in-memory mock SQLite instance in `conftest.py` so they are fully offline and fast.
python -m pytest tests/ -v
```

## Acceptance criteria — status
- [x] **Migrations run cleanly against a fresh DB** — PASS: The `schema.sql` is fully idempotent and applies immediately on lifespan startup via `asyncpg`.
- [x] **Message round-trips through normalized-message → DB storage path** — PASS: `MessageHandler` now tracks users and saves every inbound normalized message to the `messages` table.
- [x] **Tasks can be created/read/updated/deleted through wired endpoints** — PASS: Simple CRUD text commands (`task add/list/done/delete`) are mapped through the `MessageHandler` and persist to the Postgres `tasks` table.

## Known limitations / TODOs for later phases
1. **Background Worker** — The background worker queue is still an in-memory `asyncio.Queue` (Phase 2 limitation). It will be swapped out for a durable job runner in Phase 4.
2. **Text Parsing CRUD** — The task CRUD is currently string-matching (e.g. `task add`). This is temporary and designed to be deleted when Phase 6 introduces the LLM Gateway.
3. **Tests use SQLite** — The `conftest.py` injects a mocked SQLite database so that tests don't require an active Postgres connection. If you wish to test postgres-specific SQL logic, a Postgres testcontainer fixture will be needed in the future.
