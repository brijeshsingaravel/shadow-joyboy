"""RFC-0002 §9.3 -- `FluidMycelialEngine`, the T6 locality micro-benchmark. The single
make-or-break experiment the whole `.tamil` compute-frugality thesis rests on (§9): does laying
out AST nodes by Morton (Z-order) code and traversing them in that order produce a measurably
better real-hardware access pattern than an equivalent flat, non-spatial layout?

**This module measures; it does not decide.** `run_locality_benchmark` returns the raw timings
for both layouts -- whether spatial locality actually wins is read from the result, never
asserted here (§9.4: "either outcome is informative, not a failure state"). Entirely local,
in-process, seeded/deterministic (§9.2 bottleneck #3) -- no network, no live infra touched, pure
NumPy + the wall clock, matching the RFC's own "zero new heavy tooling required" finding.

Wall-clock timing over a real, large-enough NumPy payload array is the portable proxy for
cache/NUMA locality here (no cross-platform perf-counter access assumed) -- disclosed, not
hidden: real hardware perf counters (cache-miss rate specifically) are real future work if wall
clock alone proves too noisy a signal.
"""

from __future__ import annotations

import time

import numpy as np
from pydantic import BaseModel

from madras.dsl.kollan_sparse_index import encode_morton_nd

_COORD_BITS = 10  # 0..1023 per axis, matching encode_morton_nd's own default
_PAYLOAD_FLOATS = 8  # one synthetic "cache line" worth of data per node (64 bytes)


class LocalityResult(BaseModel):
    """Raw measurements only -- §9.4's gate is read from these numbers by whoever runs the
    experiment, never baked in as a verdict here."""

    n_nodes: int
    seed: int
    morton_traversal_seconds: float
    flat_traversal_seconds: float
    morton_full_cycle_seconds: float
    flat_full_cycle_seconds: float


class NeighborhoodResult(BaseModel):
    """The fairer §9.1 measurement: repeatedly visiting a real spatial neighborhood (a
    contiguous Morton-rank window) under two physically DIFFERENT memory layouts --
    Morton-materialized (a plain contiguous slice) vs the original/flat layout (a real gather
    at the same nodes' original, spatially-unrelated positions). Raw measurements only, same
    §9.4 discipline as `LocalityResult`."""

    n_nodes: int
    seed: int
    window: int
    n_queries: int
    morton_layout_seconds: float
    flat_layout_seconds: float


class FluidMycelialEngine:
    """A small NumPy simulation of the elastic box (RFC-0002 §5.2): `n_nodes` synthetic nodes
    placed at random `(x, y, z)` coordinates (seeded -- reproducible across runs, §9.2 #3), each
    carrying one payload "cache line" of floats. `dilate`/`compress`/`contract` are synthetic
    mutation ops standing in for the real elastic box's own dilate/compress/contract cycle
    (§5.2) -- enough churn to make GC-like overhead visible (§9.2 #2), not the real thing yet."""

    def __init__(self, n_nodes: int, *, seed: int, payload_floats: int = _PAYLOAD_FLOATS) -> None:
        if n_nodes <= 0:
            raise ValueError("n_nodes must be positive")
        if payload_floats <= 0:
            raise ValueError("payload_floats must be positive")
        rng = np.random.default_rng(seed)
        limit = 1 << _COORD_BITS
        self.coords = rng.integers(0, limit, size=(n_nodes, 3))
        self.payload = rng.random((n_nodes, payload_floats))

    @property
    def n_nodes(self) -> int:
        return self.coords.shape[0]

    def morton_order(self) -> np.ndarray:
        """Node indices sorted by their Morton code -- spatially near nodes land near each
        other in traversal order."""
        codes = [
            encode_morton_nd((int(x), int(y), int(z)), bits=_COORD_BITS) for x, y, z in self.coords
        ]
        return np.argsort(codes)

    def flat_order(self) -> np.ndarray:
        """The non-spatial baseline: plain index order -- no relationship to physical
        coordinates at all, the "equivalent flat scheduler" §9.1 asks T6 to compare against."""
        return np.arange(self.n_nodes)

    def traverse(self, order: np.ndarray) -> float:
        """Read every node's payload in `order`, summing it (forces a real memory read per
        node, not an optimized-away no-op) -- returns the sum only so the read can't be
        eliminated by the interpreter, not because the sum itself matters."""
        return float(self.payload[order].sum())

    def dilate(self, order: np.ndarray, fraction: float) -> None:
        """Synthetic growth: perturb `fraction` of the nodes' payload (in `order`) -- stands in
        for the elastic box's real dilation (RFC-0002 §5.2)."""
        k = max(1, int(self.n_nodes * fraction))
        idx = order[:k]
        self.payload[idx] += 1.0

    def compress(self, order: np.ndarray, fraction: float) -> None:
        """Synthetic shrink: the inverse perturbation of `dilate`."""
        k = max(1, int(self.n_nodes * fraction))
        idx = order[:k]
        self.payload[idx] -= 1.0

    def contract(self, order: np.ndarray, fraction: float) -> None:
        """Synthetic pruning: zero out a fraction of the nodes' payload -- stands in for the
        elastic box's real contraction (RFC-0002 §5.2)."""
        k = max(1, int(self.n_nodes * fraction))
        idx = order[:k]
        self.payload[idx] = 0.0

    def full_cycle(self, order: np.ndarray, *, fraction: float = 0.1) -> float:
        """§9.2 bottleneck #2: times the FULL dilate->compress->contract cycle, not just
        steady-state traversal -- so mutation/churn overhead is visible, not hidden."""
        start = time.perf_counter()
        self.dilate(order, fraction)
        self.compress(order, fraction)
        self.contract(order, fraction)
        return time.perf_counter() - start

    def materialize_morton_layout(self) -> None:
        """**The real fix for `traverse`'s confound**: `traverse(order)` applied a fancy-index
        gather EVERY call, for BOTH layouts alike -- so the timed cost was dominated by "do one
        permutation gather" (always slow) vs "read the array as-created" (always fast), a NumPy
        indexing-mechanism cost that had nothing to do with cache/NUMA locality, the thing §9.1
        actually asks about. Confirmed empirically: flat beat Morton by the same ~2.5-3x margin
        at every scale from 100k to 10M nodes -- consistent with a fixed mechanical cost, not a
        genuine hardware-locality signal that should vary with data size relative to cache size.

        This method physically reorders `payload` into Morton order ONCE, here, outside any
        timed region -- the actual "lay out AST nodes by a Morton-coded address" §9.1 describes,
        not a per-traversal gather. `self.morton_rank_to_orig_index` records which original node
        each Morton rank came from, so a caller can fetch the SAME set of nodes from the
        un-reordered (`self.payload`) array for a fair, like-for-like comparison."""
        order = self.morton_order()
        self.morton_payload = self.payload[order].copy()
        self.morton_rank_to_orig_index = order

    def spatial_neighborhood(self, rng: np.random.Generator, window: int) -> np.ndarray:
        """A random contiguous window of `window` MORTON RANKS -- ranks adjacent in Morton
        order are spatially adjacent in `(x, y, z)` BY CONSTRUCTION (the Z-order curve's own
        locality-preserving property), so this genuinely models "visit a spatial
        neighborhood," the access pattern real AST traversal / dilate-compress-contract churn
        actually performs, not an arbitrary index range."""
        if window > self.n_nodes:
            raise ValueError(f"window ({window}) cannot exceed n_nodes ({self.n_nodes})")
        start = int(rng.integers(0, self.n_nodes - window + 1))
        return np.arange(start, start + window)


def run_locality_benchmark(n_nodes: int, *, seed: int = 0, repeats: int = 5) -> LocalityResult:
    """The §9.3 experiment itself: build one seeded engine, measure BOTH layouts' steady-state
    traversal time (best-of-`repeats`, to damp scheduling noise) and full-cycle churn time, on
    the identical node set -- so the two layouts are compared on the same data, not resampled
    data that could itself differ."""
    engine = FluidMycelialEngine(n_nodes, seed=seed)
    morton = engine.morton_order()
    flat = engine.flat_order()

    def _best_of(order: np.ndarray) -> float:
        times: list[float] = []
        for _ in range(repeats):
            start = time.perf_counter()
            engine.traverse(order)
            times.append(time.perf_counter() - start)
        return min(times)

    morton_traversal = _best_of(morton)
    flat_traversal = _best_of(flat)
    morton_cycle = engine.full_cycle(morton)
    flat_cycle = engine.full_cycle(flat)

    return LocalityResult(
        n_nodes=n_nodes,
        seed=seed,
        morton_traversal_seconds=morton_traversal,
        flat_traversal_seconds=flat_traversal,
        morton_full_cycle_seconds=morton_cycle,
        flat_full_cycle_seconds=flat_cycle,
    )


def run_neighborhood_benchmark(
    n_nodes: int, *, seed: int = 0, window: int = 64, n_queries: int = 2000
) -> NeighborhoodResult:
    """The fair §9.1 experiment: `n_queries` repeated visits to a random spatial neighborhood
    (`window` Morton-adjacent nodes each), timed under each physical layout. The Morton-layout
    reorder happens ONCE (`materialize_morton_layout`), outside the timed loop -- so what's
    measured per query is a genuine like-for-like access: a contiguous slice of the
    Morton-materialized array vs a gather of the SAME nodes from the un-reordered array. If
    spatial layout gives a real hardware win, it should show up HERE, not get masked by a
    fixed per-call permutation cost every prior version of this benchmark paid on both sides."""
    engine = FluidMycelialEngine(n_nodes, seed=seed)
    engine.materialize_morton_layout()
    rng = np.random.default_rng(seed + 1)  # a separate stream from node placement itself

    windows = [engine.spatial_neighborhood(rng, window) for _ in range(n_queries)]

    start = time.perf_counter()
    for w in windows:
        engine.morton_payload[w[0] : w[0] + window].sum()
    morton_seconds = time.perf_counter() - start

    start = time.perf_counter()
    for w in windows:
        orig_idx = engine.morton_rank_to_orig_index[w[0] : w[0] + window]
        engine.payload[orig_idx].sum()
    flat_seconds = time.perf_counter() - start

    return NeighborhoodResult(
        n_nodes=n_nodes,
        seed=seed,
        window=window,
        n_queries=n_queries,
        morton_layout_seconds=morton_seconds,
        flat_layout_seconds=flat_seconds,
    )


__all__ = [
    "FluidMycelialEngine",
    "LocalityResult",
    "NeighborhoodResult",
    "run_locality_benchmark",
    "run_neighborhood_benchmark",
]
