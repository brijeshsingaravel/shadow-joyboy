-- Migration 0002: Claude-Code-style per-project tool permission store
-- Applied: 2026-06-13

CREATE TABLE IF NOT EXISTS madras_tool_permissions (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    project     TEXT NOT NULL DEFAULT 'default',
    tool        TEXT NOT NULL,
    arg_pattern TEXT NOT NULL DEFAULT '*',
    decision    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_perm_project ON madras_tool_permissions(project);
