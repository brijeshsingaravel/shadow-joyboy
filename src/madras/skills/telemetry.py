"""Skill Mastery Engine's outcome-recording bridge (row skill-mastery-engine).

`retrieve_skills()` (the READ half) is live-wired in server/app.py; `record_use()`
(the WRITE half) had zero callers -- surfaced skills never got their outcome recorded,
so `success_count`/`fail_count` stayed permanent zeros. `turn_succeeded` reuses the
SAME trajectory tags already emitted by graph/tool_loop.py (`stuck_loop`,
`wise_quit`) as the success signal -- no new signal invented.
"""

from __future__ import annotations

from typing import Protocol

_FAILURE_TAGS = frozenset({"stuck_loop", "wise_quit"})


def turn_succeeded(trajectory: list[str]) -> bool:
    """A turn is considered successful unless it ended in one of the loop's own
    honest-failure trajectory tags (stuck-loop circuit breaker, or a wise quit)."""
    return not (_FAILURE_TAGS & set(trajectory or []))


class SkillTelemetryStore(Protocol):
    async def record_use(self, name: str, *, project: str, success: bool) -> None: ...


async def record_skill_outcomes(
    store: SkillTelemetryStore,
    names: list[str],
    *,
    project: str,
    success: bool,
) -> None:
    """Record this turn's outcome against every skill that was actually surfaced
    (`RetrievedSkills.matched_names`) -- never against the full L0 listing, since
    most of those weren't actually used for this turn."""
    for name in names:
        try:
            await store.record_use(name, project=project, success=success)
        except Exception:
            pass  # best-effort telemetry -- never breaks the turn
