-- Durable scheduler — agent-self-schedulable, governed, survives restart.
-- madras_schedules holds the recurring/once jobs; madras_schedule_runs is the append-only
-- run history (idempotency-keyed, so a duplicate tick can't double-fire).

CREATE TABLE IF NOT EXISTS madras_schedules (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL DEFAULT '',
    agent_name          TEXT NOT NULL DEFAULT 'shadow',
    kind                TEXT NOT NULL,                       -- once|delay|interval|daily
    run_at              DOUBLE PRECISION NOT NULL DEFAULT 0, -- once/delay fire time (epoch)
    every_secs          DOUBLE PRECISION NOT NULL DEFAULT 0, -- interval
    at_hour             INT NOT NULL DEFAULT 0,              -- daily
    at_minute           INT NOT NULL DEFAULT 0,
    tz                  TEXT NOT NULL DEFAULT 'UTC',         -- IANA id
    anchor              DOUBLE PRECISION NOT NULL DEFAULT 0,
    misfire_grace_secs  DOUBLE PRECISION NOT NULL DEFAULT 3600,
    status              TEXT NOT NULL DEFAULT 'active',      -- active|paused|done|dead
    action              JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {type:'prompt', text:...}
    max_retries         INT NOT NULL DEFAULT 3,
    backoff_base_secs   DOUBLE PRECISION NOT NULL DEFAULT 30,
    last_run            DOUBLE PRECISION,                    -- epoch of last scheduled instant fired
    last_status         TEXT NOT NULL DEFAULT '',            -- ok|error|dead
    run_count           INT NOT NULL DEFAULT 0,
    fail_count          INT NOT NULL DEFAULT 0,
    created_at          DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_schedules_active ON madras_schedules (status) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_schedules_agent ON madras_schedules (agent_name);

CREATE TABLE IF NOT EXISTS madras_schedule_runs (
    id              BIGSERIAL PRIMARY KEY,
    schedule_id     TEXT NOT NULL,
    idempotency     TEXT NOT NULL,                           -- schedule_id:instant
    fired_at        DOUBLE PRECISION NOT NULL DEFAULT 0,
    ok              BOOLEAN NOT NULL DEFAULT FALSE,
    misfired        BOOLEAN NOT NULL DEFAULT FALSE,
    attempt         INT NOT NULL DEFAULT 1,
    error           TEXT NOT NULL DEFAULT '',
    UNIQUE (idempotency)                                     -- the dedup guarantee
);

CREATE INDEX IF NOT EXISTS idx_sched_runs_sid ON madras_schedule_runs (schedule_id, fired_at DESC);
