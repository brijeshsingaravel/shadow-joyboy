"""Community summarization (row nano-graphrag) -- communities as retrieval units.

OSS-radar review (gusye1234/nano-graphrag, MIT): its actual RAG value-add is
LLM-summarized communities used as retrieval units, not just raw membership lists.
`memory/communities.py::detect_communities` clusters an existing edge list but never
summarizes; `concept_clusters` (relationship_tools.py) surfaces raw node-name lists.
Not forking the 1100-line repo (it bundles its own storage/LLM clients/chunking,
duplicating memory/vector.py + graph.py + LiteLLM routing already in place) -- this
is the one native piece worth adding, reusing compiler/clarify.py's structured_output
idiom. LLM-extraction-to-graph (nano-graphrag's other half) is a separate, larger,
riskier addition left explicitly open, not rushed alongside this.
"""

from __future__ import annotations

from madras.llm.gateway import LLMGateway
from madras.llm.structured import structured_output

_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}


def _prompt(nodes: list[str]) -> str:
    return (
        "The following entities/concepts were grouped together because they are "
        "densely connected in a knowledge graph (a community).\n\n"
        f"Members: {', '.join(nodes)}\n\n"
        "In one sentence, describe what conceptually ties this group together -- "
        "what a reader retrieving this community is actually getting."
    )


async def summarize_community(
    nodes: list[str],
    gateway: LLMGateway,
    model: str,
) -> str:
    """One structured LLM call per community -- a retrieval-unit summary, not just
    the raw member list. Degrades to a plain member-list fallback on any failure
    (never raises -- summarization is an enhancement, not a hard requirement)."""
    if not nodes:
        return ""
    result = await structured_output(
        gateway,
        model,
        [{"role": "user", "content": _prompt(nodes)}],
        _SCHEMA,
        max_retries=2,
    )
    if not result.ok:
        return f"({', '.join(nodes)})"
    summary = str(result.data.get("summary", "")).strip()
    return summary or f"({', '.join(nodes)})"
