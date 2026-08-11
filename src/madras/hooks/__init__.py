"""Lifecycle hooks (W4·B5) — deterministic, user-defined extensibility (Claude Code idiom).

Callable hooks register per lifecycle event in a HookRegistry; `dispatch` runs them. The
`pre_tool_use` event is the deterministic control point — a hook can BLOCK a tool call. The
Builder wires user-defined shell/HTTP hooks onto this registry via an adapter (later).
"""

from madras.hooks.models import BLOCKING_EVENTS, HOOK_EVENTS, HookResult
from madras.hooks.registry import HookRegistry

__all__ = ["BLOCKING_EVENTS", "HOOK_EVENTS", "HookRegistry", "HookResult"]
