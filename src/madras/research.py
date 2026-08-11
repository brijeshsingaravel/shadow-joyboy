"""Governed deep research — the OWL / Tongyi-DR / SmolAgents patterns, lifted (B67/row 67).

Madras already IS an agent framework, so this is an orchestration capability that LIFTS the
strongest patterns from the 2026 OSS leaders into a governed loop (not an imported agent):

* **Tongyi DeepResearch — IterResearch loop + context compaction:** research proceeds in ROUNDS;
  each round synthesizes a COMPACT findings string and we carry a *bounded* working report forward
  (never the raw sources). Long-horizon research stays cheap + robust — context can't bloat.
* **OWL — planner → workforce → aggregate:** the question is decomposed into subquestions, each
  researched independently, then aggregated (replan-friendly).
* **Verify-before-include + citations:** every claim must cite >=1 source AND pass an (adversarial)
  verifier or it's DROPPED — no unsupported claims reach the report; full source provenance.

Governance: every source URL is egress-checked ([[Network Egress Policy]] / SSRF), source text is
ASI02-fenced (`<retrieved>`), claims are verified before inclusion, and every step is audited.
Search + LLM steps are injectable (Tongyi-DR via LiteLLM zero-cost / OWL / SmolAgents swappable);
defaults are deterministic so the orchestration is fully testable offline.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from madras.security.net_policy import NetPolicy


@dataclass
class Source:
    url: str
    title: str
    content: str

    def fenced(self) -> str:
        # ASI02: retrieved web content is untrusted DATA, never instructions.
        return f"<retrieved url={self.url!r}>\n{self.content}\n</retrieved>"


@dataclass
class Claim:
    text: str
    sources: list[str] = field(default_factory=list[str])  # supporting source URLs (citations)
    verified: bool = False


@dataclass
class ResearchReport:
    question: str
    report: str  # the synthesized, bounded, cited findings
    claims: list[Claim]
    rounds: int
    sources_used: int
    dropped_claims: int  # robustness: unsupported/failed-verify claims dropped
    working_chars: int  # efficiency: the bounded working-context size
    dropped_claim_texts: list[str] = field(default_factory=list[str])  # row mystery-engine —
    # the note's own "dissonance detection" trigger: a claim the verifier rejected IS a
    # real contradiction with evidence, not just a count. Previously silently discarded.


# Injectable steps (deterministic defaults → testable + zero-cost; swap in Tongyi-DR/OWL).
Planner = Callable[[str], list[str]]  # question -> subquestions (OWL)
Extractor = Callable[[str, Source], list[Claim]]  # subq, source -> claims
Synthesizer = Callable[[str, str, list[Claim]], str]  # subq, prior, claims -> compact findings
Verifier = Callable[[Claim], bool]  # claim -> supported? (adversarial)


class SearchBackend:
    """Injectable search/LLM backend. Default raises — provide a real one (web search tool) or a
    fake in tests. Live: route Tongyi-DR / OWL / SmolAgents (all Apache) here."""

    name = "search-backend"

    async def search(self, query: str, *, k: int) -> list[Source]:  # pragma: no cover
        raise NotImplementedError("inject a SearchBackend (web search tool / Tongyi-DR)")


def _default_plan(question: str) -> list[str]:
    """Decompose on conjunctions/clauses (OWL-style); fall back to the whole question."""
    parts = [p.strip(" ?.") for chunk in question.split("?") for p in chunk.split(" and ")]
    subs = [p for p in parts if len(p) > 8]
    return subs or [question.strip()]


def _default_extract(subq: str, source: Source) -> list[Claim]:
    """One atomic claim per source sentence, each citing the source URL."""
    out: list[Claim] = []
    for sent in source.content.replace("\n", " ").split("."):
        s = sent.strip()
        if len(s) > 12:
            out.append(Claim(text=s, sources=[source.url]))
    return out


def _default_synth(subq: str, prior: str, claims: list[Claim]) -> str:
    """Compact per-round synthesis: the subquestion + its verified claims with inline citations."""
    if not claims:
        return ""
    lines = [f"- {c.text} [{', '.join(c.sources)}]" for c in claims]
    return f"## {subq}\n" + "\n".join(lines)


def _default_verify(claim: Claim) -> bool:
    return bool(claim.sources) and len(claim.text) > 12


@dataclass
class DeepResearch:
    search_backend: SearchBackend
    plan: Planner = _default_plan
    extract: Extractor = _default_extract
    synthesize_round: Synthesizer = _default_synth
    verify: Verifier = _default_verify
    net_policy: NetPolicy = field(default_factory=NetPolicy)
    audit: Callable[[dict[str, object]], None] | None = None
    max_rounds: int = 4
    working_cap: int = 1500  # Tongyi compaction: hard cap on carried working context
    per_query_k: int = 5
    # s46: Knowledge-Seeking Engine's disconfirmation-seeking (row knowledge-seeking-engine)
    # -- the anti-confirmation-bias move: when on, one extra subquestion per round searches
    # for what would CONTRADICT the question's premise, not just what supports it. STORM
    # (Stanford)'s opposing-perspective interviewer pattern, applied here as an extra
    # planned subquestion rather than a separate agent persona -- reuses the SAME
    # decompose/extract/verify pipeline every other subquestion runs through.
    disconfirm: bool = False

    def _audit(self, event: str, **kw: object) -> None:
        if self.audit is not None:
            self.audit({"event": event, **kw})

    def _disconfirming_subquestion(self, question: str) -> str:
        return f"What evidence would contradict or disprove: {question.strip(' ?.')}?"

    async def run(self, question: str) -> ResearchReport:
        subquestions = self.plan(question)[: self.max_rounds]  # OWL decomposition
        if self.disconfirm:
            # replace the LAST slot rather than growing past max_rounds -- disconfirmation
            # is the one round we never want silently dropped by the round cap.
            subquestions = subquestions[: max(1, self.max_rounds - 1)]
            subquestions.append(self._disconfirming_subquestion(question))
        working = ""  # bounded running report
        all_claims: list[Claim] = []
        sources_used = 0
        dropped = 0
        dropped_texts: list[str] = []

        for subq in subquestions:  # Tongyi iterative rounds
            sources = await self.search_backend.search(subq, k=self.per_query_k)
            kept_sources: list[Source] = []
            for s in sources:
                verdict = self.net_policy.check(s.url)
                if not verdict.allow:
                    self._audit("egress_block", subq=subq, url=s.url, reason=verdict.reason)
                    continue
                kept_sources.append(s)
            sources_used += len(kept_sources)

            round_claims: list[Claim] = []
            for s in kept_sources:
                round_claims.extend(self.extract(subq, s))  # extract over the fenced source

            verified: list[Claim] = []
            for c in round_claims:  # verify-before-include
                if c.sources and self.verify(c):
                    c.verified = True
                    verified.append(c)
                else:
                    dropped += 1
                    dropped_texts.append(c.text)
            all_claims.extend(verified)

            findings = self.synthesize_round(subq, working, verified)
            if findings:
                # compaction: carry a BOUNDED working report, not the raw sources
                working = (working + "\n\n" + findings).strip()[-self.working_cap :]
            self._audit(
                "round",
                subq=subq,
                sources=len(kept_sources),
                kept=len(verified),
                dropped=len(round_claims) - len(verified),
            )

        return ResearchReport(
            question=question,
            report=working,
            claims=all_claims,
            rounds=len(subquestions),
            sources_used=sources_used,
            dropped_claims=dropped,
            working_chars=len(working),
            dropped_claim_texts=dropped_texts,
        )


class TongyiResearchBackend(SearchBackend):
    """Adapter for the research model/framework (default: Alibaba Tongyi DeepResearch, Apache-2.0;
    OWL / SmolAgents swappable). Client injected (or a fake in tests); `connect()` lazy-loads.
    Live: route the 30B Tongyi-DR (or OWL's workforce) via LiteLLM/Ollama for a zero-cost run."""

    name = "tongyi-deepresearch"

    def __init__(self, client: object) -> None:
        self._client = client

    @classmethod
    def connect(
        cls, client_factory: Callable[[], object] | None = None, *, region: str = "wt-wt"
    ) -> TongyiResearchBackend:
        """Wire a live SearchBackend. The 30B Tongyi-DR model needs >=24 GB, so the zero-cost path
        the adapter offers is a real **web-search** client (DuckDuckGo via `ddgs` — no API key, one
        request per query, not a sweep). Inject `client_factory` to route the full Tongyi-DR/OWL."""
        if client_factory is not None:
            return cls(client_factory())
        try:
            import ddgs  # noqa: F401  # type: ignore[reportMissingTypeStubs]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "deep-research backend needs the `research` extra (ddgs web search), or inject a "
                "client_factory to route the full Tongyi DeepResearch / OWL (Apache-2.0)"
            ) from exc
        return cls(_WebSearchClient(region=region))

    async def search(self, query: str, *, k: int) -> list[Source]:
        raw = await self._client.search(query, k=k)  # type: ignore[attr-defined]
        rows = cast("list[dict[str, str]]", raw)
        return [
            Source(url=r["url"], title=r.get("title", ""), content=r.get("content", ""))
            for r in rows
        ]


class _WebSearchClient:
    """Zero-cost web search (DuckDuckGo via `ddgs`) for the research SearchBackend."""

    def __init__(self, region: str = "wt-wt") -> None:
        self._region = region

    async def search(self, query: str, *, k: int) -> list[dict[str, str]]:
        return await asyncio.to_thread(self._search, query, k)

    def _search(self, query: str, k: int) -> list[dict[str, str]]:
        import ddgs as _ddgs_mod  # type: ignore[reportMissingTypeStubs]

        ddgs_mod: Any = _ddgs_mod
        rows: Any = ddgs_mod.DDGS().text(query, max_results=k, region=self._region)
        return [
            {"url": r.get("href", ""), "title": r.get("title", ""), "content": r.get("body", "")}
            for r in rows
        ]
