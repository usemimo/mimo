# Phase 4 Handover — Scheduler & Reminders

## What was built
- **Durable Job Runner** (`app/workers/background.py`) — Completely eliminated the in-memory `asyncio.Queue`. The worker now continuously polls the PostgreSQL `reminders` table in the background.
- **Atomic Concurrency** — Implemented `FOR UPDATE SKIP LOCKED` inside the polling query. This guarantees that if multiple instances of this bot are deployed across different servers, they will safely load-balance reminders without ever double-firing a single message.
- **Timezone-Aware Delivery** — Timestamps are stored universally in UTC (`TIMESTAMP WITH TIME ZONE`). Reminders correctly match real-world due times based on timezone conversions.
- **Recurrence Engine (RRULE)** — Integrated `dateutil.rrule`. When a recurring reminder is sent successfully, the worker evaluates the `recurrence_rule` column and atomically computes and inserts the next occurrence into the `reminders` table.
- **Date Parsing for CRUD** — The temporary text-based `MessageHandler` now understands scheduled tasks. Users can type `task add buy milk due: 2026-08-17 15:00` and the system correctly parses the time and sets up the reminder.

## Interfaces exposed to later phases
- **Reminders Table**: You can interact directly with the `reminders` and `tasks` tables to manage schedules.
- **AsyncDatabase fetchall()**: Added `fetchall` to the `AsyncDatabase` interface for convenient multi-row retrieval.

## Config / environment variables added
- No new environment variables added in this phase.

## How to run & test locally
```bash
# 1. Start the server (worker loop automatically spins up in lifespan)
python -m uvicorn app.main:app --reload --port 8000

# 2. Test via Webhook
# Use Postman or curl to send a JSON payload mimicking:
# "task add test reminder due: 2026-10-31 12:00"
# It will insert into `tasks` and `reminders` and print success.

# 3. Run unit tests
python -m pytest tests/ -v
```

## Acceptance criteria — status
- [x] **Durable Queue Replaced** — PASS: The worker now polls the Postgres `reminders` table.
- [x] **Atomic Claims** — PASS: `SELECT ... FOR UPDATE SKIP LOCKED` guarantees safety in multi-worker scenarios.
- [x] **Timezone Support** — PASS: `dateutil.parser` and `dateutil.tz` correctly coerce times to UTC before database insertion.
- [x] **Recurrence Processing** — PASS: After successful delivery, `rrule` calculates the next execution time and inserts a new row.

## Known limitations / TODOs for later phases
1. **Natural Language Parsing**: The current date parsing requires an explicit `due:` keyword in a specific format. Phase 6 (LLM Gateway) will completely replace this with natural LLM intent extraction.
2. **Timezone configuration**: We currently default to `UTC` if a user's `timezone` column is null. Later phases might need to infer or ask the user for their timezone.
