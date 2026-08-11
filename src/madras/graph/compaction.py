"""Conversation compaction (Hermes ContextCompressor pattern + Madras durable memory).

When messages exceed a token threshold, summarize the middle via an aux LLM call,
keep the last N turns intact, repair orphaned tool-call/result pairs, and persist
the summary as a provenanced episode so the compacted context stays recallable.

ASI02 compliance: the conversation transcript being summarized is wrapped in
<retrieved>...</retrieved> — it is DATA fed to the LLM, not trusted instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from madras.eval_.emitter import emit_action_signals
from madras.llm.gateway import LLMGateway, LLMRequest

# OpenAI-style message = {"role": str, "content": str, optional "tool_calls"/"tool_call_id"}
Message = dict[str, Any]


def estimate_tokens(messages: list[Message]) -> int:
    """Rough token estimate: ~4 chars/token over content + tool_calls."""
    chars = 0
    for m in messages:
        chars += len(str(m.get("content") or ""))
        tool_calls: list[Any] = m.get("tool_calls", []) or []
        for tc in tool_calls:
            chars += len(str(tc))
    return chars // 4


@dataclass
class CompactionResult:
    summary: str
    episode_id: int | None
    tokens_before: int
    tokens_after: int
    turns_compacted: int
    signals: dict[str, Any]


def _split_protect_tail(
    messages: list[Message], protect_last_n: int
) -> tuple[list[Message], list[Message]]:
    """Return (middle, tail).

    Tail = last protect_last_n messages, but never start the tail on an orphaned
    tool result (a {'role':'tool'} whose assistant tool_call is in the middle) —
    walk the boundary back until the tail begins cleanly.
    """
    if protect_last_n >= len(messages):
        return [], list(messages)
    cut = len(messages) - protect_last_n
    # Don't let the tail begin with a tool message orphaned from its assistant tool_call
    while 0 < cut < len(messages) and messages[cut].get("role") == "tool":
        cut -= 1
    return messages[:cut], messages[cut:]


async def maybe_compact(
    messages: list[Message],
    *,
    gateway: LLMGateway,
    model: str,
    session_id: str,
    agent_name: str = "shadow",
    threshold_tokens: int = 3000,
    protect_last_n: int = 6,
    episodic: Any = None,  # EpisodicMemory or duck-typed fake; None -> skip persist
    langfuse_trace_id: str | None = None,
    seed: int | None = None,  # eval-lab T2.10: reproducible sampling when set
) -> tuple[list[Message], CompactionResult | None]:
    """Compact a long conversation by summarizing the middle turns.

    Returns (original_messages, None) when below threshold.
    Returns (compacted_messages, CompactionResult) when compaction ran.

    The compacted list is: [summary_message] + tail (last protect_last_n messages).
    The summary is also written as a durable episode in EpisodicMemory (best-effort —
    a write failure never propagates to the caller).
    """
    before = estimate_tokens(messages)
    if before < threshold_tokens or len(messages) <= protect_last_n + 1:
        return messages, None

    middle, tail = _split_protect_tail(messages, protect_last_n)
    if not middle:
        return messages, None

    # Build transcript of the middle segment — DATA, not instruction (ASI02 fence)
    transcript = "\n".join(f"{m.get('role')}: {str(m.get('content') or '')[:1500]}" for m in middle)
    prompt = (
        "Summarize the following earlier conversation segment for an agent's memory. "
        "Preserve decisions, facts, named entities, numbers, and open items. Be concise.\n\n"
        f"<retrieved>\n{transcript}\n</retrieved>"
    )
    resp = await gateway.complete(
        LLMRequest(model=model, messages=[{"role": "user", "content": prompt}], seed=seed)
    )
    summary = resp.text.strip()

    compacted: list[Message] = [
        {"role": "user", "content": f"[Summary of earlier conversation]\n{summary}"},
        *tail,
    ]
    after = estimate_tokens(compacted)

    episode_id: int | None = None
    if episodic is not None:
        try:
            from madras.memory.episodic import Episode  # local import — avoids circular dep

            episode_id = await episodic.write(
                Episode(
                    session_id=session_id,
                    agent_name=agent_name,
                    summary=summary,
                    tags=["compaction"],
                    extras={
                        "kind": "compaction",
                        "parent_session_id": session_id,
                        "turns_compacted": len(middle),
                        "tokens_before": before,
                        "tokens_after": after,
                    },
                )
            )
        except Exception:
            episode_id = None  # durability is best-effort; never break the turn
        else:
            from madras.obs.langfuse_client import push_event

            push_event(
                trace_id=langfuse_trace_id,
                name="memory.write.episodic",
                output={"episode_id": episode_id, "summary": summary},
                metadata={
                    "turns_compacted": len(middle),
                    "tokens_before": before,
                    "tokens_after": after,
                },
            )

    signals = emit_action_signals(
        {
            "task_completion": bool(summary),
            "trajectory_trace": ["compaction"],
            "tool_calls": [],
            "tool_selection": "none_required",
            "argument_correctness": True,
            "confidence": 0.7,
            "latency_ms": round(resp.latency_ms, 3),
            "cost_usd": resp.cost_usd,
        }
    )
    return compacted, CompactionResult(
        summary=summary,
        episode_id=episode_id,
        tokens_before=before,
        tokens_after=after,
        turns_compacted=len(middle),
        signals=signals,
    )
