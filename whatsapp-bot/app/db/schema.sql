-- app/db/schema.sql
-- Idempotent schema initialization for PostgreSQL
-- Run at application startup.

-- 1. processed_messages (for IdempotencyStore)
CREATE TABLE IF NOT EXISTS processed_messages (
    wamid TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL,
    from_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL
);

-- 2. users
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    whatsapp_id TEXT UNIQUE NOT NULL,
    display_name TEXT,
    timezone TEXT,
    locale TEXT,
    onboarding_state TEXT DEFAULT 'new',
    age_gate_status TEXT DEFAULT 'unknown',
    persona_mode TEXT DEFAULT 'neutral',
    opt_out_status BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. conversations
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    conversation_status TEXT DEFAULT 'active',
    last_inbound_timestamp TIMESTAMP WITH TIME ZONE,
    last_outbound_timestamp TIMESTAMP WITH TIME ZONE,
    customer_service_window_state TEXT DEFAULT 'open',
    summary_reference TEXT
);

-- 4. messages
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    wamid TEXT UNIQUE NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_type TEXT NOT NULL,
    normalized_text TEXT,
    provider_status TEXT,
    correlation_id TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. tasks
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'deleted')),
    priority TEXT DEFAULT 'medium' CHECK (priority IN ('low', 'medium', 'high')),
    due_time TIMESTAMP WITH TIME ZONE,
    timezone TEXT,
    recurrence_rule TEXT,
    tags TEXT,
    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    completed_timestamp TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. reminders
CREATE TABLE IF NOT EXISTS reminders (
    id SERIAL PRIMARY KEY,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    trigger_time TIMESTAMP WITH TIME ZONE NOT NULL,
    delivery_status TEXT DEFAULT 'pending' CHECK (delivery_status IN ('pending', 'claimed', 'delivered', 'failed')),
    retry_count INTEGER DEFAULT 0,
    snooze_history TEXT,
    template_decision TEXT,
    claimed_by TEXT,
    claimed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 7. preferences
CREATE TABLE IF NOT EXISTS preferences (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT,
    confidence REAL,
    consent_status TEXT DEFAULT 'implicit',
    effective_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expiration_time TIMESTAMP WITH TIME ZONE
);

-- 8. memory_facts
CREATE TABLE IF NOT EXISTS memory_facts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    fact TEXT NOT NULL,
    category TEXT,
    source TEXT,
    confidence REAL,
    expiry_time TIMESTAMP WITH TIME ZONE,
    visibility TEXT DEFAULT 'active',
    deletion_marker BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 9. permissions
CREATE TABLE IF NOT EXISTS permissions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    integration TEXT NOT NULL,
    scope TEXT NOT NULL,
    status TEXT DEFAULT 'granted' CHECK (status IN ('granted', 'revoked')),
    granted_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    revoked_time TIMESTAMP WITH TIME ZONE,
    token_reference TEXT
);

-- 10. safety_events
CREATE TABLE IF NOT EXISTS safety_events (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    label TEXT NOT NULL,
    reason_code TEXT,
    action TEXT NOT NULL,
    severity TEXT,
    model_version TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 11. audit_events
CREATE TABLE IF NOT EXISTS audit_events (
    id SERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    before_metadata TEXT,
    after_metadata TEXT,
    correlation_id TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
