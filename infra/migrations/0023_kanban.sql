-- 0023: durable multi-agent kanban board (D1.9 — Multi-Agent Collaboration).
-- The durable, async, cross-turn counterpart to the in-memory delegate_team:
-- multiple governed workers collaborate on a shared goal via a board they claim
-- tasks from. board = hard isolation; tenant = soft namespace; failure-limit auto-block.
-- Applied idempotently by KanbanStore.setup() (Phase 0-2 convention).

CREATE TABLE IF NOT EXISTS madras_kanban_boards (
  id          TEXT PRIMARY KEY,
  tenant      TEXT NOT NULL DEFAULT 'default',
  goal        TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'open',          -- open | done
  created_at  DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS madras_kanban_tasks (
  id            TEXT PRIMARY KEY,
  board_id      TEXT NOT NULL REFERENCES madras_kanban_boards(id) ON DELETE CASCADE,
  tenant        TEXT NOT NULL DEFAULT 'default',
  title         TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'general',
  status        TEXT NOT NULL DEFAULT 'ready',        -- ready | claimed | done | blocked
  claimed_by    TEXT,
  attempts      INT  NOT NULL DEFAULT 0,
  failure_limit INT  NOT NULL DEFAULT 2,
  result        TEXT,
  error         TEXT,
  created_at    DOUBLE PRECISION NOT NULL DEFAULT 0,
  updated_at    DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_kanban_tasks_board_status
  ON madras_kanban_tasks (board_id, status);
