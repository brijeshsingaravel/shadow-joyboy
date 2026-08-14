-- MCP registry — declarative server catalog (kills config-hell) + the pinned tool manifest.
-- A server is quarantined if its tools are poisoned (scan) or its manifest drifts (rug-pull).

CREATE TABLE IF NOT EXISTS madras_mcp_servers (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    transport    TEXT NOT NULL DEFAULT 'stdio',          -- stdio | http
    command      TEXT NOT NULL DEFAULT '',
    args         JSONB NOT NULL DEFAULT '[]'::jsonb,
    url          TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',          -- active | paused | quarantined
    allowlisted  BOOLEAN NOT NULL DEFAULT FALSE,
    pinned_hash  TEXT NOT NULL DEFAULT '',
    agent_name   TEXT NOT NULL DEFAULT 'shadow',
    created_at   DOUBLE PRECISION NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_agent ON madras_mcp_servers (agent_name);

CREATE TABLE IF NOT EXISTS madras_mcp_tools (
    id           BIGSERIAL PRIMARY KEY,
    server_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    schema       JSONB NOT NULL DEFAULT '{}'::jsonb,
    flagged      BOOLEAN NOT NULL DEFAULT FALSE,          -- poisoning scan hit
    UNIQUE (server_id, name)
);

CREATE INDEX IF NOT EXISTS idx_mcp_tools_server ON madras_mcp_tools (server_id);
