"""A2A (Agent2Agent) task lifecycle + Agent Card (W4·B2).

Conforms to the Linux-Foundation **A2A v1.0** shapes: a structured task lifecycle
(pending → in-progress → completed/failed/canceled, validated transitions) and a spec
Agent Card served at `/.well-known/agent-card.json`. Optional Ed25519 card signing reuses
the same injected-signer pattern as the memory-export bundle (E-X4b). Pure + testable; the
server wires `/a2a/message` to create + advance a task via the governed loop.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

TASK_STATES = ("pending", "in-progress", "completed", "failed", "canceled")
_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in-progress", "canceled", "failed"},
    "in-progress": {"completed", "failed", "canceled"},
    "completed": set(),
    "failed": set(),
    "canceled": set(),
}


@dataclass
class A2ATask:
    id: str
    message: str = ""
    state: str = "pending"
    result: str = ""
    error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    history: list[str] = field(default_factory=lambda: ["pending"])

    def can_transition(self, to: str) -> bool:
        return to in _TRANSITIONS.get(self.state, set())

    def transition(self, to: str, *, now: float, result: str = "", error: str = "") -> None:
        if not self.can_transition(to):
            raise ValueError(f"a2a: illegal task transition {self.state!r} -> {to!r}")
        self.state = to
        self.updated_at = now
        self.history.append(to)
        if result:
            self.result = result
        if error:
            self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "result": self.result,
            "error": self.error,
            "history": list(self.history),
        }


@dataclass
class AgentCard:
    name: str
    description: str
    version: str
    url: str
    skills: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    protocol_version: str = "1.0"
    default_input_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    streaming: bool = False
    signature: str = ""

    def to_well_known(self) -> dict[str, Any]:
        card: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "protocolVersion": self.protocol_version,
            "url": self.url,
            "capabilities": {"streaming": self.streaming, "pushNotifications": False},
            "skills": self.skills,
            "defaultInputModes": self.default_input_modes,
            "defaultOutputModes": self.default_output_modes,
        }
        if self.signature:
            card["signature"] = self.signature
        return card


def _canonical(card: AgentCard) -> str:
    base = card.to_well_known()
    base.pop("signature", None)
    return json.dumps(base, sort_keys=True, separators=(",", ":"))


def build_agent_card(
    *,
    name: str,
    description: str,
    version: str,
    url: str,
    skills: list[dict[str, str]] | None = None,
    sign: Callable[[str], str] | None = None,
) -> AgentCard:
    """Build a spec Agent Card; if `sign` is given, sign its canonical form (v1.0 signed card)."""
    card = AgentCard(
        name=name, description=description, version=version, url=url, skills=skills or []
    )
    if sign is not None:
        card.signature = sign(_canonical(card))
    return card
