-- Messaging — unified multi-channel message log (governed send/receive + E4 continuity).
-- One row per inbound/outbound message; outbound defaults to draft->approve->sent.
-- A partial UNIQUE on (dedupe) WHERE status='sent' makes SEND idempotent (a retry of an
-- already-sent message can't double-send) while still allowing intentional repeats as drafts.

CREATE TABLE IF NOT EXISTS madras_messages (
    id           TEXT PRIMARY KEY,
    channel      TEXT NOT NULL,
    direction    TEXT NOT NULL,                       -- inbound | outbound
    "to"         TEXT NOT NULL DEFAULT '',
    sender       TEXT NOT NULL DEFAULT '',
    subject      TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',
    thread_id    TEXT NOT NULL DEFAULT '',
    session_id   TEXT NOT NULL DEFAULT '',            -- links to the session ledger (E4)
    status       TEXT NOT NULL DEFAULT 'draft',
    provenance   TEXT NOT NULL DEFAULT '',
    dedupe       TEXT NOT NULL DEFAULT '',
    agent_name   TEXT NOT NULL DEFAULT 'shadow',
    created_at   DOUBLE PRECISION NOT NULL DEFAULT 0,
    extras       JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_msg_session ON madras_messages (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_msg_pending ON madras_messages (status) WHERE status='pending_approval';
CREATE INDEX IF NOT EXISTS idx_msg_thread ON madras_messages (thread_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_msg_sent_dedupe
    ON madras_messages (dedupe) WHERE status='sent' AND dedupe <> '';
