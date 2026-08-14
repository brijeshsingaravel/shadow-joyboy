-- 0020: biological-memory reinforcement (E-X4). Each recall strengthens an item so it
-- decays slower (Ebbinghaus spacing) and refreshes its recency anchor. Backward-compatible
-- defaults so existing rows behave exactly as before until first recalled.
ALTER TABLE madras_memory
  ADD COLUMN IF NOT EXISTS strength double precision NOT NULL DEFAULT 1.0,
  ADD COLUMN IF NOT EXISTS last_accessed double precision NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS recall_count integer NOT NULL DEFAULT 0;
