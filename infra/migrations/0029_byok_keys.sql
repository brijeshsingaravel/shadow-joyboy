-- § B11 Settings — per-customer BYOK (bring-your-own-key) provider credentials.
-- The api_key is stored ENCRYPTED (Fernet, MADRAS_BYOK_ENCRYPTION_KEY) — never plaintext.
-- One key per user (a customer runs their own agents on one provider account in v1).
CREATE TABLE IF NOT EXISTS madras_byok_keys (
    id                BIGSERIAL PRIMARY KEY,
    user_id           TEXT NOT NULL UNIQUE,
    provider          TEXT NOT NULL,        -- display label, e.g. "openai" / "openrouter"
    base_url          TEXT NOT NULL,        -- OpenAI-compatible /v1 endpoint
    model             TEXT NOT NULL,        -- the model id to route to
    encrypted_key     TEXT NOT NULL,        -- Fernet-encrypted provider key
    key_hint          TEXT NOT NULL,        -- last 4 chars, for the customer to recognize it
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
