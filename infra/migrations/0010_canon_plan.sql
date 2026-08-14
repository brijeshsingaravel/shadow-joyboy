-- Canon Plan — the durable, vision-anchored user plan (the planning moat).
-- One row per (project) canon; phases/tasks/pivots are nested JSONB; revisions
-- are an append-only history (git-for-vision). Mirrors madras_plans (0004).

CREATE TABLE IF NOT EXISTS madras_canon (
    id              BIGSERIAL PRIMARY KEY,
    plan_id         TEXT UNIQUE NOT NULL,
    project         TEXT NOT NULL DEFAULT 'default',
    vision          TEXT NOT NULL DEFAULT '',
    north_star      TEXT NOT NULL DEFAULT '',
    phases          JSONB NOT NULL DEFAULT '[]'::jsonb,
    pivots          JSONB NOT NULL DEFAULT '[]'::jsonb,
    version         INT  NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_madras_canon_project ON madras_canon (project);

-- Append-only revision history: every user edit + accepted agent proposal.
CREATE TABLE IF NOT EXISTS madras_canon_revisions (
    id          BIGSERIAL PRIMARY KEY,
    plan_id     TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    author      TEXT NOT NULL DEFAULT 'user',   -- user | agent
    op          TEXT NOT NULL,                  -- write | pivot | check | restructure
    summary     TEXT NOT NULL DEFAULT '',
    snapshot    JSONB                            -- full canon snapshot for diff/rollback
);

CREATE INDEX IF NOT EXISTS idx_madras_canon_rev_plan ON madras_canon_revisions (plan_id, ts DESC);
