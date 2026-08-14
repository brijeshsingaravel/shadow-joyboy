-- 0009 — Proving Ground agent dimension.
--
-- The unit-under-test becomes (agent, model), not just model. Every per-result
-- table gains an `agent` column and folds it into its primary key; the coverage
-- matrix additionally gains `model` so a cell is sliceable by (agent, model,
-- feature|tool, benchmark=use-case). Existing rows default to 'shadow' — the
-- historical implicit agent — so the migration is back-compatible.
--
-- Manual apply (Phase 0). DROP CONSTRAINT IF EXISTS guards re-runs.

-- Run-level: record which agents took part (alongside the existing `models`).
ALTER TABLE pg_runs ADD COLUMN IF NOT EXISTS agents JSONB NOT NULL DEFAULT '[]'::jsonb;

-- Per-model rollup → per (agent, model).
ALTER TABLE pg_model_runs ADD COLUMN IF NOT EXISTS agent TEXT NOT NULL DEFAULT 'shadow';
ALTER TABLE pg_model_runs DROP CONSTRAINT IF EXISTS pg_model_runs_pkey;
ALTER TABLE pg_model_runs ADD PRIMARY KEY (run_id, agent, model);

-- Per-scenario result → per (agent, model, scenario).
ALTER TABLE pg_scenario_results ADD COLUMN IF NOT EXISTS agent TEXT NOT NULL DEFAULT 'shadow';
ALTER TABLE pg_scenario_results DROP CONSTRAINT IF EXISTS pg_scenario_results_pkey;
ALTER TABLE pg_scenario_results ADD PRIMARY KEY (run_id, agent, model, scenario_id);

-- Lineage tables (BIGSERIAL PKs — only add the column).
ALTER TABLE pg_tool_calls ADD COLUMN IF NOT EXISTS agent TEXT NOT NULL DEFAULT 'shadow';
ALTER TABLE pg_judge_votes ADD COLUMN IF NOT EXISTS agent TEXT NOT NULL DEFAULT 'shadow';
ALTER TABLE pg_metrics ADD COLUMN IF NOT EXISTS agent TEXT NOT NULL DEFAULT 'shadow';
CREATE INDEX IF NOT EXISTS pg_metrics_run_agent_model_idx
    ON pg_metrics (run_id, agent, model, metric);

-- Coverage matrix → reflect (agent, model) as well as feature/tool/benchmark.
ALTER TABLE pg_coverage ADD COLUMN IF NOT EXISTS agent TEXT NOT NULL DEFAULT 'shadow';
ALTER TABLE pg_coverage ADD COLUMN IF NOT EXISTS model TEXT;
CREATE INDEX IF NOT EXISTS pg_coverage_run_agent_model_idx
    ON pg_coverage (run_id, agent, model);
