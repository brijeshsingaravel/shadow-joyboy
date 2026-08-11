"""Mixture-of-Agents (MoA) — multi-model answer fusion.

N proposers (free-fleet models via task routing) draft answers; an aggregator synthesizes one.
Optional refinement rounds let proposers see the prior round's answers. Model calls are
*injected* → deterministic + zero-cost (no live multi-model hammering). The simpler sibling of
Tree-of-Thoughts; composes the free-fleet routing + the Delegation contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from madras.delegation.verdict import parse_confidence

Proposer = Callable[[str, list[str]], Awaitable[str]]  # (query, prior_answers) -> answer
Aggregator = Callable[[str, list[str]], Awaitable[str]]  # (query, answers) -> fused answer


@dataclass
class MoAResult:
    answer: str
    proposals: list[str] = field(default_factory=list[str])
    rounds: int = 1


def confidence_weighted_pick(answers: list[str]) -> str:
    """A default aggregator core: pick the answer with the highest self-stated confidence
    (reuses delegation.verdict.parse_confidence — the same weighting the verify path uses)."""
    if not answers:
        return ""
    return max(answers, key=parse_confidence)


async def mixture_of_agents(
    query: str,
    *,
    proposers: list[Proposer],
    aggregator: Aggregator,
    rounds: int = 1,
) -> MoAResult:
    """Fan out to proposers (concurrently), then fuse via the aggregator. A proposer that
    raises (or returns empty) is dropped; if all fail in a round, that's a hard error."""
    if not proposers:
        raise ValueError("MoA needs at least one proposer")
    n_rounds = max(1, rounds)
    prior: list[str] = []
    proposals: list[str] = []
    for _ in range(n_rounds):
        results = await asyncio.gather(
            *(p(query, prior) for p in proposers), return_exceptions=True
        )
        proposals = [r for r in results if isinstance(r, str) and r.strip()]
        if not proposals:
            raise RuntimeError("MoA: all proposers failed")
        prior = proposals
    answer = await aggregator(query, proposals)
    return MoAResult(answer=answer, proposals=proposals, rounds=n_rounds)
