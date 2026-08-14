-- Mind Palace ledger tables (Phase 1).
-- Per-project (Phase 1 = single project "default"). Multi-project arrives Phase 4.

CREATE TABLE IF NOT EXISTS madras_mindpalace_sessions (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_id      TEXT UNIQUE NOT NULL,
    project         TEXT NOT NULL DEFAULT 'default',
    agent_name      TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    duration_secs   INTEGER,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(10, 6) NOT NULL DEFAULT 0,
    tools_used      JSONB NOT NULL DEFAULT '[]'::jsonb,
    decisions       JSONB NOT NULL DEFAULT '[]'::jsonb,
    files_touched   JSONB NOT NULL DEFAULT '[]'::jsonb,
    open_items      JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary         TEXT NOT NULL DEFAULT '',
    tags            TEXT[] NOT NULL DEFAULT '{}'::text[]
);

CREATE INDEX IF NOT EXISTS idx_mp_sessions_project ON madras_mindpalace_sessions(project);
CREATE INDEX IF NOT EXISTS idx_mp_sessions_agent ON madras_mindpalace_sessions(agent_name);
CREATE INDEX IF NOT EXISTS idx_mp_sessions_tags ON madras_mindpalace_sessions USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_mp_sessions_ts ON madras_mindpalace_sessions(ts DESC);
CREATE INDEX IF NOT EXISTS idx_mp_sessions_summary_fts
    ON madras_mindpalace_sessions USING GIN(to_tsvector('english', summary));

CREATE TABLE IF NOT EXISTS madras_mindpalace_briefings (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    project         TEXT NOT NULL DEFAULT 'default',
    agent_name      TEXT NOT NULL,
    target_date     DATE NOT NULL,
    briefing_text   TEXT NOT NULL,
    consumed        BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_mp_briefings_target
    ON madras_mindpalace_briefings(project, agent_name, target_date);
