"""3-tier prompt assembly (Hermes prompt_caching pattern).

Tiers by stability so prefix caching can hit the long-lived parts:
  STABLE   — identity / Constitution / persona / tool guidance (≈ the anchor)
  CONTEXT  — skills, retrieved notes/files, current task (changes slowly)
  VOLATILE — live memory, timestamp, recent compaction summary (every turn)

assemble() concatenates stable -> context -> volatile. `cache_breakpoints()`
returns the char offsets where an Anthropic-native transport would place
cache_control markers; on OpenAI-format backends it's currently informational.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from madras.factory.spawn import AgentRecord
from madras.persona.anchor import build_session_start_anchor


@dataclass
class PromptTiers:
    stable: str
    context: str = field(default="")
    volatile: str = field(default="")

    def assemble(self) -> str:
        parts = [self.stable.strip()]
        if self.context.strip():
            parts.append("# Working context\n" + self.context.strip())
        if self.volatile.strip():
            parts.append("# Now\n" + self.volatile.strip())
        return "\n\n".join(parts)

    def cache_breakpoints(self) -> list[int]:
        """Char offsets after STABLE (and after CONTEXT) — where prefix-cache
        markers go on a caching-capable transport. Informational on OpenAI format."""
        offsets: list[int] = [len(self.stable.strip())]
        if self.context.strip():
            # Offset = length of everything up to (but not including) the volatile section.
            # Split on the volatile heading to find where context ends.
            assembled = self.assemble()
            if self.volatile.strip():
                # Everything before "# Now\n"
                before_volatile = assembled.split("# Now\n")[0]
                offsets.append(len(before_volatile))
        return offsets


def render_plan_block(plan: Any) -> str:
    """One-line-per-item checklist for the volatile prompt tier. Returns '' if no plan."""
    if plan is None:
        return ""
    items: list[Any] = getattr(plan, "items", None) or []
    if not items:
        return ""
    lines = [f"# Plan: {getattr(plan, 'title', '')}"]
    for item in items:
        status = getattr(item, "status", "pending")
        text = getattr(item, "text", "")
        if status == "done":
            lines.append(f"- [x] {text}")
        elif status == "in_progress":
            lines.append(f"- [~] {text}")
        elif status == "blocked":
            lines.append(f"- [ ] {text} (blocked)")
        else:
            lines.append(f"- [ ] {text}")
    return "\n".join(lines)


def build_prompt_tiers(
    agent: AgentRecord | None,
    *,
    skills: list[str] | None = None,
    notes: list[str] | None = None,
    memory_summary: str | None = None,
    recalled_memories: list[str] | None = None,
    timestamp: str | None = None,
    project_rules: str | None = None,
    user_model: str | None = None,
) -> PromptTiers:
    stable = build_session_start_anchor(agent) if agent is not None else "You are a Madras agent."
    context_parts: list[str] = []
    if project_rules and project_rules.strip():
        # E-B5: user-authored, per-project rules — highest-priority steering, loaded
        # verbatim into the cacheable CONTEXT tier (generalizes Lighthouse's _DOC_FILES).
        context_parts.append(
            "# Project rules (user-authored — follow these)\n" + project_rules.strip()
        )
    if user_model and user_model.strip():
        # E-B7: the agent's evolving model of who it's working with (CONTEXT tier).
        context_parts.append(user_model.strip())
    if skills:
        context_parts.append("Available skills:\n" + "\n".join(f"- {s}" for s in skills))
    if notes:
        context_parts.append("Notes:\n" + "\n".join(f"- {n}" for n in notes))
    volatile_parts: list[str] = []
    if recalled_memories:
        # Durable memories relevant to this turn (fabric recall) — surfaced so the agent
        # acts on what it already knows about the user without being asked to recall.
        volatile_parts.append(
            "Relevant memory (durable, currently true):\n"
            + "\n".join(f"- {m}" for m in recalled_memories)
        )
    if memory_summary:
        volatile_parts.append("Recent context (compacted):\n" + memory_summary)
    if timestamp:
        volatile_parts.append(f"Current time: {timestamp}")
    return PromptTiers(
        stable=stable,
        context="\n\n".join(context_parts),
        volatile="\n\n".join(volatile_parts),
    )


def assemble_system_prompt(agent: AgentRecord | None, **kw: object) -> str:
    """Convenience: build tiers and assemble to a single system-prompt string."""
    return build_prompt_tiers(agent, **kw).assemble()  # type: ignore[arg-type]
