-- 0030_agent_killswitch.sql — § E6/F trust & safety: the emergency killswitch.
-- A quarantine record halts an agent at the single invoke door (compiler/turn.py).
-- scope='agent'   -> target is an agent_name (stop one agent)
-- scope='creator' -> target is a user_id (stop ALL of a creator's agents, resolved
--                    via madras_agent_ownership) — the rogue-creator-at-scale case.
-- Reversible: lifting sets active=FALSE + lifted_at; rows are never deleted (forensics).

CREATE TABLE IF NOT EXISTS madras_agent_quarantine (
    id          BIGSERIAL PRIMARY KEY,
    scope       TEXT NOT NULL CHECK (scope IN ('agent', 'creator')),
    target      TEXT NOT NULL,
    reason      TEXT NOT NULL,
    by_actor    TEXT NOT NULL,           -- creator user_id | 'platform' | 'auto-abuse'
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lifted_at   TIMESTAMPTZ
);

-- Fast active-block lookup by target (agent_name or user_id).
CREATE INDEX IF NOT EXISTS idx_quarantine_active_target
    ON madras_agent_quarantine (target) WHERE active;

-- At most one ACTIVE quarantine per (scope, target) — re-flagging is idempotent.
CREATE UNIQUE INDEX IF NOT EXISTS uq_quarantine_active
    ON madras_agent_quarantine (scope, target) WHERE active;
