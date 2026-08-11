"""kollan_cache.py -- T8.17: a fixed-capacity, no-eviction capability-call result cache, keyed by
Mugavari ID, the first real memoization behind a cached `Bind`.

Each distinct Mugavari ID (a stable, per-call-site address, `tamil_lang.mugavari.assign_ids`) gets
its own real 8-byte arena slot the first time it's seen, built on the SAME `BumpAllocator` (T8.12)
every other real allocation in Kollan already uses. A slot's initial bytes are already zero
(`BumpAllocator`'s own backing buffer is zero-initialized at construction, and bump allocation
never reuses a region once handed out) -- exactly the `(populated=0, value=0)` "empty" shape
`compile_goal`'s cached-Bind stencil expects, with no explicit initialization write needed.

**N2 -- a cached call WITH arguments needs a different shape, not the single 8-byte slot above.**
It gets a small open-addressed hash table instead (`kollan/__init__.py`'s `_emit_cached_call_with_
arg`, D77): capacity 4, 16 bytes/slot (`[key:int64][value:int64]`), content-addressed by the
call's argument VALUE rather than just its call-site position -- the real fix for a LIVE-CONFIRMED
bug (`tests/test_dsl/test_kollan_cache_loop_risk.py`): a cached call inside a `Loop`, argument
varying per iteration, silently returned stale data under the position-only scheme. Unlike the
8-byte slot, this table's KEY fields are NOT safely zero-initialized -- the probe's empty-slot
sentinel is `-1`, not `0` (0 is itself a common, real argument value, e.g. a loop's first index),
so every key field must be EXPLICITLY written to -1 here, once, the first time each such Mugavari
ID is seen (bump allocation never reuses a region, so this only ever runs once per ID, same
"first sight" timing the 8-byte path already uses).

v0 scope, stated plainly: fixed capacity, no eviction (a real LRU/TTL policy is a separate, later
increment); one cache instance is meant to outlive many separate `compile_goal`/execution calls
(created once, e.g. at agent startup, reused across every goal the agent compiles) -- that's what
makes the memoization actually save real work across an agent's lifetime, not just within one
compiled function's own execution.
"""

from __future__ import annotations

import ctypes

from tamil_lang.ast import Goal

# Cache keys come from the IR (s59 addressing row): the key a slot is stored under is the
# lowered module's Mugavari address, not the AST node's. Same contract -- deterministic and
# distinct per call site -- proven directly in tests/test_tamil_lang/test_mugavari_on_nadi.py.
from tamil_lang.nadi import (
    assign_mugavari_ids,
    lower_to_nadi,
    nadi_cached_binds,
    nadi_cached_binds_with_args,
)

from madras.dsl.kollan_allocator import BumpAllocator

_CACHE_ENTRY_SIZE = 8  # one packed (populated << 32) | value int64 per Mugavari ID (no-args path)

# N2 -- must match `_CACHE_PROBE_CAPACITY` (kollan/__init__.py) and the 16-byte slot layout
# `_emit_cached_call_with_arg`'s stencils assume ([key:int64][value:int64], -1 = empty).
_PROBE_CAPACITY = 4
_PROBE_SLOT_SIZE = 16
_PROBE_TABLE_SIZE = _PROBE_CAPACITY * _PROBE_SLOT_SIZE
_EMPTY_SENTINEL = -1


class ResultCache:
    """A real, persistent capability-call result cache. Create ONE instance and reuse it across
    every `compile_goal` call whose cached `Bind`s should share memoization -- a fresh instance
    starts every call site as a cache miss again."""

    def __init__(self, capacity: int) -> None:
        self._allocator = BumpAllocator(capacity)
        self._slots: dict[str, int] = {}

    def resolve(self, goal: Goal) -> dict[str, int]:
        """For every Mugavari ID `collect_cached_binds(goal)` finds, ensure it has a real arena
        slot (allocating one on first sight; reusing the same address on every later call,
        including across different `Goal` objects/compilations), and return ID -> address, ready
        to pass straight into `compile_goal`'s `cache_addresses` parameter."""
        module = lower_to_nadi(goal)
        assign_mugavari_ids(module)
        with_args = nadi_cached_binds_with_args(module)
        addresses: dict[str, int] = {}
        for mugavari_id in nadi_cached_binds(module):
            if mugavari_id not in self._slots:
                if mugavari_id in with_args:
                    addr = self._allocator.alloc(_PROBE_TABLE_SIZE)
                    # 8 int64 fields total (4 slots * [key, value]) -- indexing `fields[i*2]`
                    # for i in 0..capacity-1 must stay within THIS array's real bounds, not
                    # `_PROBE_CAPACITY` alone (a real off-by-half bug caught before it ran).
                    fields = (ctypes.c_int64 * (_PROBE_CAPACITY * 2)).from_address(addr)
                    for i in range(_PROBE_CAPACITY):
                        fields[i * 2] = _EMPTY_SENTINEL  # each slot's KEY is the even int64
                    self._slots[mugavari_id] = addr
                else:
                    self._slots[mugavari_id] = self._allocator.alloc(_CACHE_ENTRY_SIZE)
            addresses[mugavari_id] = self._slots[mugavari_id]
        return addresses


__all__ = ["ResultCache"]
