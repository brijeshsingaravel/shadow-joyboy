"""User-authored rails — declarative deterministic governance compiled into lifecycle hooks.

A non-dev author/user declares topical/safety rails as DATA (NeMo-style); `compile_rail`
turns each into a `Hook` callable registered into the built `HookRegistry`. Pure + deterministic
(no LLM): on a matching `pre_tool_use` a `deny` rail BLOCKS the call (its message is the
teach-back); on non-blocking events (and `warn` rails) it adds feedback. Completes
User-Authored Rails on top of the existing hooks subsystem — governance is code, not a prompt.

Note: the hook system only *blocks* on `pre_tool_use` (the deterministic control point), so a
`deny` on a non-blocking event (e.g. user_prompt_submit) degrades to a strong warning; prompt
blocking belongs to the input guardrail.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from typing import Any

from madras.hooks.models import BLOCKING_EVENTS, HOOK_EVENTS, HookResult
from madras.hooks.registry import Hook, HookRegistry

_TOOL_EVENTS = frozenset({"pre_tool_use", "post_tool_use", "subagent_start", "subagent_stop"})
_TEXT_KEYS = ("text", "prompt", "content", "message")


@dataclass
class Rail:
    event: str
    decision: str = "deny"  # deny (block on blocking events) | warn (feedback)
    tool: str = ""  # glob on the tool name (tool events only)
    contains: str = ""  # case-insensitive substring on args-json / prompt text
    pattern: str = ""  # regex on args-json / prompt text
    message: str = ""  # teach-back / feedback


def _haystack(event: str, payload: dict[str, Any]) -> str:
    if event in _TOOL_EVENTS:
        return json.dumps(payload.get("args", payload), default=str)
    for k in _TEXT_KEYS:
        if payload.get(k):
            return str(payload[k])
    return json.dumps(payload, default=str)


def _matches(rail: Rail, event: str, payload: dict[str, Any]) -> bool:
    # A rail with no condition matches nothing (never block everything by accident).
    if not (rail.tool or rail.contains or rail.pattern):
        return False
    if rail.tool and not fnmatch.fnmatch(str(payload.get("tool", "")), rail.tool):
        return False
    hay = _haystack(event, payload)
    if rail.contains and rail.contains.lower() not in hay.lower():
        return False
    if rail.pattern and not re.search(rail.pattern, hay):
        return False
    return True


def compile_rail(rail: Rail) -> Hook:
    """Turn a declarative Rail into a registry Hook."""

    async def _hook(event: str, payload: dict[str, Any]) -> HookResult | None:
        if not _matches(rail, event, payload):
            return None  # no opinion
        msg = rail.message or f"blocked by user rail on {event}"
        if rail.decision == "deny" and event in BLOCKING_EVENTS:
            return HookResult(allow=False, message=msg)
        return HookResult(allow=True, message=msg)  # warn / non-blocking feedback

    return _hook


def load_rails(data: list[dict[str, Any]]) -> list[Rail]:
    """Build Rails from author-supplied dicts (e.g. AgentConfig / a rails file). Validates."""
    rails: list[Rail] = []
    for d in data or []:
        event = str(d.get("event", "")).strip()
        if event not in HOOK_EVENTS:
            raise ValueError(f"unknown rail event {event!r}")
        decision = str(d.get("decision", "deny")).strip().lower()
        if decision not in ("deny", "warn"):
            raise ValueError(f"unknown rail decision {decision!r}")
        rails.append(
            Rail(
                event=event,
                decision=decision,
                tool=str(d.get("tool", "")),
                contains=str(d.get("contains", "")),
                pattern=str(d.get("pattern", "")),
                message=str(d.get("message", "")),
            )
        )
    return rails


def register_rails(registry: HookRegistry, rails: list[Rail]) -> int:
    """Compile + register rails into a HookRegistry. Returns the count registered."""
    for rail in rails:
        registry.register(rail.event, compile_rail(rail))
    return len(rails)
