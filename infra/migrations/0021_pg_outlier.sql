-- 0021_pg_outlier.sql — W0·3: the outlier-metric spine.
-- Adds the compounding signature + the outlier verdict to the per-(agent,model) leaderboard row,
-- and the compounding signal to the climb history. The other outlier signals already exist:
-- madras_index/scaffold_lift/cost_of_pass/tokens_per_task/speed_tok_s (0018) + pass_k (0007).

ALTER TABLE pg_model_runs
    ADD COLUMN IF NOT EXISTS quality_lift double precision,
    ADD COLUMN IF NOT EXISTS cost_decay   double precision,
    ADD COLUMN IF NOT EXISTS compounding  double precision,
    ADD COLUMN IF NOT EXISTS is_outlier   boolean;

ALTER TABLE pg_climb
    ADD COLUMN IF NOT EXISTS compounding double precision;
