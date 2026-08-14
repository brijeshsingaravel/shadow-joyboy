-- 0040: scope pg_sft_rows' uniqueness to (id, tenant).
--
-- FIFTH occurrence of one defect shape, and the last one this sweep found:
--   madras_memory            (0033)
--   the Qdrant point id      (s61)
--   madras_mindpalace_sessions (0034)
--   madras_canon             (0039)
--   pg_sft_rows              (here)
-- Every one is a uniqueness key that omits the namespace, and every one fails SILENTLY --
-- `ON CONFLICT ... DO NOTHING` drops the second tenant's row with no error and no signal.
-- Producers derive row ids from content (a mining pass over the same source yields the same id
-- deliberately, for idempotence), so two tenants mining the same corpus collide by design.
--
-- ids were globally unique before, so there are no (id, tenant) duplicates to reconcile.
ALTER TABLE pg_sft_rows DROP CONSTRAINT IF EXISTS pg_sft_rows_pkey;
ALTER TABLE pg_sft_rows ADD CONSTRAINT pg_sft_rows_pkey PRIMARY KEY (id, tenant);

-- NO RLS POLICY HERE, DELIBERATELY -- and the reason is a design difference, not an oversight.
--
-- Every other table under policy is reached through a store bound to ONE tenant, which binds
-- `madras.tenant` per acquire. `ProvingGroundStoreV2` is deliberately tenant-AGNOSTIC: the tenant
-- travels on each ROW, and one `write_sft_rows` batch may legitimately carry several. A
-- per-connection policy cannot express that, and forcing one would be an architectural change to
-- the Dataset Compiler rather than the wiring change the other eight needed.
--
-- Left open with the reason recorded, not left open silently. Closing it properly means deciding
-- whether the compiler should write one tenant per batch -- a question for whoever owns G3's
-- per-row tenant/consent/provenance model, not a detail to settle inside a migration.
