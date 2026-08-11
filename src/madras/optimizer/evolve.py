"""GEPA-style reflective prompt/skill/tool-desc evolution (W4·B1).

The in-house GEPA pattern (Genetic-Pareto, ICLR-2026): reflect on execution-trace failures,
propose a targeted rewrite, **measure lift** on an eval set, keep a Pareto frontier of
candidates (best on at least one instance), and return the best as a gated `OptimProposal`.

Pure orchestration: the reflection LM (`reflect`) and the eval (`evaluate`) are injected
async callables, so this is hermetically testable and the gateway/proving-ground wiring stays
at the edge. Reflection is governed (gateway) in production; nothing is applied here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from madras.optimizer.models import Candidate, OptimProposal, Target

# evaluate(text) -> {instance_id: score in [0,1]} ; reflect(text, failures) -> new text
Evaluate = Callable[[str], Awaitable[dict[str, float]]]
Reflect = Callable[[str, list[str]], Awaitable[str]]


def pareto(cands: list[Candidate]) -> list[Candidate]:
    """Keep candidates that are strictly best on at least one instance (the GEPA frontier)."""
    if not cands:
        return []
    instances: set[str] = set[str]().union(*[set(c.scores) for c in cands])
    keep: list[Candidate] = []
    for c in cands:
        dominated = False
        for d in cands:
            if d is c:
                continue
            # d dominates c if d >= c on every instance and > on at least one
            ge = all(d.scores.get(i, 0.0) >= c.scores.get(i, 0.0) for i in instances)
            gt = any(d.scores.get(i, 0.0) > c.scores.get(i, 0.0) for i in instances)
            if ge and gt:
                dominated = True
                break
        if not dominated:
            keep.append(c)
    return keep or [max(cands, key=lambda c: c.mean())]


def _failures(cand: Candidate, feedback: dict[str, str], *, floor: float = 1.0) -> list[str]:
    """Feedback strings for the instances this candidate scored below `floor` on."""
    return [feedback[i] for i, s in sorted(cand.scores.items()) if s < floor and i in feedback]


def _select(frontier: list[Candidate]) -> Candidate:
    """Pick the frontier candidate to improve next — highest mean (stable)."""
    return max(frontier, key=lambda c: (c.mean(), c.text))


async def evolve(
    target: Target,
    *,
    evaluate: Evaluate,
    reflect: Reflect,
    feedback: dict[str, str] | None = None,
    rounds: int = 3,
) -> OptimProposal:
    """Evolve ``target.current_text`` over ``rounds``; return a gated proposal with lift."""
    fb = feedback or {}
    baseline = Candidate(target.current_text, await evaluate(target.current_text))
    frontier = [baseline]
    best = baseline
    for _ in range(max(0, rounds)):
        parent = _select(frontier)
        new_text = await reflect(parent.text, _failures(parent, fb))
        if not new_text or new_text == parent.text:
            continue
        cand = Candidate(new_text, await evaluate(new_text))
        frontier = pareto([*frontier, cand])
        if cand.mean() > best.mean():
            best = cand
    return OptimProposal(
        target_kind=target.kind,
        target_id=target.id,
        old_text=baseline.text,
        new_text=best.text,
        baseline_score=baseline.mean(),
        new_score=best.mean(),
        instances=len(baseline.scores),
        rounds=max(0, rounds),
        approved=False,
    )
