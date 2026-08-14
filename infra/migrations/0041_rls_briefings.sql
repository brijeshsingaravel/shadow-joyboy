-- 0041: RLS on madras_mindpalace_briefings (D83 step 7, the eighth and last table wired).
--
-- BriefingStore has no pool of its own -- it borrows MindPalaceLedger's, which binds
-- `madras.tenant` per acquire, so reads are scoped for free once the policy exists.
--
-- Its INSERT was the part that needed changing first (s61): it never named the `tenant` column,
-- which defaults to 'default'. From a ledger on any OTHER tenant that row would have been REJECTED
-- by WITH CHECK -- correctly, but confusingly, since nothing in briefing.py mentioned tenancy. The
-- value now comes from the ledger the pool belongs to, so the row written and the policy checking
-- it agree by construction rather than by coincidence.
ALTER TABLE madras_mindpalace_briefings ENABLE ROW LEVEL SECURITY;
ALTER TABLE madras_mindpalace_briefings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON madras_mindpalace_briefings;
CREATE POLICY tenant_isolation ON madras_mindpalace_briefings
    USING (tenant = current_setting('madras.tenant', true))
    WITH CHECK (tenant = current_setting('madras.tenant', true));
