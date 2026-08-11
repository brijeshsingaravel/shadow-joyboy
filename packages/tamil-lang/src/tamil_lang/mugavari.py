"""Mugavari (முகவரி, "address") -- the Morton-coded materialized-path node ID (RFC-0002 §4.2).

Not a UUID: origin-rooted (the root is the user's goal), hierarchical (the ID prefix IS the
parent's ID), and 3-D-native. A node's ID = `parent-prefix · Morton(x,y,z) · content-hash`,
where the three axes carry both meaning and locality (RFC-0002 §4.2, founder s54):

    x = depth from the user's goal (origin)      -- doubles as the hot<->cold memory tier
    y = faculty / category (the six kernel kinds) -- category (lexicon metadata)
    z = order / time (event sequence)             -- recency

Assigned by `assign_ids()`, a post-parse tree walk -- depth/order are only knowable once the
full tree exists, not during Lark's bottom-up transform (`kural.py`), so every node's
`mugavari_id` is `None` until this runs.
"""

from __future__ import annotations

import hashlib

from tamil_lang.ast import Bind, Call, FnDef, Goal, Recall, Remember, _Node

# The six kernel kinds -- the y axis (faculty/category), fixed by the base-6 kernel (D50/D60),
# never grown here; new capabilities are catalog entries, not new categories.
_CATEGORY: dict[str, int] = {
    "goal": 0,
    "governance-check": 1,
    "capability-call": 2,
    "compose-bind": 3,
    "memory-ref": 4,
    "control-flow": 5,
}


# Shared with elastic_box.py's depth decoder -- must match _morton3's default exactly.
MORTON_BITS = 10


def _morton3(x: int, y: int, z: int, bits: int = MORTON_BITS) -> int:
    """3-D Morton (Z-order) code -- bit-interleave x/y/z, `bits` bits each (bit-width-agnostic
    per §4.2: truncating the code = zooming to a coarser cell)."""
    m = 0
    for i in range(bits):
        m |= ((x >> i) & 1) << (3 * i)
        m |= ((y >> i) & 1) << (3 * i + 1)
        m |= ((z >> i) & 1) << (3 * i + 2)
    return m


def decode_morton3(mugavari_id: str, bits: int = MORTON_BITS) -> tuple[int, int, int]:
    """Recover `(x, y, z)` from a node's own Mugavari ID -- the exact inverse of `_morton3`'s
    bit-interleave. `bits` must match the value `_morton3` was called with, or the decode is
    silently wrong (not an exception) -- kept as one function so `elastic_box.py`/`radial.py`
    never re-derive this arithmetic independently."""
    last_segment = mugavari_id.rsplit("/", 1)[-1]
    morton_hex = last_segment.split("-", 1)[0]
    morton = int(morton_hex, 16)
    x = y = z = 0
    for i in range(bits):
        x |= ((morton >> (3 * i)) & 1) << i
        y |= ((morton >> (3 * i + 1)) & 1) << i
        z |= ((morton >> (3 * i + 2)) & 1) << i
    return x, y, z


def _content_hash(node: _Node) -> str:
    """Hash of the node's own content, computed BEFORE recursing into children (so a parent's
    hash reflects its own fields, not descendant IDs which don't exist yet at this point)."""
    payload = node.model_dump_json(exclude={"mugavari_id"})
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _assign(node: _Node, parent_prefix: str, depth: int, counter: list[int]) -> None:
    # A GROUPING HELPER (today only `MatchArm`, G9) carries no kernel `kind` and therefore has
    # no category in the frozen 6-entry `_CATEGORY` -- it is scaffolding that holds statements,
    # not a statement itself. Recursion passes THROUGH it at the same depth and prefix, so the
    # statements inside are addressed while the helper is not. Detected by absence of `kind`
    # rather than by name, so a future grouping node needs no edit here.
    if not isinstance(node, FnDef) and getattr(node, "kind", None) not in _CATEGORY:
        for field in ("body", "then", "otherwise", "arms"):
            for child in getattr(node, field, None) or []:
                _assign(child, parent_prefix, depth, counter)
        return

    # FnDef (G1) is a top-level declaration, not a kernel-kind Statement -- its own `.kind`
    # ("fn-def") isn't one of the frozen 6 categories (D50/D60: `_CATEGORY` is never grown).
    # It reuses `goal`'s category number: both are depth-0 tree origins, not kernel-kind
    # statements, so this doesn't add a 7th entry to the frozen dict.
    category = _CATEGORY["goal"] if isinstance(node, FnDef) else _CATEGORY[node.kind]  # type: ignore[attr-defined]
    order = counter[0]
    counter[0] += 1

    content_hash = _content_hash(node)
    morton = _morton3(depth, category, order)
    node.mugavari_id = f"{parent_prefix}/{morton:x}-{content_hash}"

    prefix = node.mugavari_id
    child_depth = depth + 1

    # Statement bodies, followed by FIELD NAME rather than an isinstance ladder (fixed s59).
    # The previous version enumerated Goal/FnDef/Branch/Loop explicitly and so never descended
    # into `Match.arms` (G9) or `Parallel.body` (G10) as those kinds were added -- leaving 10
    # real nodes in stdlib/gateway.tamil with NO address at all, and therefore invisible to
    # depth, to locality, and to the elastic box. A `MatchArm` is a grouping helper, not a
    # kernel node (it has no `kind` in the frozen 6-entry `_CATEGORY`), so recursion passes
    # THROUGH it to the statements inside without addressing the arm itself.
    for field in ("body", "then", "otherwise", "arms"):
        for child in getattr(node, field, None) or []:
            _assign(child, prefix, child_depth, counter)
    if isinstance(node, Bind):
        _assign(node.call, prefix, child_depth, counter)
    elif isinstance(node, Call):
        for arg in node.args:
            if isinstance(arg, Recall):
                _assign(arg, prefix, child_depth, counter)
    elif isinstance(node, Remember):
        if isinstance(node.value, Recall):
            _assign(node.value, prefix, child_depth, counter)
    # Govern, Recall (leaf): no further children to recurse into.


def assign_ids(goal: Goal | FnDef) -> Goal | FnDef:
    """Walk `goal`'s (or `FnDef`'s, G1) tree and assign every node's `mugavari_id` in place.
    Returns the same object (mutated) for convenient chaining after `parse()`/`parse_program()`."""
    _assign(goal, "", 0, [0])
    return goal


__all__ = ["MORTON_BITS", "assign_ids", "decode_morton3"]
