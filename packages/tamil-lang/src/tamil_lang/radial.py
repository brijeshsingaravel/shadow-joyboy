"""Radial node placement -- the "circle inside the box" (RFC-0002 §4.2, founder's own framing
s55: `.tamil`'s venv is a bounded, radial box).

Every node's Mugavari `(x, y, z)` becomes a real polar coordinate: `x` (depth from the origin)
is the radius -- near-origin nodes sit at the centre (hot), far ones at the rim (cold), exactly
matching §4.2's "x doubles as the hot<->cold memory tier." `y` (the six kernel categories)
divides the circle into six fixed angular sectors, never grown here (D50/D60's admission rule
applies to geometry too -- a new capability is a catalog entry, not a seventh sector). `z`
(visit order) places a node within its sector, so siblings of the same category fan out by
recency rather than overlapping at one angle.

This is placement/classification only -- no execution semantics. Real dilation/compression
against a live working set is Veli's job, once it exists (§5.2); this just gives every node in
a parsed tree a real, deterministic position in the box today.
"""

from __future__ import annotations

import math

from tamil_lang.mugavari import decode_morton3

# The six kernel categories (mugavari._CATEGORY's y-axis) -- one fixed angular sector each.
_SECTOR_COUNT = 6
_SECTOR_WIDTH = 2 * math.pi / _SECTOR_COUNT


def radial_position(mugavari_id: str) -> tuple[float, float]:
    """`(radius, angle_radians)` for a node -- radius = depth (`x`), angle = the node's
    category sector (`y`) plus a within-sector offset from its visit order (`z`), so nodes of
    the same category fan out rather than stacking at one angle. `z` is unbounded in principle;
    folded into `[0, 1)` via a simple decaying series (`1 - 2^-z`) so ever-later nodes approach
    the far edge of their sector without ever reaching or crossing into the next one."""
    x, y, z = decode_morton3(mugavari_id)
    radius = float(x)
    within_sector_fraction = 1.0 - (2.0**-z if z > 0 else 0.0)
    angle = (y * _SECTOR_WIDTH) + (within_sector_fraction * _SECTOR_WIDTH)
    return radius, angle


def classify_tier(radius: float, max_radius: int, *, hot_fraction: float = 0.2) -> str:
    """`"hot"` if a node's radius is within `hot_fraction` of the box's own `max_radius`,
    `"cold"` otherwise -- §5.2's "hot AST arena near the centre" vs. "cold weight pool at the
    rim," as an honest first classification (a fixed fraction, not yet the real dilate/
    compress/contract state machine, which needs Veli's live working set to react to).

    `max_radius` is a DEPTH bound, deliberately distinct from `elastic_box`'s `V_max` (a node-
    *count* ceiling) -- radius and working-set size are different units of the same box, not
    interchangeable. Conflating them would be a real correctness bug, not a simplification."""
    hot_radius = max_radius * hot_fraction
    return "hot" if radius <= hot_radius else "cold"


__all__ = ["classify_tier", "radial_position"]
