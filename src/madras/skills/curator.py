"""Curator — skill lifecycle from usage telemetry (the Hermes curator pattern, practical
Apoptosis). Pins heavily-used skills, archives stale/unused ones, restores archived skills
that get used again. **Never deletes** — archive is reversible. Pure policy + a thin applier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Defaults (seconds). 30 days idle = stale.
STALE_SECS = 30 * 86_400
PIN_MIN_USES = 20
ARCHIVE_MAX_USES = 1

ACTIONS = ("pin", "archive", "restore", "keep")


@dataclass
class SkillUsage:
    name: str
    status: str  # active | archived (others ignored)
    pinned: bool = False
    uses: int = 0  # success + fail
    last_used_secs: float | None = None
    created_secs: float = 0.0


@dataclass
class CurationDecision:
    name: str
    action: str  # pin | archive | restore | keep
    reason: str = ""


def _idle(u: SkillUsage, now: float) -> float:
    ref = u.last_used_secs if u.last_used_secs is not None else u.created_secs
    return now - ref


def curate(
    rows: list[SkillUsage],
    *,
    now: float,
    stale_secs: float = STALE_SECS,
    pin_min_uses: int = PIN_MIN_USES,
    archive_max_uses: int = ARCHIVE_MAX_USES,
) -> list[CurationDecision]:
    """Decide per-skill: pin / archive / restore / keep. Never returns a delete."""
    out: list[CurationDecision] = []
    for u in rows:
        idle = _idle(u, now)
        if u.status == "active" and not u.pinned and u.uses >= pin_min_uses:
            out.append(CurationDecision(u.name, "pin", f"{u.uses} uses >= {pin_min_uses}"))
        elif (
            u.status == "archived"
            and u.last_used_secs is not None
            and (now - u.last_used_secs) < stale_secs
        ):
            out.append(CurationDecision(u.name, "restore", "used again recently"))
        elif (
            u.status == "active"
            and not u.pinned
            and idle >= stale_secs
            and u.uses <= archive_max_uses
        ):
            out.append(CurationDecision(u.name, "archive", f"idle {int(idle)}s, {u.uses} uses"))
        else:
            out.append(CurationDecision(u.name, "keep"))
    return out


def usage_from_row(row: dict[str, Any]) -> SkillUsage:
    """Adapt a SkillStore.usage_rows() dict to a SkillUsage."""
    lu = row.get("last_used_secs")
    return SkillUsage(
        name=row["name"],
        status=row["status"],
        pinned=bool(row.get("pinned")),
        uses=int(row.get("uses") or 0),
        last_used_secs=float(lu) if lu is not None else None,
        created_secs=float(row.get("created_secs") or 0.0),
    )


async def apply_curation(
    store: Any,
    decisions: list[CurationDecision],
    *,
    project: str = "default",
) -> dict[str, int]:
    """Apply decisions to the store (pin/archive/restore). Returns per-action counts."""
    counts = {a: 0 for a in ACTIONS}
    for d in decisions:
        if d.action == "pin":
            await store.set_pinned(d.name, True, project=project)
        elif d.action == "archive":
            await store.set_status(d.name, "archived", project=project)
        elif d.action == "restore":
            await store.set_status(d.name, "active", project=project)
        counts[d.action] += 1
    return counts
