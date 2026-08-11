"""Commitments — a governed commitment machine for the agent's promises (row 75).

Beyond a reminder list (OpenClaw): each promise the agent makes is a SOCIAL COMMITMENT
(debtor=agent → creditor=user) with a formal lifecycle, lifting three outlier methodologies:
* **Commitment machines** — the formal op set: create → discharge / release / violate.
* **BDI intention-as-commitment + intention RECONSIDERATION** — a conditional commitment auto-
  releases when its antecedent can no longer hold (world moved on → don't honor a stale promise).
* **Verify-before-commit** — `discharge` REQUIRES evidence; a `violate` is a trust signal.
**Conditional commitments** `C(antecedent → consequent)` capture "I'll do X after Y" so a commitment
becomes DUE only when its antecedent fires. Pure core + an injectable/deterministic extractor.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class State(str, Enum):
    PENDING = "pending"  # conditional: antecedent not yet fired
    ACTIVE = "active"  # due/deliverable (unconditional, or antecedent fired)
    DISCHARGED = "discharged"  # fulfilled (evidence-backed)
    RELEASED = "released"  # reconsidered / dismissed — no longer applies
    VIOLATED = "violated"  # broken — a trust signal


@dataclass
class Commitment:
    id: str
    text: str  # the raw promise
    consequent: str  # what the agent will do
    antecedent: str = ""  # the trigger ("the deploy"); "" = unconditional
    session_id: str = ""  # provenance
    created_at: float = 0.0
    due_at: float | None = None  # deadline once ACTIVE
    state: State = State.ACTIVE
    evidence: str = ""  # discharge evidence (verify-before-commit)

    @property
    def conditional(self) -> bool:
        return bool(self.antecedent)


_STOP = frozenset("the a an of to in on at for and or is are this that it you i we my your".split())


def _toks(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2 and w not in _STOP}


def _matches(antecedent: str, event: str) -> bool:
    a = _toks(antecedent)
    return bool(a) and bool(a & _toks(event))


_TRIGGER = r"(?:after|once|when|as soon as)"
_PROMISE = re.compile(
    rf"\bi(?:'ll|\s+will|'m going to|\s+am going to)\s+(?P<what>.+?)"
    rf"(?:\s+{_TRIGGER}\s+(?P<trig>.+))?[.!?]",
    re.I,
)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip(" .,!?'\"").strip()


def extract_commitments(
    text: str, *, session_id: str = "", now: float = 0.0, start_seq: int = 0
) -> list[Commitment]:
    """Detect promises the AGENT made (deterministic); parse the conditional antecedent. An
    injectable LLM extractor can layer on for higher recall (OpenClaw-style background pass)."""
    out: list[Commitment] = []
    seen: set[tuple[str, str]] = set()
    seq = start_seq
    for raw in re.split(r"(?<=[.!?])\s+|\n", text or ""):
        line = raw.strip()
        if not line:
            continue
        probe = line if re.search(r"[.!?]$", line) else line + "."
        m = _PROMISE.search(probe)
        if not m:
            continue
        what = _clean(m.group("what"))
        trig = _clean(m.group("trig") or "")
        if len(what) < 3:
            continue
        key = (what.lower(), trig.lower())
        if key in seen:
            continue
        seen.add(key)
        seq += 1
        out.append(
            Commitment(
                id=f"c{seq}",
                text=line,
                consequent=what,
                antecedent=trig,
                session_id=session_id,
                created_at=now,
            )
        )
    return out


@dataclass
class CommitmentMachine:
    max_active: int = 3  # delivery cap (OpenClaw maxPerDay analogue)
    audit: Callable[[dict[str, Any]], None] | None = None

    def __post_init__(self) -> None:
        self._items: dict[str, Commitment] = {}

    def _audit(self, op: str, c: Commitment) -> None:
        if self.audit is not None:
            self.audit(
                {
                    "event": "commitment",
                    "op": op,
                    "id": c.id,
                    "state": c.state.value,
                    "session": c.session_id,
                    "text": c.consequent,
                }
            )

    def register(self, c: Commitment) -> Commitment:
        c.state = State.PENDING if c.conditional else State.ACTIVE
        self._items[c.id] = c
        self._audit("create", c)
        return c

    def add_all(self, commitments: list[Commitment]) -> list[Commitment]:
        return [self.register(c) for c in commitments]

    def fire(self, antecedent_event: str) -> list[Commitment]:
        """An antecedent event occurred → activate the matching PENDING (conditional) ones."""
        fired: list[Commitment] = []
        for c in self._items.values():
            if c.state is State.PENDING and _matches(c.antecedent, antecedent_event):
                c.state = State.ACTIVE
                self._audit("activate", c)
                fired.append(c)
        return fired

    def discharge(self, cid: str, *, evidence: str) -> bool:
        """Fulfill — REQUIRES evidence (verify-before-commit); no evidence ⇒ cannot discharge."""
        c = self._items.get(cid)
        if c is None or c.state not in (State.ACTIVE, State.PENDING) or not evidence:
            return False
        c.state = State.DISCHARGED
        c.evidence = evidence
        self._audit("discharge", c)
        return True

    def release(self, cid: str, *, reason: str = "") -> bool:
        """User dismiss / no-longer-applies."""
        c = self._items.get(cid)
        if c is None or c.state in (State.DISCHARGED, State.VIOLATED):
            return False
        c.state = State.RELEASED
        self._audit("release", c)
        return True

    def reconsider(self, invalid_antecedents: list[str]) -> list[Commitment]:
        """Intention reconsideration — auto-release PENDING commitments whose antecedent can no
        longer hold (the world moved on → don't deliver a stale promise)."""
        released: list[Commitment] = []
        for c in self._items.values():
            if c.state is State.PENDING and any(
                _matches(c.antecedent, a) for a in invalid_antecedents
            ):
                c.state = State.RELEASED
                self._audit("reconsider_release", c)
                released.append(c)
        return released

    def sweep_violated(self, now: float) -> list[Commitment]:
        """ACTIVE + past-due, never discharged → VIOLATED (a trust signal)."""
        violated: list[Commitment] = []
        for c in self._items.values():
            if c.state is State.ACTIVE and c.due_at is not None and now > c.due_at:
                c.state = State.VIOLATED
                self._audit("violate", c)
                violated.append(c)
        return violated

    def due(self, now: float | None = None) -> list[Commitment]:
        """ACTIVE commitments to deliver now, capped at max_active (oldest first)."""
        active = sorted(
            (c for c in self._items.values() if c.state is State.ACTIVE), key=lambda c: c.created_at
        )
        return active[: self.max_active]

    def by_state(self, state: State) -> list[Commitment]:
        return [c for c in self._items.values() if c.state is state]

    def trust_score(self) -> float:
        """Honored / (honored + violated) over resolved commitments — the reliability signal."""
        honored = len(self.by_state(State.DISCHARGED))
        violated = len(self.by_state(State.VIOLATED))
        total = honored + violated
        return honored / total if total else 1.0
