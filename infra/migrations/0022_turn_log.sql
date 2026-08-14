-- W1·c (3a) — per-turn session log: the detailed, tagged turn-level record that the Mind
-- Palace session-summary (madras_mindpalace_sessions, one row/session) lacks. One row PER
-- TURN: intent, the exchange, tool-calls, files, tags + an FTS vector for turn-level recall
-- (3b). Raw material the nightly Memory Manager distils into atomic Fabric memories (3c).
CREATE TABLE IF NOT EXISTS madras_turn_log (
    id             BIGSERIAL PRIMARY KEY,
    session_id     TEXT NOT NULL,
    agent_name     TEXT NOT NULL DEFAULT 'shadow',
    project        TEXT NOT NULL DEFAULT 'default',
    turn_idx       INT  NOT NULL,
    ts             DOUBLE PRECISION NOT NULL,        -- epoch seconds (passed in; no wall clock)
    user_text      TEXT NOT NULL DEFAULT '',
    assistant_text TEXT NOT NULL DEFAULT '',
    intent         TEXT NOT NULL DEFAULT '',
    tools_called   TEXT[] NOT NULL DEFAULT '{}',
    files_touched  TEXT[] NOT NULL DEFAULT '{}',
    tags           TEXT[] NOT NULL DEFAULT '{}',
    cost_usd       DOUBLE PRECISION NOT NULL DEFAULT 0,
    confidence     DOUBLE PRECISION NOT NULL DEFAULT 0,
    consolidated   BOOLEAN NOT NULL DEFAULT FALSE,   -- set once distilled into the Fabric (3c)
    fts            tsvector,                         -- turn-level full-text recall (3b)
    UNIQUE (session_id, turn_idx)
);
CREATE INDEX IF NOT EXISTS idx_turn_log_session  ON madras_turn_log (session_id, turn_idx);
CREATE INDEX IF NOT EXISTS idx_turn_log_agent_ts ON madras_turn_log (agent_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_turn_log_fts      ON madras_turn_log USING GIN (fts);
CREATE INDEX IF NOT EXISTS idx_turn_log_unconsolidated
    ON madras_turn_log (agent_name, ts) WHERE consolidated = FALSE;
