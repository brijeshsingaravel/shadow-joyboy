"""Per-turn tool-loop guardrail — detect a stuck agent and halt before it burns the turn.

Watches the tool-call trajectory and flags three pathologies (the Hermes pattern):
- **exact-failure repeat** — the same tool+args failing again and again,
- **same-call repeat** — the same tool+args invoked over and over (mutating calls halt sooner
  than idempotent reads),
- **no-progress cycle** — a window of calls with no success and ≤2 distinct signatures.

Pure + deterministic; the agentic loop calls `observe(...)` after each tool result and acts on
`halt`. Completes Graceful Error Recovery.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

OK, WARN, HALT = "ok", "warn", "halt"


@dataclass
class LoopVerdict:
    action: str  # ok | warn | halt
    reason: str = ""


def _arg_key(tool: str, args: dict[str, Any]) -> str:
    try:
        blob = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = str(args)
    return hashlib.blake2b(f"{tool}|{blob}".encode(), digest_size=12).hexdigest()


@dataclass
class LoopGuard:
    repeat_halt: int = 3  # identical idempotent calls in a row → halt
    fail_halt: int = 2  # identical FAILING calls (in window) → halt
    window: int = 8  # look-back window for no-progress / failures
    mutating: frozenset[str] = field(default_factory=frozenset[str])
    _hist: list[tuple[str, str, bool]] = field(
        default_factory=list[tuple[str, str, bool]]
    )  # (tool, key, ok)

    def observe(
        self, tool: str, args: dict[str, Any], ok: bool, *, toolset: str | None = None
    ) -> LoopVerdict:
        key = _arg_key(tool, args)
        self._hist.append((tool, key, ok))
        sig = (tool, key)

        # consecutive identical signatures at the tail
        consec = 0
        for t, k, _o in reversed(self._hist):
            if (t, k) == sig:
                consec += 1
            else:
                break

        recent = self._hist[-self.window :]
        fails = sum(1 for t, k, o in recent if (t, k) == sig and not o)
        if fails >= self.fail_halt:
            return LoopVerdict(HALT, f"{tool} failed identically {fails}x")

        # mutating calls are riskier → halt one repeat sooner
        limit = self.repeat_halt - (1 if toolset in self.mutating else 0)
        if consec >= limit:
            return LoopVerdict(HALT, f"{tool} repeated {consec}x with identical args")

        # no-progress cycle: a full window with no success and very few distinct signatures
        if len(recent) >= max(4, self.window // 2):
            distinct = {(t, k) for t, k, _o in recent}
            if len(distinct) <= 2 and not any(o for _t, _k, o in recent):
                return LoopVerdict(HALT, "no progress — cycling without success")

        if consec >= limit - 1 and consec >= 2:
            return LoopVerdict(WARN, f"{tool} repeating ({consec}x)")
        return LoopVerdict(OK)

    def reset(self) -> None:
        self._hist.clear()
