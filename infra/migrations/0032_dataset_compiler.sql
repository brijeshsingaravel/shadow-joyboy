-- 0032: the Dataset Compiler's SFT output (Track 4, T4.1 -- hardening-eval-lab-handoff.md).
-- One row per mined/synthesized training example, from either producer (Synthetic-Data-Kit over
-- the Capability Catalog, or the Distilabel Teacher Council over dev-split Proving Ground cases).
-- G3 (D41): every row carries tenant + consent + provenance -- launch-gating, not deferred.
-- G4 (D41): rows are sourced from dev-split cases only; never the held-out firewall.

CREATE TABLE IF NOT EXISTS pg_sft_rows (
    id           TEXT PRIMARY KEY,
    tenant       TEXT NOT NULL DEFAULT 'default',
    consent      BOOLEAN NOT NULL DEFAULT true,
    producer     TEXT NOT NULL,              -- 'synthetic-data-kit' | 'distilabel-teacher-council'
    source_id    TEXT,                       -- capability id (1a) or case/scenario id (1b)
    prompt       TEXT NOT NULL,
    completion   TEXT NOT NULL,
    score        DOUBLE PRECISION,
    provenance   JSONB NOT NULL DEFAULT '{}'::jsonb,   -- teacher model, mining_run_id, timestamp, ...
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS pg_sft_rows_producer_idx ON pg_sft_rows (producer);
CREATE INDEX IF NOT EXISTS pg_sft_rows_tenant_idx ON pg_sft_rows (tenant);
