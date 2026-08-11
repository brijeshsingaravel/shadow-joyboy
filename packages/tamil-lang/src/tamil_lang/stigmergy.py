"""Stigmergic edge reinforcement/decay (RFC-0002 §5.1's "Stigmergic" runtime law) -- the one law
with zero prior code and no PL-runtime precedent. Real, proven prior art instead: AntNet (Di
Caro & Dorigo, 1998) -- a distributed routing algorithm, field-tested by British Telecom,
outperforming OSPF under heavy/variable load. Two mechanics, both real: reinforcement (deposit
pheromone on an edge proportional to use) and decay/evaporation (a rate that continuously
weakens unused edges) -- "active mycelial edges are reinforced, idle ones decay; load
redistributes with no central controller" (§5.1).

Scope, stated honestly: this is the mechanism only -- a real, tested pheromone table over
Mugavari-addressed edges -- not yet wired into live execution, since `.tamil` has no repeated/
long-running execution loop yet (Kural's interpreter runs once and exits). Adaptive (fast-then-
slow) evaporation is a documented future refinement (2026 AH-ACO); this starts with AntNet's
original fixed-rate decay, the proven core mechanic.

Distinct from the vault's `Stigmergy` capability (`kind: frontier`, Framework/Capabilities/
Stigmergy.md) -- that is a separate, deferred, agent-OS-wide item (multi-agent pheromone
coordination between whole agents, shadow-rebuild.md Workstream 9). This module only borrows
the biological name for a narrower, distinct thing: edge reinforcement within one program's own
AST graph.
"""

from __future__ import annotations

Edge = tuple[str, str]  # (parent mugavari_id, child mugavari_id)


class PheromoneTable:
    """A real, tested pheromone table over `.tamil` AST edges. No global controller: each edge's
    weight only ever changes via `reinforce()` (on use) or `decay()` (evaporation over time) --
    the same two mechanics AntNet actually used in production."""

    def __init__(self) -> None:
        self._weights: dict[Edge, float] = {}

    def reinforce(self, edge: Edge, *, amount: float = 1.0) -> None:
        """Deposit pheromone on `edge` -- an active/used edge gets stronger."""
        self._weights[edge] = self._weights.get(edge, 0.0) + amount

    def decay(self, rate: float) -> None:
        """Evaporate every known edge by `rate` (AntNet's fixed evaporation coefficient `rho`,
        `0 <= rate <= 1`) -- an idle edge weakens even if never explicitly touched, exactly
        AntNet's original mechanic, no adaptive schedule yet."""
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"decay rate must be in [0, 1], got {rate!r}")
        for edge in self._weights:
            self._weights[edge] *= 1.0 - rate

    def weight(self, edge: Edge) -> float:
        """An edge never reinforced has weight 0.0 -- no implicit/default strength."""
        return self._weights.get(edge, 0.0)

    def strongest_edges(self, n: int) -> list[Edge]:
        """The `n` highest-weight edges, strongest first -- ties broken by edge identity so the
        order is deterministic, not dict-insertion-order-dependent."""
        ranked = sorted(self._weights.items(), key=lambda item: (-item[1], item[0]))
        return [edge for edge, _weight in ranked[:n]]


__all__ = ["Edge", "PheromoneTable"]
