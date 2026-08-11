"""Birthday / anniversary experiences — E5 celebratory reflection engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol


@dataclass(frozen=True)
class BirthdayAnchor:
    """A recurring personal or project milestone."""

    subject_id: str
    kind: str  # "birthday" | "project_start" | "join_date" | custom
    anchor_date: date
    note: str = ""
    importance: int = 3  # 1..5

    def __post_init__(self) -> None:
        if not (1 <= self.importance <= 5):
            raise ValueError("importance must be 1..5")


def next_occurrence(anchor: date, after: date) -> date:
    """Next anniversary of `anchor` on or after `after`."""
    try:
        this_year = anchor.replace(year=after.year)
    except ValueError:
        # Feb 29 on non-leap year -> Feb 28
        this_year = date(after.year, 2, 28)
    if this_year >= after:
        return this_year
    try:
        return anchor.replace(year=after.year + 1)
    except ValueError:
        return date(after.year + 1, 2, 28)


def age_at(anchor: date, on: date) -> int:
    """Years since `anchor` as of `on`."""
    return on.year - anchor.year - ((on.month, on.day) < (anchor.month, anchor.day))


class MemoryLike(Protocol):
    async def recall(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...

    async def store(self, item: dict[str, Any]) -> str: ...


class LedgerLike(Protocol):
    async def recite(self, project_id: str) -> list[dict[str, Any]]: ...


async def birthday_reflection(
    *,
    subject_id: str,
    anchor_date: date,
    kind: str,
    memory: MemoryLike,
    ledger: LedgerLike,
    now: date,
) -> str:
    """Generate a reflective card for an anniversary.

    Returns markdown-ready text summarizing the journey since the anchor.
    """
    years = age_at(anchor_date, now)
    ordinal = _ordinal(years)

    # Pull relevant memories
    recalled: list[dict[str, Any]] = await memory.recall(f"{subject_id} {kind} reflection", limit=5)

    # Pull ledger recitations
    recitations: list[dict[str, Any]] = await ledger.recite(subject_id)

    lines = [
        f"## {ordinal} anniversary of {kind.replace('_', ' ').title()}",
        f"*{years} years since {anchor_date.isoformat()}*",
        "",
    ]

    if recalled:
        lines.append("### Highlights from memory")
        for r in recalled[:3]:
            lines.append(f"- {r.get('content', r.get('text', ''))[:120]}")  # type: ignore[union-attr]
        lines.append("")

    if recitations:
        lines.append("### Recited commitments")
        for r in recitations[:3]:
            lines.append(f"- {r.get('text', '')[:120]}")  # type: ignore[union-attr]
        lines.append("")

    # Drift detection hint
    drift_count = sum(1 for r in recalled if r.get("meta", {}).get("drift_flag"))  # type: ignore[union-attr]
    if drift_count:
        lines.append(f"⚠️ {drift_count} belief(s) have shifted since then — review recommended.")

    lines.append("")
    lines.append(f"_Reflection generated {now.isoformat()}_")

    return "\n".join(lines)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
