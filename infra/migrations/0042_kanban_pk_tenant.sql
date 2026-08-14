-- 0042: scope the kanban tables' uniqueness to (id, tenant).
--
-- SIXTH and SEVENTH occurrences of one defect shape, found while proving RLS isolation end to end:
--   madras_memory (0033) · Qdrant point id (s61) · madras_mindpalace_sessions (0034) ·
--   madras_canon (0039) · pg_sft_rows (0040) · madras_kanban_boards + _tasks (here).
--
-- Every one is a uniqueness key that omits the namespace and fails SILENTLY:
-- `ON CONFLICT (id) DO NOTHING` drops the second tenant's row with no error. Board and task ids
-- are caller-supplied here, so two tenants naming a board "sprint-1" collide by ordinary usage
-- rather than by coincidence.
--
-- ids were globally unique before, so there are no (id, tenant) duplicates to reconcile. The FK
-- from tasks to boards must be re-pointed at the new key: a composite FK is what keeps a task from
-- referencing another tenant's board even at the constraint level, not merely at the policy level.
ALTER TABLE madras_kanban_tasks DROP CONSTRAINT IF EXISTS madras_kanban_tasks_board_id_fkey;

ALTER TABLE madras_kanban_boards DROP CONSTRAINT IF EXISTS madras_kanban_boards_pkey;
ALTER TABLE madras_kanban_boards ADD CONSTRAINT madras_kanban_boards_pkey PRIMARY KEY (id, tenant);

ALTER TABLE madras_kanban_tasks DROP CONSTRAINT IF EXISTS madras_kanban_tasks_pkey;
ALTER TABLE madras_kanban_tasks ADD CONSTRAINT madras_kanban_tasks_pkey PRIMARY KEY (id, tenant);

ALTER TABLE madras_kanban_tasks
    ADD CONSTRAINT madras_kanban_tasks_board_fkey
    FOREIGN KEY (board_id, tenant) REFERENCES madras_kanban_boards (id, tenant) ON DELETE CASCADE;
