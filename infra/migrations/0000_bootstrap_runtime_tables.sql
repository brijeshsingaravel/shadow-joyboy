-- 0000_bootstrap_runtime_tables.sql -- make a clean database reachable from migrations ALONE.
--
-- WHY THIS EXISTS (found s59, provisioning base-01 from scratch)
-- --------------------------------------------------------------
-- Two tables were only ever created by APPLICATION code at runtime, never by a migration:
--
--     madras_audit_log   <- madras/audit/writer.py    (CREATE_TABLE_SQL)
--     madras_episodes    <- madras/memory/episodic.py (CREATE_TABLE_SQL)
--
-- That made the schema NON-REPRODUCIBLE from infra/migrations/: applying every migration to an
-- empty database failed, because 0017_audit_chain.sql ALTERs madras_audit_log -- a table nothing
-- had created yet. Worse, tests/conftest.py's session-scoped snapshot fixture queries the
-- LangGraph checkpoint tables at collection time, so on a fresh database EVERY test errored
-- (3,339 of them) before a single assertion ran.
--
-- None of this was visible on a long-lived dev machine, where those tables have existed for
-- weeks. It only surfaces the first time someone provisions a clean environment -- i.e. exactly
-- when it is most expensive to discover.
--
-- NUMBERED 0000 DELIBERATELY: migrations are applied in filename order, so this MUST sort before
-- 0017_audit_chain.sql (and before anything else that assumes these tables exist). Renumbering
-- or editing already-applied migrations would be worse -- this only adds a new first step.
--
-- IDEMPOTENT BY CONSTRUCTION: every statement is IF NOT EXISTS, so this is a no-op on the
-- existing dev/production databases where the app already created these tables. Nothing is
-- dropped, altered, or migrated -- this only closes the cold-start gap.
--
-- DUPLICATION, ACKNOWLEDGED: these definitions intentionally mirror the two Python
-- CREATE_TABLE_SQL constants above. A .sql file cannot import Python, so the DDL is stated
-- twice. tests/test_infra/test_bootstrap_migration.py asserts the two stay in sync, so drift
-- fails a test rather than silently producing two different schemas.
--
-- NOT COVERED HERE (deliberately): the LangGraph checkpointer tables
-- (checkpoints/checkpoint_blobs/checkpoint_writes/checkpoint_migrations). Those are owned by
-- LangGraph, their schema changes between library versions, and they are created by
-- `AsyncPostgresSaver.setup()`. Hardcoding them here would fight the library on its next
-- upgrade. `scripts/bootstrap_db.py` calls that setup after applying migrations, which is the
-- correct owner boundary.

-- ---------------------------------------------------------------- audit (madras/audit/writer.py)
CREATE TABLE IF NOT EXISTS madras_audit_log (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_name   TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    action       TEXT NOT NULL,
    signals      JSONB NOT NULL,
    tool_calls   JSONB NOT NULL DEFAULT '[]'::jsonb,
    extras       JSONB NOT NULL DEFAULT '{}'::jsonb,
    prev_hash    TEXT NOT NULL DEFAULT '',
    record_hash  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_log_session ON madras_audit_log(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_agent ON madras_audit_log(agent_name);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON madras_audit_log(ts);
ALTER TABLE madras_audit_log ADD COLUMN IF NOT EXISTS prev_hash TEXT NOT NULL DEFAULT '';
ALTER TABLE madras_audit_log ADD COLUMN IF NOT EXISTS record_hash TEXT NOT NULL DEFAULT '';

-- ------------------------------------------------------- episodic (madras/memory/episodic.py)
CREATE TABLE IF NOT EXISTS madras_episodes (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_id  TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    decisions   JSONB NOT NULL DEFAULT '[]'::jsonb,
    tags        TEXT[] NOT NULL DEFAULT '{}'::text[],
    extras      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_episodes_agent ON madras_episodes(agent_name);
CREATE INDEX IF NOT EXISTS idx_episodes_tags ON madras_episodes USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_episodes_ts ON madras_episodes(ts DESC);
