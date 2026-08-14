-- infra/migrations/0005_proving_ground.sql
CREATE TABLE IF NOT EXISTS madras_pg_runs (
    run_id        TEXT PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    head_sha      TEXT,
    agent_model   TEXT,
    judge_set     JSONB NOT NULL DEFAULT '[]'::jsonb,
    bank_version  TEXT,
    overall_score DOUBLE PRECISION NOT NULL,
    pass_k        DOUBLE PRECISION NOT NULL,
    per_feature   JSONB NOT NULL DEFAULT '{}'::jsonb,
    per_benchmark JSONB NOT NULL DEFAULT '{}'::jsonb,
    n_scenarios   INTEGER NOT NULL,
    deltas        JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS madras_pg_scenario_results (
    run_id          TEXT NOT NULL REFERENCES madras_pg_runs(run_id) ON DELETE CASCADE,
    scenario_id     TEXT NOT NULL,
    benchmark_family TEXT,
    features        JSONB NOT NULL DEFAULT '[]'::jsonb,
    k               INTEGER NOT NULL,
    passes          INTEGER NOT NULL,
    pass_rate       DOUBLE PRECISION NOT NULL,
    det_pass        BOOLEAN NOT NULL,
    judge_pass      BOOLEAN NOT NULL,
    deterministic   JSONB NOT NULL DEFAULT '[]'::jsonb,
    judge_votes     JSONB NOT NULL DEFAULT '[]'::jsonb,
    trajectory      JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (run_id, scenario_id)
);
CREATE TABLE IF NOT EXISTS madras_pg_backlog (
    id            BIGSERIAL PRIMARY KEY,
    opened_ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
    status        TEXT NOT NULL DEFAULT 'open',
    severity      TEXT,
    pattern       TEXT,
    evidence_run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    root_cause    TEXT,
    suggested_fix TEXT,
    track         TEXT,
    scope_flag    TEXT,
    closed_ts     TIMESTAMPTZ
);
