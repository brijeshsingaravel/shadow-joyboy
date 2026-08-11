"""Skill Mastery Engine's weakness diagnosis (row skill-mastery-engine).

"What am I weak at?" -- the note's own gap. `SkillStore.record_use` (success/fail
telemetry) existed but had ZERO live callers anywhere; `success_count`/`fail_count`
were permanent zeros. Once real outcomes are recorded (skills/telemetry.py), this
pure ranking closes the diagnosis half: sort the skill library by success rate,
weakest first -- the "weakest-first curriculum scheduling" pattern repeatedly found
in 2025/2026 agent-skill-curriculum research (AIT Academy, SkillAudit, SkillC), none
of which ships as a forkable OSS library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SkillWeakness:
    name: str
    success_count: int
    fail_count: int

    @property
    def uses(self) -> int:
        return self.success_count + self.fail_count

    @property
    def success_rate(self) -> float:
        return self.success_count / self.uses if self.uses else 1.0  # unknown = benefit of doubt


def rank_by_weakness(rows: list[dict[str, Any]], *, min_uses: int = 1) -> list[SkillWeakness]:
    """Weakest-first: skills with real usage data, sorted by success_rate ascending
    (lowest first), ties broken by MORE uses first (more evidence of the weakness).
    Skills with fewer than `min_uses` recorded uses are excluded -- not enough
    signal yet to call them weak, not benefit-of-the-doubt strong either."""
    weaknesses = [
        SkillWeakness(
            name=str(r.get("name", "")),
            success_count=int(r.get("success_count", 0) or 0),
            fail_count=int(r.get("fail_count", 0) or 0),
        )
        for r in rows
    ]
    eligible = [w for w in weaknesses if w.uses >= min_uses]
    eligible.sort(key=lambda w: (w.success_rate, -w.uses))
    return eligible
