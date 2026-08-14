-- infra/migrations/0008_pg_economics.sql
CREATE TABLE IF NOT EXISTS pg_economics (
    economics_id   TEXT PRIMARY KEY,
    source_run_id  TEXT,
    ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
    report         JSONB NOT NULL DEFAULT '{}'::jsonb,
    user_mix       JSONB NOT NULL DEFAULT '{}'::jsonb,
    target_margins JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS pg_economics_ts_idx ON pg_economics (ts DESC);
