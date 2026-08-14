-- Human labels for judge meta-evaluation (validate-the-validator). A small set of
-- human pass/fail verdicts per (run, scenario) that we measure the panel against.

CREATE TABLE IF NOT EXISTS pg_human_labels (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL DEFAULT '',
    scenario_id TEXT NOT NULL,
    agent       TEXT NOT NULL DEFAULT 'shadow',
    model       TEXT NOT NULL DEFAULT '',
    human_pass  BOOLEAN NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    ts          DOUBLE PRECISION NOT NULL DEFAULT 0,
    UNIQUE (run_id, scenario_id, agent, model)
);
CREATE INDEX IF NOT EXISTS idx_pg_human_labels_run ON pg_human_labels (run_id);
