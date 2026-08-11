"""Trajectory + context compression — shrink a long message history without losing the thread.

Tool outputs dominate a long agent trajectory; this compresses the MIDDLE (between the system
prompt and a verbatim recent window): collapse duplicate tool outputs, truncate over-long ones,
keeping the recent window + system message intact. Deterministic + zero-cost by default; pass a
`summarizer` to fold the middle into one LLM summary (route it via the free fleet). Informs the
frontier Entropy-Guided Compaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

Msg = dict[str, Any]


@dataclass
class CompressionStats:
    before_chars: int
    after_chars: int
    dropped: int = 0
    truncated: int = 0

    @property
    def saved_chars(self) -> int:
        return max(0, self.before_chars - self.after_chars)


def _chars(messages: list[Msg]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages)


def compress_trajectory(
    messages: list[Msg],
    *,
    keep_recent: int = 6,
    max_tool_chars: int = 600,
    summarizer: Callable[[list[Msg]], str] | None = None,
) -> tuple[list[Msg], CompressionStats]:
    """Compress a message list. Keeps the leading system message + the last `keep_recent`
    messages verbatim; compresses the middle (dedup + truncate tool outputs, or one summary)."""
    before = _chars(messages)
    if len(messages) <= keep_recent:
        return messages, CompressionStats(before, before)

    head: list[Msg] = []
    body = messages
    if messages and messages[0].get("role") == "system":
        head, body = messages[:1], messages[1:]

    if len(body) <= keep_recent:
        return messages, CompressionStats(before, before)

    middle, recent = body[:-keep_recent], body[-keep_recent:]
    dropped = truncated = 0

    if summarizer is not None:
        summary = summarizer(middle)
        compressed_middle: list[Msg] = [
            {"role": "system", "content": f"[earlier conversation summarized]\n{summary}"}
        ]
        dropped = len(middle) - 1
    else:
        compressed_middle = []
        seen: set[int] = set()
        for m in middle:
            content = str(m.get("content", ""))
            if m.get("role") == "tool":
                key = hash(content)
                if key in seen:
                    dropped += 1
                    continue
                seen.add(key)
                if len(content) > max_tool_chars:
                    elided = len(content) - max_tool_chars
                    m = {**m, "content": content[:max_tool_chars] + f"\n…[+{elided} chars elided]"}
                    truncated += 1
            compressed_middle.append(m)

    out = [*head, *compressed_middle, *recent]
    return out, CompressionStats(before, _chars(out), dropped=dropped, truncated=truncated)
