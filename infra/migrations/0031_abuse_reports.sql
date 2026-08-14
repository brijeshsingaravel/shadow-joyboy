-- 0031_abuse_reports.sql — § E6/F: user abuse reports against marketplace agents.
-- Past a threshold of DISTINCT reporters, the agent is auto-quarantined (killswitch,
-- migration 0030) pending review. One report per (agent, reporter) — re-filing updates
-- the reason, it does not inflate the distinct-reporter count (anti-brigading is a
-- separate concern; distinct reporters is the honest signal).

CREATE TABLE IF NOT EXISTS madras_abuse_reports (
    id                BIGSERIAL PRIMARY KEY,
    agent_name        TEXT NOT NULL,
    reporter_user_id  TEXT NOT NULL,
    category          TEXT NOT NULL,
    detail            TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_name, reporter_user_id)
);
CREATE INDEX IF NOT EXISTS idx_abuse_reports_agent ON madras_abuse_reports (agent_name);
