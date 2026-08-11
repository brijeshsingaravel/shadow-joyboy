"""Customer-facing endpoint cases — every cockpit section the user touches.

Read-mostly GETs that must return a sane shape. Mutating/streaming endpoints
(/v1/chat, /approve, /skills/approve) are exercised by the agent loop + approval
scenarios, not here. Keep these side-effect-free so the probe is safe to run anytime.
"""

from __future__ import annotations

from madras.eval_.proving_ground.cockpit_probe import EndpointCase

# Status sets are generous: a section is "wired + shaped" if it answers without a
# 5xx and (when 200) carries its expected top-level keys. Some routes legitimately
# 404/400 without seed data — accepted, since we're probing the surface, not data.
CUSTOMER_FACING_CASES: list[EndpointCase] = [
    EndpointCase("GET", "/healthz", expect_status=(200,), expect_keys=["status"], label="health"),
    EndpointCase("GET", "/config", expect_status=(200,), label="settings"),
    EndpointCase("GET", "/models", expect_status=(200,), label="model picker"),
    EndpointCase("GET", "/toolsets", expect_status=(200,), label="tools manager"),
    EndpointCase("GET", "/skills", expect_status=(200,), label="skills section"),
    EndpointCase("GET", "/sessions", expect_status=(200,), label="sessions rail"),
    EndpointCase("GET", "/briefing", expect_status=(200, 404), label="briefing"),
    EndpointCase("GET", "/canon", expect_status=(200, 404), label="canon/lighthouse"),
    EndpointCase("GET", "/canon/projects", expect_status=(200, 404), label="multi-project hub"),
    EndpointCase("GET", "/schedules", expect_status=(200,), label="schedules/cron deck"),
    EndpointCase("GET", "/automations", expect_status=(200,), label="automations"),
    EndpointCase("GET", "/messages", expect_status=(200,), label="messages/inbox"),
    EndpointCase("GET", "/usage", expect_status=(200,), label="usage"),
    EndpointCase("GET", "/marketplace", expect_status=(200, 404), label="marketplace"),
    EndpointCase("GET", "/workspace/tree", expect_status=(200,), label="workspace tree"),
    EndpointCase("GET", "/proving-ground/status", expect_status=(200,), label="PG status"),
    EndpointCase("GET", "/proving-ground/runs", expect_status=(200,), label="PG runs"),
    EndpointCase("GET", "/proving-ground/agents", expect_status=(200,), label="PG agents"),
    EndpointCase("GET", "/proving-ground/targets", expect_status=(200,), label="beat-ladder"),
    EndpointCase(
        "GET", "/proving-ground/coverage", expect_status=(200, 404), label="PG coverage matrix"
    ),
]
