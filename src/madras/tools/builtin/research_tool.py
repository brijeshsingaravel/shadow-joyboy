"""Knowledge-Seeking Engine wired live for the first time (row knowledge-seeking-engine).

research.py's DeepResearch + TongyiResearchBackend (Apache-2.0 pattern lift, zero-cost DDG
search) were fully built with zero live callers -- not even registered as a tool. Registers
deep_research, auto-selecting a seeking mode (metacog/seeking_mode.py, the frame's 6-mode
taxonomy) from the question's own language; reflective/structured modes turn on
disconfirmation-seeking automatically (a skeptical or complete-picture request should
surface contradicting evidence, not just supporting evidence).
"""

from __future__ import annotations

from typing import Any

from madras.metacog.seeking_mode import classify_seeking_mode
from madras.models.agent_config import Rank
from madras.research import DeepResearch, TongyiResearchBackend
from madras.tools.registry import ToolResult, tool

# Modes where surfacing contradicting evidence (not just supporting) is the whole point.
_AUTO_DISCONFIRM_MODES = frozenset({"reflective", "structured"})

_backend: TongyiResearchBackend | None = None


def _get_backend() -> TongyiResearchBackend:
    global _backend
    if _backend is None:
        _backend = TongyiResearchBackend.connect()
    return _backend


@tool(
    name="deep_research",
    toolset="web",
    rank_required=Rank.INTERN,
    description=(
        "Run governed multi-round deep research on a question: decompose into "
        "subquestions, search, verify-before-include, cite sources. Auto-selects a "
        "seeking mode (directed/exploratory/social/experiential/reflective/structured) "
        "from the question's own language; reflective/structured questions also seek out "
        "contradicting evidence, not just supporting evidence."
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The research question"},
        },
        "required": ["question"],
    },
)
async def deep_research(args: dict[str, Any]) -> ToolResult:
    question = str(args.get("question", "")).strip()
    if not question:
        return ToolResult(ok=False, error="question is required")

    try:
        backend = _get_backend()
    except ImportError as exc:
        return ToolResult(ok=False, error=str(exc))

    mode = classify_seeking_mode(question)
    dr = DeepResearch(search_backend=backend, disconfirm=mode in _AUTO_DISCONFIRM_MODES)
    report = await dr.run(question)

    return ToolResult(
        ok=True,
        content=f"[mode: {mode}] {report.report}"
        if report.report
        else f"[mode: {mode}] no verified findings ({report.dropped_claims} claim(s) dropped)",
        extras={
            "mode": mode,
            "rounds": report.rounds,
            "sources_used": report.sources_used,
            "dropped_claims": report.dropped_claims,
            "claims": [{"text": c.text, "sources": c.sources} for c in report.claims],
        },
    )
