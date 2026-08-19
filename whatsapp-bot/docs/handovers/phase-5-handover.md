# Phase 5 Handover — Long-Term Memory

## What was built
- **Memory Store Schema** (`app/db/schema.sql`) — Added `expiry_time` column to the `memory_facts` table.
- **Memory Service** (`app/core/memory.py`) — Implemented the `MemoryStore` class for handling memory CRUD logic (saving, retrieving active memories, and forgetting all facts for a user). It respects the `deletion_marker` and `expiry_time`.
- **Memory Text Commands** (`app/handlers/message_handler.py`) — Wired up basic text commands (`memory add`, `memory view`, `memory forget`) so users can test the memory store natively before the LLM takes over in Phase 6.

## Interfaces exposed to later phases
- **Database Table**: `memory_facts` containing columns for `fact`, `category`, `source`, `confidence`, `expiry_time`, `visibility`, and `deletion_marker`.
- **MemoryStore API** (`app/core/memory.py`):
  - `save_fact(user_id, fact, category, source, confidence, expiry_time)`
  - `get_active_facts(user_id, category)`
  - `forget_all(user_id)`

## Config / environment variables added
- No new environment variables added in this phase.

## How to run & test locally
```bash
# 1. Start the server
python -m uvicorn app.main:app --reload --port 8000

# 2. Test via Webhook using Postman/curl simulating a message:
# Send "memory add my favorite color is blue" to store a fact.
# Send "memory view" to see what the bot remembers.
# Send "memory forget" to delete all memories.
```

## Acceptance criteria — status
- [x] **Supermemory/DB integration** — PASS: Added robust standard PostgreSQL-based equivalent for storing facts.
- [x] **Structured facts** — PASS: Schema now has `source`, `confidence`, and `expiry_time`.
- [x] **Memory retrieval filters** — PASS: `MemoryStore.get_active_facts` automatically excludes expired or deleted facts.
- [x] **Deletion/view workflows** — PASS: `memory view` and `memory forget` allow the user to view and delete their data easily.

## Known limitations / TODOs for later phases
1. **Natural Language Processing**: The text commands (`memory add`, etc.) are temporary and rigid. Phase 6 (LLM Gateway) will completely replace this by auto-extracting facts contextually and saving them behind the scenes.
2. **Fact deduplication**: Currently, sending the exact same fact twice will store it twice. Phase 7 might handle deduplicating facts before insertion.
