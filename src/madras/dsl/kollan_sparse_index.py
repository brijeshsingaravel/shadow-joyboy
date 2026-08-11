"""The sparse page-index for Core, 4D+ (RFC-0002's address-space wall; K1's own finding,
s56: past 3D a DENSE N-D->1D layout outbounds even the 64-bit address space, while the
demand-paged working set stays MB-scale at any N).

Promotes the K1 experiment's own validated N-D Morton encoder (O:/tmp/vmax_test/dim_tiering.py,
self-tested bijective for 2D-5D) from scratch harness to real, committed code. Deliberately NOT
`tamil_lang.mugavari._morton3` -- that is a FIXED-3-axis (depth/category/order) scheme for AST
NODE IDENTITY, a different concern; conflating the two would tie Core's memory layout to the
kernel's frozen 6-category node-kind list, which has nothing to do with N-D coordinates.

This module is the pure encode/decode foundation only -- bit-interleave is host/target-agnostic
math, exactly like `_morton3`/`dim_tiering.morton_encode` already are. The arena-resident radix-
tree WALK (real x86-64 stencils reading/inserting nodes keyed by `RADIX_BITS`-bit groups of this
code, living inside the bump arena -- physically real, not host-side Python standing in for it)
is the deliberately separate next row.
"""

from __future__ import annotations

# The radix tree's own branching factor (bits consumed per tree level) -- a LATER row's concern
# (the tree structure itself), exported here because both rows share the same constant: the
# coarsening granularity a "prefix = spatial containment" query operates at.
RADIX_BITS = 8


def encode_morton_nd(coords: tuple[int, ...], bits: int = 10) -> int:
    """Bit-interleave `coords` (one value per dimension) into a single Morton (Z-order) code,
    `bits` bits per axis. Generalizes `dim_tiering.py`'s own validated N-D encoder (2D-5D,
    self-tested bijective) to real code -- same bit-interleave scheme, arbitrary N rather than
    a fixed 2-5 range."""
    if not coords:
        raise ValueError("encode_morton_nd needs at least one coordinate")
    limit = 1 << bits
    for i, c in enumerate(coords):
        if not (0 <= c < limit):
            raise ValueError(f"coordinate {i} ({c}) does not fit in {bits} bits (0..{limit - 1})")
    n = len(coords)
    m = 0
    for i in range(bits):
        for axis, c in enumerate(coords):
            m |= ((c >> i) & 1) << (n * i + axis)
    return m


def decode_morton_nd(code: int, *, n: int, bits: int = 10) -> tuple[int, ...]:
    """The exact inverse of `encode_morton_nd` -- recovers the original `n`-tuple of
    coordinates. `n` and `bits` must match what `code` was encoded with, or the decode is
    silently wrong (not an exception), mirroring `mugavari.decode_morton3`'s own documented
    caveat -- kept as one function so no caller re-derives this arithmetic independently."""
    coords = [0] * n
    for i in range(bits):
        for axis in range(n):
            bit = (code >> (n * i + axis)) & 1
            coords[axis] |= bit << i
    return tuple(coords)


__all__ = ["RADIX_BITS", "decode_morton_nd", "encode_morton_nd"]
