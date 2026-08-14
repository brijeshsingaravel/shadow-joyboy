-- § B7 Workspace/Dashboard — links a compiled agent to the customer account that built
-- it. `user_id` is a plain TEXT reference to auth-customer.ts's `customer_auth.user.id`
-- (cross-schema, deliberately no FK -- same discipline as madras_audit_log's agent_name:
-- a loose reference, not a hard coupling between the engine's own tables and the web
-- layer's auth schema).
CREATE TABLE IF NOT EXISTS madras_agent_ownership (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, agent_name)
);
CREATE INDEX IF NOT EXISTS idx_agent_ownership_user ON madras_agent_ownership(user_id);
