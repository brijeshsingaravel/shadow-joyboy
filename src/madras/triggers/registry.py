"""Webhook trigger registry — maps (channel, event_filter) → agent run.

A trigger says: "when an inbound webhook arrives on channel X matching filter Y,
launch an agent run with prompt Z." The registry holds triggers in memory (for
unit tests) or Postgres (for production). The inbound-to-trigger bridge
(see server/app.py integration) consults the registry after HMAC verification
and inserts a one-shot durable schedule via the existing SchedulerStore.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from madras.scheduler.schedule_math import Schedule
from madras.scheduler.store import SchedulerStore


@dataclass
class Trigger:
    """A single webhook trigger definition."""

    id: str
    channel: str  # "github", "slack", "generic", or "*" for any
    event_filter: dict[str, Any] = field(default_factory=dict)  # type: ignore[reportUnknownVariableType]
    prompt_template: str = ""
    enabled: bool = True
    session_id: str = ""
    created_at: float = field(default_factory=time.time)


def render_prompt(template: str, context: dict[str, Any]) -> str:
    """Render a prompt template by resolving {{key}} placeholders.

    Supports nested access via dot notation: {{body.pull_request.title}}.
    Unresolved keys are left as-is (no crash on missing data).
    """

    def _resolve(match: re.Match[str]) -> str:  # type: ignore[type-arg]
        key = match.group(1).strip()
        parts = key.split(".")
        val: Any = context
        for part in parts:
            if isinstance(val, dict):
                resolved = val.get(part, match.group(0))  # type: ignore[reportUnknownMemberType]
                val = resolved  # type: ignore[reportUnknownVariableType]
            else:
                return match.group(0)
        return str(val) if val is not None else match.group(0)  # type: ignore[reportUnknownArgumentType]

    return re.sub(r"\{\{(.+?)\}\}", _resolve, template)


def match_trigger(trigger: Trigger, channel: str, payload: dict[str, Any]) -> bool:
    """Check if an inbound event matches this trigger.

    Returns False if the trigger is disabled, channel doesn't match,
    or event_filter doesn't match.
    """
    if not trigger.enabled:
        return False
    if trigger.channel != "*" and trigger.channel != channel:
        return False
    for key, expected in trigger.event_filter.items():
        actual = payload.get(key)
        if actual != expected:
            return False
    return True


class TriggerRegistry:
    """In-memory trigger store (swap to Postgres for production)."""

    def __init__(self) -> None:
        self._triggers: dict[str, Trigger] = {}

    async def add(self, trigger: Trigger) -> None:
        self._triggers[trigger.id] = trigger

    async def remove(self, trigger_id: str) -> bool:
        return self._triggers.pop(trigger_id, None) is not None

    async def get(self, trigger_id: str) -> Trigger | None:
        return self._triggers.get(trigger_id)

    async def list_all(self) -> list[Trigger]:
        return list(self._triggers.values())

    async def match(self, channel: str, payload: dict[str, Any]) -> list[Trigger]:
        """Return all triggers that match the given channel + payload."""
        return [t for t in self._triggers.values() if match_trigger(t, channel, payload)]


# ── Bridge: inbound webhook → trigger → one-shot schedule ───────────────────


async def fire_triggers(
    channel: str,
    payload: dict[str, Any],
    *,
    registry: TriggerRegistry,
    scheduler_store: SchedulerStore,
    agent_name: str = "shadow",
) -> list[str]:
    """Consult the trigger registry and insert one-shot schedules for matches.

    Returns the list of schedule IDs created (empty if no triggers matched).
    Each matched trigger produces a one-shot schedule that runs immediately
    via the existing SchedulerStore + tick loop — no new execution path.
    """
    triggers = await registry.match(channel, payload)
    created: list[str] = []

    for trigger in triggers:
        prompt = render_prompt(
            trigger.prompt_template,
            {
                "body": payload,
                "sender": payload.get("from", payload.get("sender", "external")),
                "channel": channel,
            },
        )
        schedule_id = f"trigger-{trigger.id}-{uuid.uuid4().hex[:8]}"
        schedule = Schedule(
            id=schedule_id,
            kind="once",
            run_at=time.time(),
            tz="UTC",
        )
        action = {"type": "prompt", "text": prompt}
        await scheduler_store.upsert(
            schedule,
            name=f"webhook:{trigger.id}",
            action=action,
            max_retries=2,
            created_at=time.time(),
        )
        created.append(schedule_id)

    return created
