-- Skills store (Phase 2 M2F). agentskills.io-compatible SKILL.md records.
-- Progressive disclosure: L0 = name+description, L1 = full body, L2 = references.

CREATE TABLE IF NOT EXISTS madras_skills (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    project       TEXT NOT NULL DEFAULT 'default',
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL DEFAULT '',
    toolsets      TEXT[] NOT NULL DEFAULT '{}'::text[],
    category      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'candidate',  -- candidate | active | rejected | archived
    provenance    JSONB NOT NULL DEFAULT '{}'::jsonb,
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    approved_at   TIMESTAMPTZ,
    UNIQUE (project, name)
);
CREATE INDEX IF NOT EXISTS idx_skills_project_status ON madras_skills(project, status);
