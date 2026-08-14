-- 0036: Row-Level Security on madras_memory -- the first table under policy (D83 step 7).
--
-- WHY THIS IS SAFE TO APPLY NOW, AND WOULD NOT HAVE BEEN BEFORE:
--
--   * the app connects as `madras_app`, which is NOT a superuser and does NOT own this table
--     (0035 + the s61 cutover). A superuser bypasses every policy unconditionally and an owner
--     bypasses it unless FORCE is set, so applying this earlier would have produced policies that
--     `\d` lists, reviews pass, and nothing enforces;
--   * `MemoryFabric` now binds `madras.tenant` on every connection in its pool, so queries have a
--     tenant to match. Without that, RLS returns ZERO ROWS rather than an error -- a silent
--     blackout, which is the failure shape this whole line of work exists to remove.
--
-- ONE TABLE FIRST, DELIBERATELY. Eight more carry a `tenant` column; each needs its store wired
-- the same way before its policy lands. Enabling all nine at once would make any wiring gap look
-- like an empty database.

ALTER TABLE madras_memory ENABLE ROW LEVEL SECURITY;

-- FORCE, not just ENABLE: without it the TABLE OWNER is exempt, so migrations and any tooling
-- running as `madras` would silently see every tenant's rows while the app saw its own. The
-- owner exemption was measured at s61 -- it is real, not theoretical.
ALTER TABLE madras_memory FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON madras_memory;

-- USING governs which rows are VISIBLE (SELECT/UPDATE/DELETE).
-- WITH CHECK governs which rows may be WRITTEN (INSERT/UPDATE).
--
-- **Both are required, and omitting WITH CHECK is the classic mistake.** A policy with USING
-- alone isolates reads perfectly while leaving INSERT free to write a row carrying ANOTHER
-- tenant's id -- so the store could not read across the boundary but could still write across it.
-- The s61 one-table proof had exactly that shape and passed, which is why this is spelled out
-- here rather than assumed.
--
-- `current_setting(..., true)` is the missing_ok form: it returns NULL instead of raising when the
-- variable is unset. `tenant = NULL` is NULL, not true, so an unset tenant matches NOTHING. That
-- is fail-closed by construction -- a caller that forgets to bind a tenant sees an empty result,
-- never another tenant's rows.
CREATE POLICY tenant_isolation ON madras_memory
    USING (tenant = current_setting('madras.tenant', true))
    WITH CHECK (tenant = current_setting('madras.tenant', true));
