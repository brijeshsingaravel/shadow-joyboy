-- § B8 Deploy/Channels/Integrations — per-agent channel connections + API keys.
CREATE TABLE IF NOT EXISTS madras_agent_channels (
    id          BIGSERIAL PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    channel     TEXT NOT NULL,
    target_url  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_name, channel)
);
CREATE TABLE IF NOT EXISTS madras_agent_api_keys (
    id          BIGSERIAL PRIMARY KEY,
    agent_name  TEXT NOT NULL UNIQUE,
    key_hash    TEXT NOT NULL,
    key_prefix  TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
