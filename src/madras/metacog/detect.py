"""Impasse detection + subgoal recommendation + self-monitor (W5·F4). Pure."""

from __future__ import annotations

from dataclasses import dataclass, field

IMPASSE_KINDS = (
    "repeated_failure",
    "no_progress",
    "low_confidence",
    "choice_paralysis",
    "dissonance",  # row mystery-engine — new evidence contradicted an existing belief
)


@dataclass
class Outcome:
    tool: str
    ok: bool
    progressed: bool = True  # did the action advance the task state?
    confidence: float = 1.0


@dataclass
class Impasse:
    kind: str
    detail: str = ""


def detect_impasse(
    recent: list[Outcome],
    *,
    fail_threshold: int = 2,
    stall_threshold: int = 3,
    conf_floor: float = 0.4,
    options_pending: int = 0,
) -> Impasse | None:
    """Classify the strongest current impasse over recent outcomes, else None."""
    if options_pending >= 4:
        return Impasse("choice_paralysis", f"{options_pending} options unresolved")
    if not recent:
        return None
    last = recent[-1]
    same_fail = [o for o in recent if o.tool == last.tool and not o.ok]
    if not last.ok and len(same_fail) >= fail_threshold:
        return Impasse("repeated_failure", f"{last.tool} failed {len(same_fail)}x")
    tail = recent[-stall_threshold:]
    if len(tail) >= stall_threshold and not any(o.progressed for o in tail):
        return Impasse("no_progress", f"no progress in {len(tail)} steps")
    if last.confidence < conf_floor:
        return Impasse("low_confidence", f"confidence {last.confidence:.2f}")
    return None


_STRATEGY = {
    "repeated_failure": (
        "Stop retrying the same action. Reflect on WHY it fails "
        "(self-critique), then fix the inputs or delegate it to a specialist."
    ),
    "no_progress": (
        "Decompose the task into smaller sub-steps and tackle one; "
        "if a step needs expertise, delegate it."
    ),
    "low_confidence": (
        "You're uncertain — gather more evidence (read/search) or ask the user "
        "a clarifying question before acting."
    ),
    "choice_paralysis": (
        "Pick the highest-expected-value option and commit; you can revise after one step."
    ),
    "dissonance": (
        "Something you found contradicts an existing belief/claim. Don't smooth "
        "over it -- form a hypothesis, weigh the new evidence against the old, "
        "and either revise the belief or explain why it still holds."
    ),
}


def recommend_subgoal(impasse: Impasse) -> str:
    """SOAR substate strategy for the impasse (a nudge the agent acts on)."""
    return _STRATEGY.get(impasse.kind, "Form a subgoal to resolve the blocker, then re-plan.")


@dataclass
class SelfMonitor:
    """Accumulates outcomes and checks for an impasse (reason about one's own reasoning)."""

    outcomes: list[Outcome] = field(default_factory=list[Outcome])

    def record(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)

    def check(self, **kw: object) -> Impasse | None:
        return detect_impasse(self.outcomes, **kw)  # type: ignore[arg-type]
