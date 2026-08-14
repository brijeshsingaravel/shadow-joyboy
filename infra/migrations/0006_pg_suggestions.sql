-- infra/migrations/0006_pg_suggestions.sql
ALTER TABLE madras_pg_runs
    ADD COLUMN IF NOT EXISTS suggestions JSONB NOT NULL DEFAULT '[]'::jsonb;
