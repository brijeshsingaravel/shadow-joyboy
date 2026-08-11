"""The elastic box -- `V_max`, the RAM/VRAM ceiling (RFC-0002 §5.1 "Bounded" law, §5.2).

Founder's own framing (s55): `.tamil`'s "venv" is a bounded, radial box -- every node's depth
from the origin (the user's goal) is its `x` axis (Mugavari, §4.2), and the box's radius is
capped at `V_max`. This is the first, honest increment of that: a pure working-set-size check
over an ID-assigned tree, not yet the full dilate/compress/contract state machine (§5.2's three
phases need Veli's actual runtime to exist first) -- but real and enforceable today, both here
(the open front-end) and wherever a caller wires it in (the closed interpreter/compiler).
"""

from __future__ import annotations

from tamil_lang.ast import Bind, Call, FnDef, Goal, Recall, Remember, _Node
from tamil_lang.mugavari import decode_morton3


class UnassignedMugavariId(ValueError):
    """A node has no `mugavari_id` yet -- `assign_ids()` must run before a box check, since
    depth/working-set size are only knowable once every node has its address."""


def decode_depth(mugavari_id: str) -> int:
    """Recover a node's `x` axis (depth from the origin) from its own Mugavari ID -- a thin
    convenience wrapper over `mugavari.decode_morton3`, the one shared decode implementation."""
    x, _y, _z = decode_morton3(mugavari_id)
    return x


def _all_nodes(node: _Node) -> list[_Node]:
    """Every node reachable from `node` -- the same real child slots Mugavari's own
    `assign_ids()` walks (Goal.body/FnDef.body (G1)/Branch.then+otherwise/Loop.body/Bind.call/
    Call.args/Remember.value), not a subset. `Return` (G1) is a leaf here -- its `value` is a
    `str` in v0 scope (a bound name or literal, `compile_fndef`'s own restriction), never a
    `Recall`, so there's nothing further to descend into yet."""
    # A grouping helper (today only `MatchArm`, G9) holds statements but is not one -- it has no
    # kernel `kind`, gets no Mugavari address, and so must not occupy space in the box either.
    # Recursion passes through it; only real kernel nodes are counted.
    nodes = [node] if getattr(node, "kind", None) is not None else []
    # Statement bodies, followed by FIELD NAME rather than an isinstance ladder (fixed s59).
    # The previous version enumerated Goal/FnDef/Branch/Loop explicitly and so silently skipped
    # `Match.arms` (G9) and `Parallel.body` (G10) as those node kinds were added -- `V_max`, the
    # §5.1 "Bounded" law, then UNDER-COUNTED: stdlib/gateway.tamil measured 4 nodes when it has
    # 10, letting an arbitrarily large program pass the ceiling. Walking by field name means a
    # future node kind carrying a body is counted without anyone remembering to edit this.
    for field in ("body", "then", "otherwise", "arms"):
        for child in getattr(node, field, None) or []:
            nodes += _all_nodes(child)
    if isinstance(node, Bind):
        nodes += _all_nodes(node.call)
    elif isinstance(node, Call):
        for arg in node.args:
            if isinstance(arg, Recall):
                nodes += _all_nodes(arg)
    elif isinstance(node, Remember) and isinstance(node.value, Recall):
        nodes += _all_nodes(node.value)
    return nodes


def working_set_size(goal: Goal | FnDef) -> int:
    """The whole tree's node count -- the box's current occupancy. Accepts a `Goal` or a `FnDef`
    (G1) -- both are depth-0 tree origins. `assign_ids()` must have run first (raises
    `UnassignedMugavariId` otherwise); a size check without real addresses would be measuring
    nothing."""
    nodes = _all_nodes(goal)
    for node in nodes:
        if node.mugavari_id is None:
            raise UnassignedMugavariId(
                "a node has no mugavari_id -- call mugavari.assign_ids(goal) before checking "
                "the box"
            )
    return len(nodes)


def fits_in_box(goal: Goal | FnDef, v_max: int) -> bool:
    """Does this program's whole working set fit under the `V_max` ceiling? The first,
    whole-tree increment of the elastic box (§5.2) -- hot/cold-tier partitioning by depth is
    the next increment, once there's an actual runtime (Veli) to dilate/compress/contract
    against, not just a static tree."""
    return working_set_size(goal) <= v_max


__all__ = ["UnassignedMugavariId", "decode_depth", "fits_in_box", "working_set_size"]
