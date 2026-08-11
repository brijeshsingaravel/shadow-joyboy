"""Leadership Engine's style-selection layer (Human-Aligned frame, row leadership-engine).

The note's own gap: delegation has task handoff (kanban/verdict) but no *leadership
judgment* -- style selection, adaptive to a worker's demonstrated competence. This is
Hersey-Blanchard Situational Leadership (1969): the proven, well-established model the
note itself names, applied here with a single axis -- competence (a rolling per-role
success rate) -- since "commitment" (the model's second axis) has no honest analogue for
a stateless LLM worker. Research (s46) found no existing OSS applying this model to
multi-agent orchestration; this is a native build, not a fork.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from madras.tasks.durable_world import DurableWorld


class LeadershipStyle(str, Enum):
    DIRECTIVE = "directive"  # low competence: explicit, step-by-step instructions
    COACHING = "coaching"  # low-moderate: explain the why, guide closely
    SUPPORTIVE = "supportive"  # moderate-high: collaborate, worker owns the how
    DELEGATIVE = "delegative"  # high: high-level goal only, trust the worker


# Hersey-Blanchard's own quadrant thresholds, competence on a 0..1 scale.
_THRESHOLDS: tuple[tuple[float, LeadershipStyle], ...] = (
    (0.25, LeadershipStyle.DIRECTIVE),
    (0.5, LeadershipStyle.COACHING),
    (0.75, LeadershipStyle.SUPPORTIVE),
)


def select_style(competence: float) -> LeadershipStyle:
    for ceiling, style in _THRESHOLDS:
        if competence < ceiling:
            return style
    return LeadershipStyle.DELEGATIVE


_STYLE_PREFIX: dict[LeadershipStyle, str] = {
    LeadershipStyle.DIRECTIVE: (
        "Leadership style: DIRECTIVE. You have not yet demonstrated reliability on this "
        "kind of task, so follow the instructions below precisely and step-by-step. Do not "
        "improvise on the approach -- ask nothing, just execute exactly what's asked.\n\n"
    ),
    LeadershipStyle.COACHING: (
        "Leadership style: COACHING. Here is the goal and the reasoning behind it -- use "
        "your judgment on execution details, but stay close to the approach described.\n\n"
    ),
    LeadershipStyle.SUPPORTIVE: (
        "Leadership style: SUPPORTIVE. You've shown solid competence on this kind of task. "
        "Here is the goal -- you own the approach; report back if you hit a real blocker.\n\n"
    ),
    LeadershipStyle.DELEGATIVE: (
        "Leadership style: DELEGATIVE. You've consistently delivered on this kind of task. "
        "Here is the outcome that's needed -- full trust on how you get there.\n\n"
    ),
}


def style_prefix(style: LeadershipStyle) -> str:
    return _STYLE_PREFIX[style]


@dataclass
class CompetenceTracker:
    """Rolling per-role success-rate history, DurableWorld-backed (row 87 -- survives a
    restart; trust that builds over time should persist across sessions)."""

    world: DurableWorld
    ns: str = "leadership_competence"
    max_len: int = 20

    def competence(self, role: str) -> float:
        history: list[float] = list(self.world.get(self.ns, role) or [])
        if not history:
            return 0.0  # unknown worker -> start directive, earn trust
        return sum(history) / len(history)

    def record(self, role: str, *, ok: bool) -> None:
        history: list[float] = list(self.world.get(self.ns, role) or [])
        history.append(1.0 if ok else 0.0)
        history = history[-self.max_len :]
        self.world.put(self.ns, role, history)

    def style_for(self, role: str) -> LeadershipStyle:
        return select_style(self.competence(role))
