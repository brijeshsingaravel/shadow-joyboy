"""Mystery Engine's dissonance-detection front-end (row mystery-engine).

The note's own framing: "dissonance is an impasse signal" -- the trigger the
evidence/RCA half (already built: codeact/rca.py + Deep Research) was missing. A
claim `research.py::DeepResearch` rejects as unsupported IS a real contradiction
with evidence, not just a count to drop silently (research: Arbiter, arXiv
2603.08993 -- dissonance detection needs to be a SEPARATE structural check, not
something the executing LLM self-reports and smooths over). Pure extraction here;
`record_dissonance` (tools/builtin/relationship_tools.py) does the live graph write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from madras.metacog.detect import Impasse


@dataclass
class DissonanceEvent:
    claim: str
    reason: str = "verifier rejected the claim as unsupported by evidence"


def dissonance_events_from_report(report: object) -> list[DissonanceEvent]:
    """Turn a `research.py::ResearchReport`'s dropped claims into structured
    dissonance events. Pure -- no infra, mirrors DeepResearch's own dependency-free
    design."""
    texts: list[Any] = getattr(report, "dropped_claim_texts", None) or []
    return [DissonanceEvent(claim=t) for t in texts if str(t).strip()]


def impasse_for(event: DissonanceEvent) -> Impasse:
    """The SOAR-style nudge (metacog/detect.py::recommend_subgoal reads this kind)."""
    return Impasse("dissonance", f"{event.claim!r} -- {event.reason}")
