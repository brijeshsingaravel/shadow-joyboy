-- Tamper-evident audit log: each record commits to the previous record's hash.
-- The log was already append-only (no update/delete); the chain makes tampering or
-- deletion DETECTABLE — altering any historical row breaks every hash downstream.

ALTER TABLE madras_audit_log ADD COLUMN IF NOT EXISTS prev_hash   TEXT NOT NULL DEFAULT '';
ALTER TABLE madras_audit_log ADD COLUMN IF NOT EXISTS record_hash TEXT NOT NULL DEFAULT '';
