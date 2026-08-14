-- 0034: give the Mind Palace a tenant concept.
--
-- `madras_memory` was scoped to (id, tenant) at 0033 for exactly this reason: two tenants
-- producing the same deterministic id silently dropped one write via ON CONFLICT, "no error, no
-- signal". The Mind Palace still had the un-scoped shape -- `ON CONFLICT (session_id)` -- so two
-- tenants sharing a session_id would overwrite each other the same silent way. Third occurrence
-- of this defect shape in the codebase (madras_memory 0033, the Qdrant point id s61, here).
--
-- The sharper hole this closes is a read, not a write: `mindpalace/session_search.py` hydrates
-- vector hits via `ledger.get(session_id=...)`, and with no tenant column there was NO predicate
-- available to scope it -- unlike the memory fabric, which re-fetches `WHERE ... tenant=$2` and
-- so contains cross-tenant ids at the SQL layer. Sessions had no such backstop.
--
-- DEFAULT 'default' matches MemoryFabric's own default, so existing rows land in the namespace
-- they already belonged to and single-tenant callers see no change. NOT NULL so a future write
-- cannot omit it and create an un-scoped row that every tenant's predicate misses.
--
-- Columns are added to all four Mind Palace tables, not only the one being wired today: an RLS
-- policy needs a column to key on, and adding it later per-table is a migration each time.

ALTER TABLE madras_mindpalace_sessions  ADD COLUMN IF NOT EXISTS tenant text NOT NULL DEFAULT 'default';
ALTER TABLE madras_mindpalace_briefings ADD COLUMN IF NOT EXISTS tenant text NOT NULL DEFAULT 'default';
ALTER TABLE madras_canon                ADD COLUMN IF NOT EXISTS tenant text NOT NULL DEFAULT 'default';
ALTER TABLE madras_canon_revisions      ADD COLUMN IF NOT EXISTS tenant text NOT NULL DEFAULT 'default';

-- Scope uniqueness to (session_id, tenant). `session_id` was globally unique before, so there are
-- no (session_id, tenant) duplicates to reconcile and this cannot fail on existing data.
-- The UPSERT's ON CONFLICT target must match a real unique constraint, hence a named one.
ALTER TABLE madras_mindpalace_sessions
    DROP CONSTRAINT IF EXISTS madras_mindpalace_sessions_session_id_key;
DROP INDEX IF EXISTS madras_mindpalace_sessions_session_tenant_uq;
CREATE UNIQUE INDEX madras_mindpalace_sessions_session_tenant_uq
    ON madras_mindpalace_sessions (session_id, tenant);

-- Reads filter by tenant on every path, so the predicate should be indexed alongside the
-- columns it is filtered with rather than scanned.
CREATE INDEX IF NOT EXISTS madras_mindpalace_sessions_tenant_project_idx
    ON madras_mindpalace_sessions (tenant, project, agent_name);
