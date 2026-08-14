-- Proving Ground leaderboard: per-(agent,model) scaffold-lift + cost-of-pass + speed,
-- the tool-isolation + cockpit-readiness tracks, and a climb series for the
-- improvement engine. Additive — existing rows keep working (NULL = not-yet-computed).

ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS tier                  TEXT;
ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS madras_index          DOUBLE PRECISION;
ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS raw_index             DOUBLE PRECISION;
ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS scaffold_lift         DOUBLE PRECISION;
ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS cost_of_pass          DOUBLE PRECISION;
ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS tokens_per_task       DOUBLE PRECISION;
ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS speed_tok_s           DOUBLE PRECISION;
ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS tool_iso_pass_rate    DOUBLE PRECISION;
ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS endpoint_readiness_pct DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS pg_climb (
    id            BIGSERIAL PRIMARY KEY,
    run_id        TEXT NOT NULL,
    ts            DOUBLE PRECISION NOT NULL DEFAULT 0,
    agent         TEXT NOT NULL,
    model         TEXT NOT NULL,
    tier          TEXT NOT NULL DEFAULT '',
    madras_index  DOUBLE PRECISION,
    scaffold_lift DOUBLE PRECISION,
    cost_of_pass  DOUBLE PRECISION,
    head_sha      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pg_climb_am ON pg_climb (agent, model, ts);
