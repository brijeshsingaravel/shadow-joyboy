-- B28 (row 28) — skill lifecycle curation telemetry.
-- Adds pin + last-used so the Curator can pin/archive/restore stale skills (never deletes).
ALTER TABLE madras_skills ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE madras_skills ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ;
