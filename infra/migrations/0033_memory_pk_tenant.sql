-- 0033: scope madras_memory's primary key to (id, tenant). Several callers build
-- deterministic ids without tenant in scope (sleeptime distill's `lc-{agent}-{int(now)}`,
-- quick-add's content hash) -- under a bare `id` PK, two tenants producing the same id
-- silently dropped one write via `ON CONFLICT (id) DO NOTHING`, no error, no signal.
-- Safe to re-run: DROP+ADD under the same constraint name is idempotent, and since `id`
-- was already globally unique there are no (id, tenant) dupes to reconcile.
ALTER TABLE madras_memory DROP CONSTRAINT IF EXISTS madras_memory_pkey;
ALTER TABLE madras_memory ADD CONSTRAINT madras_memory_pkey PRIMARY KEY (id, tenant);
