"""W2·2(2) — runtime/behavioral anomaly tracking for MCP tool use (ASI02/ASI06).

Static signing + scanning vet a server BEFORE trust; this is the RUNTIME half the 2026
research calls for ("behavioral anomaly detection · model-decision-path tracking"). A
RuntimeMonitor watches the agent's tool-call path within a turn and flags:
  * injected_result    — an MCP tool RETURNED data carrying injected instructions (indirect
                         injection — a vetted server can still poison at runtime);
  * unknown_tool       — a tool not in the server's discovered/pinned set (smuggled tool);
  * exfiltration_chain — a sensitive read followed by an external send (the classic exfil path).
Pure + deterministic; the loop feeds it (tool, args, result) per call.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from madras.mcp.security import scan_result

_SENSITIVE_READ = re.compile(
    r"\b(read|cat|open|get|fetch|list|dump|export)\b.{0,40}\b(secret|token|key|password|"
    r"credential|\.env|\.ssh|private|vault|api[_-]?key)",
    re.I,
)
_EXTERNAL_SEND = re.compile(
    r"\b(send|post|upload|exfiltrat|webhook|email|curl|wget|http://|https://|"
    r"transmit|forward to)\b",
    re.I,
)


@dataclass
class AnomalyEvent:
    kind: str  # injected_result | unknown_tool | exfiltration_chain
    detail: str
    severity: str = "high"


class RuntimeMonitor:
    """Tracks the per-turn tool-call decision path; flags behavioral anomalies."""

    def __init__(self, *, known_tools: set[str] | None = None, window: int = 8) -> None:
        self._known = {t.lower() for t in (known_tools or set())}
        self._recent: deque[str] = deque(maxlen=window)

    def observe(
        self, *, tool: str, args_text: str = "", result_text: str = ""
    ) -> list[AnomalyEvent]:
        """Record one tool call (+ its args/result) and return any anomalies it triggers."""
        events: list[AnomalyEvent] = []
        blob = f"{tool} {args_text}"

        if result_text and scan_result(result_text):
            events.append(
                AnomalyEvent(
                    "injected_result", f"{tool}: injected instruction in the tool's result"
                )
            )
        if self._known and tool.lower() not in self._known:
            events.append(
                AnomalyEvent("unknown_tool", f"{tool} is not in the server's discovered tool set")
            )

        is_read = bool(_SENSITIVE_READ.search(blob))
        is_send = bool(_EXTERNAL_SEND.search(blob)) or bool(_EXTERNAL_SEND.search(result_text))
        if is_send and "sensitive_read" in self._recent:
            events.append(
                AnomalyEvent("exfiltration_chain", f"{tool}: external send after a sensitive read")
            )

        self._recent.append(
            "sensitive_read" if is_read else ("external_send" if is_send else "other")
        )
        return events
