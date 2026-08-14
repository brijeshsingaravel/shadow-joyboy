-- Canon Plan, round 2 — the full Lighthouse mandate for the user's projects.
-- Adds: a launch-date driver (target_date) and an idea/defer registry (ideas).
-- Security risks + research nudges are DERIVED by the Analyst, not stored here.

ALTER TABLE madras_canon ADD COLUMN IF NOT EXISTS target_date TEXT NOT NULL DEFAULT '';
ALTER TABLE madras_canon ADD COLUMN IF NOT EXISTS ideas JSONB NOT NULL DEFAULT '[]'::jsonb;
