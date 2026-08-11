"""Shadow Mode guard — for the first 30 sessions, irreversible actions are planned, not executed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Defined in `security/irreversible.py`, a leaf module, and re-exported here so every existing
# caller keeps working. It moved because `security/permissions.py` needs it too, and importing it
# from this package pulled the entire nightly Memory Manager -- 14 modules -- into the permission
# engine to obtain six strings. "Irreversible, therefore ask first" is a security statement; the
# arrow now points from here to security rather than the other way round (s66).
from madras.security.irreversible import IRREVERSIBLE_ACTIONS

__all__ = ["IRREVERSIBLE_ACTIONS", "PlannedAction", "ShadowModeGuard"]


@dataclass
class PlannedAction:
    action: str
    args: dict[str, Any]
    reason: str


class ShadowModeGuard:
    """Enforces Shadow Mode for the first ``threshold`` sessions.

    When active, irreversible actions are intercepted and returned as
    PlannedAction (plan-only) rather than executed.
    """

    def __init__(self, *, session_count: int, threshold: int = 30) -> None:
        self._session_count = session_count
        self._threshold = threshold

    @property
    def active(self) -> bool:
        """True when session_count is below the threshold."""
        return self._session_count < self._threshold

    def check(self, *, action: str, args: dict[str, Any] | None = None) -> PlannedAction | None:
        """Return a PlannedAction if Shadow Mode is active and the action is irreversible.

        Returns None when the action may proceed normally.
        """
        if not self.active:
            return None
        if action not in IRREVERSIBLE_ACTIONS:
            return None
        return PlannedAction(
            action=action,
            args=args or {},
            reason=(
                f"Shadow Mode is active (session {self._session_count} < {self._threshold}). "
                f"Action '{action}' is irreversible and will be planned, not executed."
            ),
        )
