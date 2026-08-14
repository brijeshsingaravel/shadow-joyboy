-- Cloud/async dispatch-and-return (D1.11 follow-up, cloud-async-execution capability).
-- Durable record of a background command dispatched to a remote E2B sandbox, so the
-- agent can disconnect and reconnect later (a new session, a new process) to check on
-- it. sandbox_id + pid are the E2B-side reconnect handle; job_id is our own durable key.

CREATE TABLE IF NOT EXISTS madras_background_jobs (
    job_id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    session_id TEXT NOT NULL,
    sandbox_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    cmd TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',  -- running | done | error
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS madras_background_jobs_agent_idx
    ON madras_background_jobs (agent_name, created_at DESC);
