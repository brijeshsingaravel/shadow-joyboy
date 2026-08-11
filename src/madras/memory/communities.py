"""Community detection over the relationship graph (E-X4c) — concept 'logic-layers'.

Groups related entities into communities via **label propagation** (pure stdlib, deterministic:
sorted node order + stable tie-breaks + bounded rounds — no networkx dep). Each community is a
cluster of densely-connected concepts: the memory's emergent logic-layers, surfaced for
navigation/summarization. Edges are treated as undirected for clustering.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from madras.memory.graph import Edge, edge_current


def detect_communities(
    edges: Iterable[Edge],
    *,
    now: float,
    max_rounds: int = 10,
) -> list[list[str]]:
    """Return communities (each a sorted node list), largest first. Deterministic."""
    adj: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for e in edges:
        if not edge_current(e, now):
            continue
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)
        nodes.add(e.src)
        nodes.add(e.dst)
    if not nodes:
        return []

    label = {n: n for n in nodes}
    order = sorted(nodes)
    for _ in range(max_rounds):
        changed = False
        for n in order:
            nbrs = adj[n]
            if not nbrs:
                continue
            counts: dict[str, int] = defaultdict(int)
            for m in nbrs:
                counts[label[m]] += 1
            # most frequent neighbour label; tie -> lexicographically smallest (stable)
            best = min(sorted(counts), key=lambda lbl: (-counts[lbl], lbl))
            if label[n] != best:
                label[n] = best
                changed = True
        if not changed:
            break

    comms: dict[str, set[str]] = defaultdict(set)
    for n, lbl in label.items():
        comms[lbl].add(n)
    return sorted((sorted(c) for c in comms.values()), key=lambda c: (-len(c), c[0]))
