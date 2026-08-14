-- Memory Fabric — the unified atomic-memory store across all 6 layers.
-- One row per ATOMIC memory (fact/preference/principle/relationship/semantic/episodic),
-- with first-class TEMPORAL fields so knowledge updates + contradictions are explicit
-- (valid_until/supersedes) — never a silent overwrite. Provenance on every row.

CREATE TABLE IF NOT EXISTS madras_memory (
    id           TEXT PRIMARY KEY,
    agent_name   TEXT NOT NULL DEFAULT 'shadow',
    tenant       TEXT NOT NULL DEFAULT 'default',     -- per-tenant isolation (ASI06)
    kind         TEXT NOT NULL,                        -- fact|preference|principle|relationship|semantic|episodic
    subject      TEXT NOT NULL DEFAULT '',             -- entity/topic (drives contradiction)
    content      TEXT NOT NULL,                        -- the atomic statement
    tags         TEXT[] NOT NULL DEFAULT '{}'::text[],
    confidence   REAL NOT NULL DEFAULT 1.0,
    source       TEXT NOT NULL DEFAULT '',             -- provenance
    session_id   TEXT NOT NULL DEFAULT '',
    created_at   DOUBLE PRECISION NOT NULL DEFAULT 0,  -- epoch seconds (caller-supplied)
    valid_from   DOUBLE PRECISION NOT NULL DEFAULT 0,
    valid_until  DOUBLE PRECISION,                     -- NULL = still valid
    supersedes   TEXT                                  -- id of the item this replaced
);

CREATE INDEX IF NOT EXISTS idx_memory_agent_tenant ON madras_memory (agent_name, tenant);
CREATE INDEX IF NOT EXISTS idx_memory_subject ON madras_memory (subject);
CREATE INDEX IF NOT EXISTS idx_memory_kind ON madras_memory (kind);
CREATE INDEX IF NOT EXISTS idx_memory_current ON madras_memory (valid_until) WHERE valid_until IS NULL;
CREATE INDEX IF NOT EXISTS idx_memory_tags ON madras_memory USING GIN (tags);
