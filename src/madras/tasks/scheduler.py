"""In-process automation scheduler.

Evaluates 5-field cron expressions against the wall clock and fires due, enabled
automations through a launch callback (the cockpit wires this to the governed
background-task machinery). Deliberately dependency-free — a small, well-tested cron
matcher covers the template schedules (``0 8 * * *``, ``0 17 * * FRI``, ``0 */6 * * *``)
and the common cases. Not a full crontab implementation; unsupported tokens simply
don't match (fail closed) rather than raising.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

_DOW_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}
_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _num(token: str, names: dict[str, int] | None) -> int | None:
    token = token.strip().lower()
    if names is not None and token in names:
        return names[token]
    try:
        return int(token)
    except ValueError:
        return None


def _parse_field(field: str, lo: int, hi: int, names: dict[str, int] | None = None) -> set[int]:
    """Expand one cron field into the set of matching integers in [lo, hi]."""
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        base = part
        if "/" in part:
            base, _, step_s = part.partition("/")
            try:
                step = int(step_s)
            except ValueError:
                continue
            if step <= 0:
                continue
        if base in ("*", ""):
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            start = _num(a, names)
            end = _num(b, names)
            if start is None or end is None:
                continue
        else:
            v = _num(base, names)
            if v is None:
                continue
            start = end = v
        for x in range(start, end + 1, step):
            if lo <= x <= hi:
                out.add(x)
    return out


def cron_match(expr: str, dt: datetime) -> bool:
    """True if the 5-field cron expression matches ``dt`` (to the minute).

    Fields: minute hour day-of-month month day-of-week. Day-of-week 0/7 = Sunday;
    names (MON..SUN, JAN..DEC) accepted. Standard dom/dow OR-semantics: when BOTH
    are restricted the entry matches if EITHER matches; otherwise both must match.
    """
    fields = expr.split()
    if len(fields) != 5:
        return False
    minute = _parse_field(fields[0], 0, 59)
    hour = _parse_field(fields[1], 0, 23)
    dom = _parse_field(fields[2], 1, 31)
    month = _parse_field(fields[3], 1, 12, _MONTH_NAMES)
    dow = _parse_field(fields[4], 0, 7, _DOW_NAMES)

    if dt.minute not in minute or dt.hour not in hour or dt.month not in month:
        return False

    cron_dow = dt.isoweekday() % 7  # Mon=1..Sat=6, Sun=0
    dom_ok = dt.day in dom
    dow_ok = cron_dow in dow or (cron_dow == 0 and 7 in dow)

    dom_restricted = fields[2].strip() != "*"
    dow_restricted = fields[4].strip() != "*"
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


class AutomationScheduler:
    """Fires due, enabled automations through a launch callback.

    ``automations`` is a zero-arg callable returning the live list of automation
    dicts ({id, name, schedule, prompt, enabled, ...}); the scheduler stamps
    ``_last_fired`` (minute key) on each to avoid double-firing within a minute.
    ``launch`` is an async callable invoked with the automation dict when it is due.
    """

    def __init__(
        self,
        *,
        automations: Callable[[], list[dict[str, Any]]],
        launch: Callable[[dict[str, Any]], Awaitable[Any]],
        clock: Callable[[], datetime] | None = None,
        interval_secs: float = 30.0,
    ) -> None:
        self._automations = automations
        self._launch = launch
        self._clock = clock or (lambda: datetime.now(UTC))
        self._interval = interval_secs

    async def tick(self, now: datetime) -> list[str]:
        """Fire every due+enabled automation once for this minute. Returns fired ids."""
        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        fired: list[str] = []
        for a in list(self._automations()):
            if not a.get("enabled"):
                continue
            if a.get("_last_fired") == minute_key:
                continue
            if cron_match(str(a.get("schedule") or ""), now):
                a["_last_fired"] = minute_key
                try:
                    await self._launch(a)
                    fired.append(str(a.get("id")))
                except Exception:
                    # A single bad automation must never kill the scheduler loop.
                    pass
        return fired

    async def run(self) -> None:  # pragma: no cover - exercised via the live container
        """Long-running loop: tick, then sleep. Survives launch errors."""
        while True:
            try:
                await self.tick(self._clock())
            except Exception:
                pass
            await asyncio.sleep(self._interval)
