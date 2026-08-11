"""Hook event taxonomy + result (W4·B5)."""

from __future__ import annotations

from dataclasses import dataclass

# Lifecycle cadences: once-per-session / once-per-turn / per-tool.
HOOK_EVENTS = (
    "session_start",
    "session_end",
    "user_prompt_submit",
    "stop",
    "pre_tool_use",
    "post_tool_use",
    "subagent_start",
    "subagent_stop",
)

# Only these may BLOCK (deterministic control point — like Claude Code's PreToolUse).
BLOCKING_EVENTS = frozenset({"pre_tool_use"})


@dataclass
class HookResult:
    allow: bool = True  # honored only for BLOCKING_EVENTS
    message: str = ""  # teach-back (on block) or feedback (post events)
