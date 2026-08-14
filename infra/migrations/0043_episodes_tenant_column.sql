-- 0043 — give `madras_episodes` a tenant column (s63).
--
-- THE NINTH TABLE. 0037's comment recorded that enabling RLS on all nine tenant tables at once
-- "would make any wiring gap look like an empty database", so they were staged. Eight landed
-- (canon, canon_revisions, kanban_boards, kanban_tasks, memory, memory_edges, mindpalace_briefings,
-- mindpalace_sessions). `madras_episodes` was left out for a concrete reason rather than an
-- oversight: **it had no tenant column to enforce a policy on.** This adds it.
--
-- WHY IT MATTERS BEYOND TIDINESS: episodes are the per-conversation summaries and decisions Shadow
-- writes. Without a tenant they cannot be answered for -- a user asking "what has Shadow recorded
-- about me?" (DPDP access) or "delete it" (DPDP erasure) cannot be served from a table whose rows
-- belong to no one. The column is what makes those questions answerable at all.
--
-- MEASURED BEFORE WRITTEN (s63): `SELECT count(*) FROM madras_episodes` = **0**. A real count, not
-- pg_stat_user_tables' n_live_tup estimate, which read 0 for every madras_ table on the freshly
-- restarted server and would have "confirmed" an empty database either way. So there is nothing to
-- backfill and no orphan rows to adjudicate -- the expensive half of this change does not exist.

ALTER TABLE madras_episodes ADD COLUMN IF NOT EXISTS tenant TEXT;

-- Named `tenant`, matching madras_memory / madras_canon / the kanban tables and the
-- `current_setting('madras.tenant')` the policies read. A `tenant_id` here would be the one table
-- spelled differently -- exactly the hand-maintained inconsistency that is this project's most
-- expensive recurring defect.
--
-- **WHAT GOES IN IT: a stable account ID, never a display name.** A name is mutable; the moment
-- someone changes what they are called, every row keyed on the old value belongs to nobody -- the
-- episodes still exist and are permanently unreachable, including by the person entitled to delete
-- them. The ID is the identity; the display name lives on the account record and stays free to
-- change. The 8 pre-existing `tenant = 'default'` rows in madras_memory are a dev placeholder and
-- are NOT the pattern to copy for real users.

-- **COLUMN ONLY. NO POLICY, DELIBERATELY.**
-- Enabling RLS here now would be harmful in the fail-closed direction: `AgentState` carries no
-- tenant, so the two writers (graph/compaction.py, graph/tool_loop.py) have nothing to bind, and
-- WITH CHECK would REFUSE every new episode. Not an error any caller sees -- the note simply would
-- not be saved, and episodic memory would go quietly empty. The published retrofit guidance says
-- enable + policy must land in ONE migration (RLS with no policy is deny-by-default); that is
-- satisfied by enabling NEITHER here. Order: column -> thread the tenant through the runtime ->
-- THEN enable + policy together. Each step is safe to stop at.
--
-- NULLABLE for the same reason: NOT NULL would reject writes from the not-yet-threaded runtime.
-- Tightening to NOT NULL belongs with the policy migration, once nothing can write a nameless
-- episode any more.

-- Reads that serve a user are per-tenant and recent-first. The existing indexes lead with
-- agent_name / tags / ts -- none with tenant -- and RLS rewrites every query to add the tenant
-- predicate, so without a tenant-leading index the policy turns these into sequential scans. This
-- is the composite index the retrofit guidance calls for, added WITH the column rather than after
-- the first slow query.
CREATE INDEX IF NOT EXISTS idx_episodes_tenant_ts ON madras_episodes(tenant, ts DESC);
