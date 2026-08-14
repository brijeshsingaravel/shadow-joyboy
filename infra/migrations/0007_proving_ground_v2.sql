-- infra/migrations/0007_proving_ground_v2.sql
-- Proving Ground v2 — normalized store (spec §5). v1 (madras_pg_*) is superseded,
-- not deleted; v2 uses the bare pg_* names below. madras_pg_backlog is NOT
-- redefined here (it already exists from 0005).

CREATE TABLE IF NOT EXISTS pg_suites (
    suite_id   TEXT PRIMARY KEY,
    name       TEXT,
    version    TEXT,
    kind       TEXT,
    provenance TEXT,
    features   JSONB NOT NULL DEFAULT '[]'::jsonb,
    tools      JSONB NOT NULL DEFAULT '[]'::jsonb,
    n_cases    INTEGER
);

CREATE TABLE IF NOT EXISTS pg_runs (
    run_id             TEXT PRIMARY KEY,
    ts                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    head_sha           TEXT,
    seed               TEXT,
    models             JSONB NOT NULL DEFAULT '[]'::jsonb,
    suites             JSONB NOT NULL DEFAULT '[]'::jsonb,
    composite_by_model JSONB NOT NULL DEFAULT '{}'::jsonb,
    leaderboard        JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS pg_model_runs (
    run_id                 TEXT NOT NULL REFERENCES pg_runs(run_id) ON DELETE CASCADE,
    model                  TEXT NOT NULL,
    overall                DOUBLE PRECISION,
    pass_k                 DOUBLE PRECISION,
    composite              DOUBLE PRECISION,
    per_feature            JSONB NOT NULL DEFAULT '{}'::jsonb,
    per_benchmark          JSONB NOT NULL DEFAULT '{}'::jsonb,
    per_metric             JSONB NOT NULL DEFAULT '{}'::jsonb,
    cost_usd               DOUBLE PRECISION,
    latency_ms             DOUBLE PRECISION,
    safety_completion_rate DOUBLE PRECISION,
    PRIMARY KEY (run_id, model)
);

CREATE TABLE IF NOT EXISTS pg_scenario_results (
    run_id           TEXT NOT NULL REFERENCES pg_runs(run_id) ON DELETE CASCADE,
    model            TEXT NOT NULL,
    scenario_id      TEXT NOT NULL,
    suite_id         TEXT,
    benchmark_family TEXT,
    features         JSONB NOT NULL DEFAULT '[]'::jsonb,
    k                INTEGER,
    passes           INTEGER,
    pass_rate        DOUBLE PRECISION,
    det              JSONB NOT NULL DEFAULT '[]'::jsonb,
    judge_pass       BOOLEAN,
    verdict          TEXT,
    n_steps          INTEGER,
    tool_error_rate  DOUBLE PRECISION,
    latency_ms       DOUBLE PRECISION,
    tokens           INTEGER,
    PRIMARY KEY (run_id, model, scenario_id)
);

CREATE TABLE IF NOT EXISTS pg_tool_calls (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES pg_runs(run_id) ON DELETE CASCADE,
    model       TEXT,
    scenario_id TEXT,
    resample    INTEGER,
    seq         INTEGER,
    tool        TEXT,
    args        JSONB NOT NULL DEFAULT '{}'::jsonb,
    ok          BOOLEAN,
    error       TEXT,
    governance  JSONB NOT NULL DEFAULT '{}'::jsonb,
    latency_ms  DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS pg_tool_calls_tool_idx ON pg_tool_calls (tool);
CREATE INDEX IF NOT EXISTS pg_tool_calls_run_scenario_idx ON pg_tool_calls (run_id, scenario_id);

CREATE TABLE IF NOT EXISTS pg_judge_votes (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES pg_runs(run_id) ON DELETE CASCADE,
    model       TEXT,
    scenario_id TEXT,
    judge_model TEXT,
    pass        BOOLEAN,
    score       DOUBLE PRECISION,
    reason      TEXT
);

CREATE TABLE IF NOT EXISTS pg_metrics (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES pg_runs(run_id) ON DELETE CASCADE,
    model       TEXT,
    scenario_id TEXT,
    suite_id    TEXT,
    feature     TEXT,
    tool        TEXT,
    metric      TEXT,
    value       DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS pg_metrics_run_model_metric_idx ON pg_metrics (run_id, model, metric);
CREATE INDEX IF NOT EXISTS pg_metrics_feature_idx ON pg_metrics (feature);
CREATE INDEX IF NOT EXISTS pg_metrics_tool_idx ON pg_metrics (tool);

CREATE TABLE IF NOT EXISTS pg_coverage (
    run_id      TEXT NOT NULL REFERENCES pg_runs(run_id) ON DELETE CASCADE,
    feature     TEXT,
    tool        TEXT,
    benchmark   TEXT,
    covered     BOOLEAN,
    n_scenarios INTEGER,
    evidence    JSONB NOT NULL DEFAULT '{}'::jsonb
);
