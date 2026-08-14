-- Plan ledger table (Phase 2 / Track 2).
-- Durable cross-session plan ledger: one row per plan, items as JSONB list.
-- The structured "intent" spine that a later Memory-Manager step reconciles
-- against the raw session log. Mirrors madras_mindpalace_sessions conventions.

CREATE TABLE IF NOT EXISTS madras_plans (
    id              BIGSERIAL PRIMARY KEY,
    plan_id         TEXT UNIQUE NOT NULL,
    project         TEXT NOT NULL DEFAULT 'default',
    agent_name      TEXT NOT NULL,
    title           TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'user',
    seq             INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open',
    started_session TEXT,
    items           JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plans_project_agent_status
    ON madras_plans(project, agent_name, status);
CREATE INDEX IF NOT EXISTS idx_plans_project_agent_seq
    ON madras_plans(project, agent_name, seq);
