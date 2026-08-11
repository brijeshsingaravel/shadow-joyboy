"""Tool-state-aware compaction — preserve framework state across compaction (row 88, eve pattern).

When the harness compacts message history, two framework-owned bits of state get summarized away and
must be RE-APPLIED (eve's `preserveFrameworkStateOnCompaction`):
1. **read-before-write evidence** is reset (`clear_stamps`, row 80) — so a write after compaction
   RE-READS the file whose read evidence was dropped (no blind overwrite);
2. **the durable todo** is re-injected — so the model keeps its task list (the recitation pattern).
Pure; composes the Plan Ledger (`tools/builtin/plan.py`) + the row-80 read guard.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from madras.tools.file_access_context import clear_stamps

# items not yet finished — any of these keeps the todo worth re-injecting
_INCOMPLETE = frozenset({"pending", "in_progress", "blocked"})


def render_todo(items: Iterable[Any]) -> str | None:
    """Render the active plan's items as a compaction re-injection block (statuses included).
    Returns None when there are no items or every item is done."""
    items = list(items or [])
    if not items:
        return None
    incomplete = sum(1 for i in items if getattr(i, "status", "") in _INCOMPLETE)
    if incomplete == 0:
        return None
    lines = [f"  - [{getattr(i, 'status', '?')}] {getattr(i, 'text', '')}" for i in items]
    return (
        "(framework state preserved across compaction — your task list)\n"
        f"{incomplete} item(s) still open:\n" + "\n".join(lines)
    )


@dataclass
class CompactionPreservation:
    messages: list[str] = field(default_factory=list[str])  # to append to the compacted history
    read_evidence_reset: bool = False
    todo_reinjected: bool = False


def preserve_framework_state_on_compaction(
    items: Iterable[Any] | None = None,
    *,
    reset_reads: bool = True,
    clear_fn: Callable[[], None] = clear_stamps,
) -> CompactionPreservation:
    """After the harness compacts message history, re-apply framework-owned state: reset
    read-before-write evidence (so a post-compaction write re-reads) and re-inject the durable
    todo (so the model keeps its task list). Returns the messages to append."""
    result = CompactionPreservation()
    if reset_reads:
        clear_fn()
        result.read_evidence_reset = True
    msg = render_todo(items or [])
    if msg:
        result.messages.append(msg)
        result.todo_reinjected = True
    return result
