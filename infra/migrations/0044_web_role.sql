-- 0044: give the WEBSITE its own limited database role (s68).
--
-- WHY THIS EXISTS AS A MIGRATION AND NOT A HAND-RUN COMMAND. `madras_web` already existed on
-- the founder's laptop, created by hand, referenced by no migration. So a rebuild from
-- migrations would not reproduce it, and -- worse -- the s68 schema audit compared the two
-- databases and reported them identical, because a role and a schema that no migration creates
-- are invisible to a diff of what migrations produce. That blind spot is how base-01 ran for
-- weeks with NO customer_auth schema at all: every sign-up and sign-in returned
-- `relation "user" does not exist`, and nobody could have created an account. Found the night
-- before the first invitations went out.
--
-- 0035 did this for the engine (`madras_app`). This is the same shape for the site, and the two
-- are deliberately separate roles: the website never touches `madras_*` data, and Shadow never
-- touches anyone's password hash. Neither can do the other's job even by accident.
--
-- SCOPE, taken from what the working local role actually has rather than from what seemed
-- reasonable: USAGE on the schemas, DML on the eight auth tables, and NOTHING on `public` --
-- verified as 0 grants across all 45 public tables while the site works normally. NOBYPASSRLS
-- so it can never read around a policy, though it has no reason to reach an RLS'd table at all.
--
-- NO CREATE. Consequence to remember: `better-auth migrate` needs DDL, so schema changes to the
-- auth tables are run as the ADMIN role, deliberately, the way 0044's own prerequisite was.
-- A limited runtime role that cannot migrate itself is the point, not an oversight.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'madras_web') THEN
        CREATE ROLE madras_web WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;

-- The schema must exist before this runs. On a fresh database that means the better-auth
-- migration has already been applied; `CREATE SCHEMA IF NOT EXISTS` here keeps the migration
-- runnable in either order rather than failing on a schema someone has not made yet.
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS customer_auth;

GRANT CONNECT ON DATABASE madras TO madras_web;

-- USAGE only -- never CREATE. `public` is granted USAGE for type and extension resolution and
-- gets no table privileges: the site has no business reading a person's memories.
GRANT USAGE ON SCHEMA public, auth, customer_auth TO madras_web;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auth, customer_auth TO madras_web;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA auth, customer_auth TO madras_web;

-- So a future better-auth table is usable without a follow-up grant nobody remembers to write.
ALTER DEFAULT PRIVILEGES IN SCHEMA auth, customer_auth
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO madras_web;
ALTER DEFAULT PRIVILEGES IN SCHEMA auth, customer_auth
    GRANT USAGE, SELECT ON SEQUENCES TO madras_web;
