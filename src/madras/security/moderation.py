"""security/moderation.py — § E6/F content moderation for user/marketplace agents.

Two layers, mirroring guardrails.py's "deterministic-now, model-judge-escalation,
stable-interface" doctrine:

  1. A DETERMINISTIC hard-block layer (always on, no I/O) — high-precision patterns
     for the catastrophic-unambiguous categories (CSAM, credible mass-casualty /
     weapons, explicit illicit-synthesis, unambiguous cyber-weapon requests). Tuned
     for PRECISION: it must almost never false-positive on legitimate agent traffic,
     because it fails closed.
  2. A pluggable MODEL-JUDGE layer (a `Classifier`) for the nuanced categories
     (Hate / Harassment / Disinformation) where keywords are genuinely weak. Wire a
     Granite-Guardian or free-fleet classifier in; when absent, only layer 1 runs.

Categories align to the AgentHarm taxonomy (Copyright, Cybercrime, Disinformation,
Drugs, Fraud, Harassment, Hate, Sexual) plus the universal must-blocks CSAM and
Violence/Weapons. `moderate()` runs layer 1 first (fail-fast) then the judge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

SAFE_REFUSAL = "I can't help with that — it falls outside what this agent is allowed to do."


@dataclass
class ModerationVerdict:
    allowed: bool
    category: str | None = None
    reason: str = ""
    layer: str = ""  # 'deterministic' | 'model-judge'
    safe_response: str | None = None


class Classifier(Protocol):
    """The model-judge seam. Return (category, score in [0,1]); category=None => clean."""

    async def classify(self, text: str) -> tuple[str | None, float]: ...


# High-precision deterministic patterns — each requires an explicit harmful INTENT +
# a harmful OBJECT so ordinary discussion ("the history of the atomic bomb") doesn't
# trip. Ordered most-severe first.
_HARD_BLOCK: list[tuple[str, str]] = [
    # CSAM — minor + sexual co-occurrence with production/solicitation intent.
    (
        r"(child|minor|underage|preteen|pre-teen|infant)[\w\s]{0,40}"
        r"(porn|sexual|nude|naked|explicit|molest)",
        "Sexual/CSAM",
    ),
    (r"(porn|sexual|nude|naked|explicit)[\w\s]{0,40}(child|minor|underage|preteen)", "Sexual/CSAM"),
    # Mass-casualty weapons / credible violence with build/deploy intent.
    (
        r"(how to|help me|instructions?|steps?|guide)[\w\s]{0,30}"
        r"(build|make|synthesi[sz]e|construct|deploy)[\w\s]{0,30}"
        r"(bomb|explosive|ied|pipe bomb|nerve agent|sarin|vx|anthrax|bioweapon|"
        r"chemical weapon|dirty bomb)",
        "Violence/Weapons",
    ),
    (
        r"(synthesi[sz]e|manufacture|produce)[\w\s]{0,20}(sarin|vx|mustard gas|ricin|anthrax)",
        "Violence/Weapons",
    ),
    # Illicit drug synthesis with explicit how-to intent.
    (
        r"(how to|steps?|instructions?|recipe|synthesi[sz]e)[\w\s]{0,25}"
        r"(meth|methamphetamine|fentanyl|heroin|cocaine|mdma)[\w\s]{0,25}"
        r"(synthesi[sz]e|cook|make|produce|manufactur)",
        "Drugs",
    ),
    (r"(synthesi[sz]e|cook|manufacture)[\w\s]{0,15}(methamphetamine|fentanyl|heroin)", "Drugs"),
    # Cyber-weapons — write/deploy malware with clear malicious intent.
    (
        r"(write|create|generate|build)[\w\s]{0,25}"
        r"(ransomware|keylogger|spyware|rootkit|botnet|trojan|worm)[\w\s]{0,30}"
        r"(to steal|to encrypt|to exfiltrate|to spread|for attack|to infect)",
        "Cybercrime",
    ),
    (
        r"(ransomware|keylogger|rootkit)[\w\s]{0,25}(to steal|to encrypt victim|to exfiltrate)",
        "Cybercrime",
    ),
]


class ModerationEngine:
    """Deterministic hard-block (always on) + an optional model-judge for nuance."""

    def __init__(
        self,
        *,
        judge: Classifier | None = None,
        judge_threshold: float = 0.8,
        safe_refusal: str = SAFE_REFUSAL,
    ) -> None:
        self._judge = judge
        self._threshold = judge_threshold
        self._safe = safe_refusal
        self._patterns = [(re.compile(p, re.IGNORECASE | re.DOTALL), cat) for p, cat in _HARD_BLOCK]

    def inspect(self, text: str) -> ModerationVerdict:
        """Layer 1 only — deterministic, synchronous, no I/O. Fails closed on a match."""
        for rx, cat in self._patterns:
            if rx.search(text):
                return ModerationVerdict(
                    allowed=False,
                    category=cat,
                    layer="deterministic",
                    reason=f"deterministic hard-block ({cat})",
                    safe_response=self._safe,
                )
        return ModerationVerdict(allowed=True)

    async def moderate(self, text: str) -> ModerationVerdict:
        """Full gate: layer 1 (fail-fast) then the model-judge (if wired). Never raises —
        a judge that errors is treated as clean (layer 1 already caught the catastrophic
        set; the judge is best-effort nuance)."""
        hard = self.inspect(text)
        if not hard.allowed:
            return hard
        if self._judge is not None:
            try:
                category, score = await self._judge.classify(text)
            except Exception:
                return ModerationVerdict(allowed=True)
            if category is not None and score >= self._threshold:
                return ModerationVerdict(
                    allowed=False,
                    category=category,
                    layer="model-judge",
                    reason=f"model-judge flagged {category} ({score:.2f})",
                    safe_response=self._safe,
                )
        return ModerationVerdict(allowed=True)
