-- 0035: create the non-superuser application role that RLS ([[D83]]) requires.
--
-- WHY THIS COMES BEFORE ANY POLICY. Measured at s61: the app connects as `madras`, which OWNS
-- every table AND is a SUPERUSER. `FORCE ROW LEVEL SECURITY` fixes the owner bypass; it does not
-- apply to superusers at all. So a policy written today would be COMPLETELY INERT while appearing
-- correct -- listed by \d, passing review, protecting nothing. Writing policies first would
-- deliver confidence without protection, which is worse than the honest gap we have now.
--
-- WHAT THIS MIGRATION DOES AND DELIBERATELY DOES NOT DO. It creates the role and grants it the
-- privileges it will need. It does NOT set a password (that is `scripts/link_app_role_secret.py`,
-- which generates one and writes it straight to the vault without displaying it -- CLAUDE.md:
-- secrets live in vault.env and nowhere else), and it does NOT switch the application over. This
-- migration is therefore ADDITIVE AND INERT: nothing connects as this role until the connection
-- string changes, so applying it cannot break a running system. The switch is a separate,
-- reversible step.
--
-- NOLOGIN until the password is set, so the role cannot be used before it is credentialed.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'madras_app') THEN
        -- No SUPERUSER, no CREATEDB, no CREATEROLE, no BYPASSRLS: every one of those would
        -- reintroduce the exact bypass this role exists to remove.
        CREATE ROLE madras_app WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE madras TO madras_app;
GRANT USAGE ON SCHEMA public TO madras_app;

-- DML only. No DDL: the app reads and writes rows, it does not alter the schema -- migrations do,
-- and they keep running as the owner. A role that cannot ALTER TABLE also cannot quietly disable
-- a policy on it.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO madras_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO madras_app;

-- Future tables too. Without this, table 46 is created by a migration and is silently unreadable
-- by the app -- a failure that would appear as an empty result, not an error, which is the same
-- silent shape this whole line of work is removing.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO madras_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO madras_app;
