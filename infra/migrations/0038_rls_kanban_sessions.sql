-- 0038: RLS on the kanban tables and the Mind Palace session ledger (D83 step 7, tables 3-5 of 9).
--
-- `KanbanStore` and `MindPalaceLedger` both bind `madras.tenant` per acquire, so queries have a
-- tenant to match. WITH CHECK as well as USING everywhere: USING alone isolates reads while
-- leaving INSERT free to write another tenant's row.
--
-- madras_kanban_tasks references madras_kanban_boards ON DELETE CASCADE. RLS does not change
-- referential integrity -- the FK is still enforced by the owner-level constraint -- but a task
-- whose board belongs to another tenant is now unreachable rather than merely unlisted.

ALTER TABLE madras_kanban_boards ENABLE ROW LEVEL SECURITY;
ALTER TABLE madras_kanban_boards FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON madras_kanban_boards;
CREATE POLICY tenant_isolation ON madras_kanban_boards
    USING (tenant = current_setting('madras.tenant', true))
    WITH CHECK (tenant = current_setting('madras.tenant', true));

ALTER TABLE madras_kanban_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE madras_kanban_tasks FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON madras_kanban_tasks;
CREATE POLICY tenant_isolation ON madras_kanban_tasks
    USING (tenant = current_setting('madras.tenant', true))
    WITH CHECK (tenant = current_setting('madras.tenant', true));

ALTER TABLE madras_mindpalace_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE madras_mindpalace_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON madras_mindpalace_sessions;
CREATE POLICY tenant_isolation ON madras_mindpalace_sessions
    USING (tenant = current_setting('madras.tenant', true))
    WITH CHECK (tenant = current_setting('madras.tenant', true));
