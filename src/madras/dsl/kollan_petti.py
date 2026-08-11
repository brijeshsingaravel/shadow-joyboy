"""kollan_petti.py -- G6 (plan-local D70): Petti's v0 arena tiers, the closed-tree half of "moves
+ arenas" (stage 1 of D70's staged ownership model; borrows/lifetimes are later stages, not this
one). A Goal's/FnDef's own materialization-time allocations (records/strings/lists/maps, built by
`kollan_records.py`/`kollan_strings.py`/`kollan_collections.py` BEFORE `compile_goal`/
`compile_fndef` ever run, D58) are scoped to one tier -- a checkpoint/rewind pair on the SAME flat
`BumpAllocator` (region-based reclaim, the ML-family/Cyclone region-inference precedent), closed
for REAL (the bump pointer is rewound, a genuine bulk free) once `tamil_lang.kollan.check_moves`
(a real use-after-move compile error, not assumed away) AND a conservative escape check both
confirm nothing allocated in this tier can still be reachable from outside it.

v0 scope, deliberately narrow and disclosed: this only reclaims MATERIALIZATION-TIME allocations
(the literal-construction chains G3/G4/G5 already build before execution) -- it does NOT yet
reclaim memory a `push`/`mapset` allocates DURING execution (G5's real runtime growth), since that
would need real machine-code save/restore of the arena's own offset cell at fn entry/exit, a
genuinely separate (and bigger) codegen problem, real future work, not attempted here. The escape
check itself is deliberately CONSERVATIVE, not precise: any `Return` or any `Call`/`Bind`-call
with arguments anywhere in the program marks the WHOLE tier as escaped (never reclaimed) even if
the specific values involved were never structural (list/map/record/string) ones -- over-
approximating on the side of safety (never wrongly reclaim) rather than risk a precise-but-buggy
analysis producing a use-after-reclaim crash, which this project has no GC/runtime safety net to
catch.
"""

from __future__ import annotations

from tamil_lang.ast import Bind, Branch, Call, FnDef, Goal, Loop, Return, Statement
from tamil_lang.kollan import check_moves

from madras.dsl.kollan_allocator import BumpAllocator


def _stmts_escape(stmts: list[Statement]) -> bool:
    for stmt in stmts:
        if isinstance(stmt, Return):
            return True
        if isinstance(stmt, Call) and stmt.args:
            return True
        if isinstance(stmt, Bind) and isinstance(stmt.call, Call) and stmt.call.args:
            return True
        if isinstance(stmt, Branch) and (_stmts_escape(stmt.then) or _stmts_escape(stmt.otherwise)):
            return True
        if isinstance(stmt, Loop) and _stmts_escape(stmt.body):
            return True
    return False


def program_escapes(program: Goal | FnDef) -> bool:
    """True if `program` contains any `Return` or any `Call`/`Bind`-call with at least one
    argument -- either could hand a materialized value's real address to a caller/callee that
    outlives this tier. See this module's own docstring for why this is conservative, not
    precise."""
    return _stmts_escape(program.body)


def close_tier_if_unescaped(
    program: Goal | FnDef, allocator: BumpAllocator, checkpoint: int
) -> bool:
    """Run `check_moves` first (raises `UnsupportedNode` on a real use-after-move bug -- this
    must never be skipped just because a tier isn't going to be reclaimed) then, ONLY if
    `program_escapes` says nothing could reach outside this scope, rewind `allocator` back to
    `checkpoint` -- a real reclaim. Returns True iff the tier was actually closed/reclaimed."""
    check_moves(program)
    if program_escapes(program):
        return False
    allocator.close_tier(checkpoint)
    return True


__all__ = ["close_tier_if_unescaped", "program_escapes"]
