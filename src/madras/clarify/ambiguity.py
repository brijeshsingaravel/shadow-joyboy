"""Ambiguity detection + cost-aware ask policy (the clarify moat's brain).

Research-grounded (Clarify-When-Necessary / mixed-initiative): the failure isn't asking
or not asking — it's that models can't tell well-specified from under-specified, so they
barrel ahead on a wrong assumption (costly rework) OR over-ask (erode trust). This module
encodes the *decision*: detect under-specification, name the SPECIFIC missing slot, and
ask ONLY when genuinely blocked — no safe default AND the answer changes the outcome.
Otherwise proceed, stating the assumption (so the user can correct cheaply).

Pure + deterministic (heuristics). An optional LLM judge can contribute extra findings;
the policy logic is identical for both. Fully testable without a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Irreversible / outward-facing actions — guessing the target here is the costly mistake.
_IRREVERSIBLE = re.compile(
    r"\b(delete|remove|drop|purge|wipe|overwrite|reset|deploy|publish|release|"
    r"send|email|post|merge|push|pay|transfer|refund|cancel|revoke)\b",
    re.I,
)
# Bare references with no antecedent in a one-shot request.
_BARE_REF = re.compile(r"\b(it|this|that|those|these|the one|the thing|the file|them)\b", re.I)
# A concrete-ish target token (path, quoted name, CapWord, code ident, url).
_CONCRETE = re.compile(r"(/\S+|\"[^\"]+\"|'[^']+'|`[^`]+`|\b[A-Z]\w+\b|\w+\.\w+|https?://)")
# Vague success criteria with no measurable target.
_VAGUE = re.compile(
    r"\b(better|best|optimi[sz]e|improve|clean up|cleanup|fix it|handle|deal with|"
    r"appropriate|properly|some|a few|several|nice|good)\b",
    re.I,
)
_MEASURE = re.compile(r"\b(by|to|under|over|within|less than|more than|\d)\b", re.I)


@dataclass
class AmbiguityFinding:
    slot: str  # what's missing: target | referent | criteria
    reason: str
    severity: str  # high | med | low
    safe_default: str | None  # None => no safe default => must ask if also high


def detect_ambiguity(request: str, *, has_context: bool = False) -> list[AmbiguityFinding]:
    """Heuristic under-specification findings for a (user) request. Pure."""
    out: list[AmbiguityFinding] = []
    text = (request or "").strip()
    if not text:
        return out
    irreversible = bool(_IRREVERSIBLE.search(text))
    concrete = bool(_CONCRETE.search(text))
    bare_ref = bool(_BARE_REF.search(text))

    # Irreversible action without an unambiguous (concrete) target — never guess this.
    if irreversible and not concrete:
        out.append(
            AmbiguityFinding(
                slot="target",
                severity="high",
                safe_default=None,
                reason="an irreversible/outward action with no unambiguous target — guessing "
                "could do real damage",
            )
        )
    # An unresolved bare reference with no prior context to bind it.
    elif bare_ref and not concrete and not has_context:
        out.append(
            AmbiguityFinding(
                slot="referent",
                severity="high",
                safe_default=None,
                reason="a bare reference ('it'/'that') with nothing to bind it to",
            )
        )

    # Vague success criteria — proceed with a stated assumption (a safe default exists).
    if _VAGUE.search(text) and not _MEASURE.search(text):
        out.append(
            AmbiguityFinding(
                slot="criteria",
                severity="med",
                safe_default="apply a sensible default and state the assumption",
                reason="vague success criteria (no measurable target)",
            )
        )
    return out


@dataclass
class ClarifyDecision:
    action: str  # "ask" | "proceed"
    finding: AmbiguityFinding | None = None
    question: str = ""
    options: list[str] | None = None
    assumption: str = ""  # stated when proceeding despite mild ambiguity


def _frame(f: AmbiguityFinding) -> tuple[str, list[str] | None]:
    if f.slot == "target":
        return (
            "Before I do something irreversible — what exactly should I act on? "
            "Please name the specific target.",
            None,
        )
    if f.slot == "referent":
        return ("Just to be sure I get this right — what does that refer to?", None)
    return ("What should 'done' look like here?", None)


def decide(findings: list[AmbiguityFinding]) -> ClarifyDecision:
    """Cost-aware Clarify-vs-Proceed. Ask ONLY when blocked (high severity) AND there's no
    safe default; otherwise proceed, surfacing the assumption so the user can correct."""
    must_ask = [f for f in findings if f.severity == "high" and f.safe_default is None]
    if must_ask:
        q, opts = _frame(must_ask[0])
        return ClarifyDecision(action="ask", finding=must_ask[0], question=q, options=opts)
    soft = [f for f in findings if f.safe_default]
    if soft:
        return ClarifyDecision(
            action="proceed",
            finding=soft[0],
            assumption=f"Assuming I {soft[0].safe_default} for "
            f"{soft[0].slot}; tell me if you'd prefer otherwise.",
        )
    return ClarifyDecision(action="proceed")
