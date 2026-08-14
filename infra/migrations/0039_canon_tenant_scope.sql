-- 0039: scope madras_canon's uniqueness to (plan_id, tenant), then put both canon tables under RLS.
--
-- FOURTH OCCURRENCE of one defect shape, and the reason it is worth naming: a uniqueness key that
-- omits the namespace fails SILENTLY. madras_memory (0033), the Qdrant point id (s61),
-- madras_mindpalace_sessions (0034), and now `ON CONFLICT (plan_id)` here -- two tenants sharing a
-- plan_id would have overwritten each other with no error and no signal.
--
-- plan_id was globally unique before, so there are no (plan_id, tenant) duplicates to reconcile
-- and this cannot fail on existing data.
ALTER TABLE madras_canon DROP CONSTRAINT IF EXISTS madras_canon_plan_id_key;
DROP INDEX IF EXISTS madras_canon_plan_tenant_uq;
CREATE UNIQUE INDEX madras_canon_plan_tenant_uq ON madras_canon (plan_id, tenant);

-- Revisions are append-only (no ON CONFLICT), so they need no uniqueness change -- only the
-- policy, so a tenant cannot read another tenant's plan history.
ALTER TABLE madras_canon ENABLE ROW LEVEL SECURITY;
ALTER TABLE madras_canon FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON madras_canon;
CREATE POLICY tenant_isolation ON madras_canon
    USING (tenant = current_setting('madras.tenant', true))
    WITH CHECK (tenant = current_setting('madras.tenant', true));

ALTER TABLE madras_canon_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE madras_canon_revisions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON madras_canon_revisions;
CREATE POLICY tenant_isolation ON madras_canon_revisions
    USING (tenant = current_setting('madras.tenant', true))
    WITH CHECK (tenant = current_setting('madras.tenant', true));
