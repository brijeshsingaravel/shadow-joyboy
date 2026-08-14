-- 0037: RLS on madras_memory_edges (D83 step 7, table 2 of 9).
--
-- `RelationshipStore` binds `madras.tenant` per acquire, so queries have a tenant to match.
-- WITH CHECK as well as USING: USING alone isolates reads while leaving INSERT free to write a
-- row carrying another tenant's id -- readable isolation with a writable hole.
ALTER TABLE madras_memory_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE madras_memory_edges FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON madras_memory_edges;
CREATE POLICY tenant_isolation ON madras_memory_edges
    USING (tenant = current_setting('madras.tenant', true))
    WITH CHECK (tenant = current_setting('madras.tenant', true));
