-- L6 Relationship layer — typed, temporal edges between entities/memories.
-- Graphiti-style: (src) --rel--> (dst). Edges are temporal (valid_until) like memories,
-- so a relationship can be retired without deletion. Multi-agent / Boardroom use.

CREATE TABLE IF NOT EXISTS madras_memory_edges (
    id           TEXT PRIMARY KEY,
    agent_name   TEXT NOT NULL DEFAULT 'shadow',
    tenant       TEXT NOT NULL DEFAULT 'default',
    src          TEXT NOT NULL,
    rel          TEXT NOT NULL,                       -- paired_with|deferred_to|contradicted|mentored|mentor_of|knows|works_with
    dst          TEXT NOT NULL,
    weight       REAL NOT NULL DEFAULT 1.0,
    source       TEXT NOT NULL DEFAULT '',            -- provenance
    created_at   DOUBLE PRECISION NOT NULL DEFAULT 0,
    valid_until  DOUBLE PRECISION                     -- NULL = still valid
);

CREATE INDEX IF NOT EXISTS idx_edges_src ON madras_memory_edges (agent_name, tenant, src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON madras_memory_edges (agent_name, tenant, dst);
CREATE INDEX IF NOT EXISTS idx_edges_current ON madras_memory_edges (valid_until) WHERE valid_until IS NULL;
