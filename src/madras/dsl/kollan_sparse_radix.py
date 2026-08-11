"""K-phase -- the sparse page-index for Core (4D+), row 2: the arena-resident radix tree
itself. Built on `encode_morton_nd`/`decode_morton_nd` (kollan_sparse_index.py) and
`BumpAllocator` (kollan_allocator.py) -- real memory, real addresses, the SAME arena everything
else in Core allocates into.

Hosted (ctypes-driven Python walk) first, mirroring `BumpAllocator`'s own hosted-then-standalone
precedent (`kollan_allocator.py` -> `kollan_standalone.py`): the memory this tree lives in is
already real and arena-resident, exactly per the founder's "Earth" call -- what's still hosted
is only the WALK logic itself (pointer-chasing, expressed here in Python). The native x86-64
stencil version (a `.tamil` program walking this SAME structure with no host process) is the
deliberately separate next row.

A node is a flat 256-slot table (`RADIX_BITS` = 8 -> 2^8 slots, 8 bytes each = 2048 bytes), one
slot per possible value of that level's 8-bit chunk of the Morton key -- the same "flat table
per level" scheme x86-64's own multi-level page tables use (just 256-way instead of 512-way,
matching the arena's own `mmap`-backed Core, which already gets an analogous structure for the
first 3 dimensions for free from the OS -- this is that same idea, one level further, for keys
too wide to be real virtual addresses at all). A slot is EITHER `_EMPTY` (0, untouched) or a
real arena address: an inner-node table at every level but the last, the stored VALUE itself at
the last level.

New tables rely on `MAP_ANONYMOUS`'s kernel zero-fill to start all-zero (`_EMPTY` everywhere)
without an explicit memset loop -- true only because `BumpAllocator` never hands out the same
bytes twice without an explicit `reset()`/`close_tier()` (documented there, not re-derived here;
this module must never be used across a tier close without re-verifying that invariant still
holds).
"""

from __future__ import annotations

import ctypes

from madras.dsl.kollan_allocator import BumpAllocator
from madras.dsl.kollan_sparse_index import RADIX_BITS

_EMPTY = 0  # the sentinel "no entry yet" slot value -- real values must be non-zero
_SLOT_SIZE = 8
_ARITY = 1 << RADIX_BITS  # 256 slots per table
_TABLE_BYTES = _ARITY * _SLOT_SIZE  # 2048


def _read_slot(addr: int) -> int:
    return ctypes.cast(addr, ctypes.POINTER(ctypes.c_uint64))[0]


def _write_slot(addr: int, value: int) -> None:
    ctypes.cast(addr, ctypes.POINTER(ctypes.c_uint64))[0] = value


class SparseRadixIndex:
    """A sparse radix tree over Morton-coded keys, `key_bits` wide (must be a positive multiple
    of `RADIX_BITS` -- no ragged final level, kept simple for this first real version). Branches
    on the HIGHEST-order chunk first (level 0 = the topmost `RADIX_BITS` bits), so a shared
    PREFIX means a shared subtree -- exactly the property `kollan_sparse_index`'s own
    containment test proved: dropping a key's low bits walks toward the root, not away from it.
    """

    def __init__(self, allocator: BumpAllocator, key_bits: int) -> None:
        if key_bits <= 0 or key_bits % RADIX_BITS != 0:
            raise ValueError(f"key_bits ({key_bits}) must be a positive multiple of {RADIX_BITS}")
        self._allocator = allocator
        self._key_bits = key_bits
        self._levels = key_bits // RADIX_BITS
        self._root_addr = _EMPTY  # allocated lazily, on the first insert

    def _chunk(self, key: int, level: int) -> int:
        """The `level`-th chunk of `key` -- level 0 is the topmost (most-significant)
        `RADIX_BITS` bits, matching `kollan_sparse_index`'s own "drop low bits = coarser cell"
        property (dropping bits walks toward level 0, the root)."""
        shift = self._key_bits - RADIX_BITS * (level + 1)
        return (key >> shift) & (_ARITY - 1)

    def _check_key(self, key: int) -> None:
        limit = 1 << self._key_bits
        if not (0 <= key < limit):
            raise ValueError(f"key {key} does not fit in {self._key_bits} bits (0..{limit - 1})")

    def insert(self, key: int, value: int) -> None:
        self._check_key(key)
        if value == _EMPTY:
            raise ValueError("value must be non-zero -- 0 is the sentinel for 'no entry'")

        if self._root_addr == _EMPTY:
            self._root_addr = self._allocator.alloc(_TABLE_BYTES)

        node_addr = self._root_addr
        for level in range(self._levels):
            slot_addr = node_addr + self._chunk(key, level) * _SLOT_SIZE
            if level == self._levels - 1:
                _write_slot(slot_addr, value)
                return
            child = _read_slot(slot_addr)
            if child == _EMPTY:
                child = self._allocator.alloc(_TABLE_BYTES)
                _write_slot(slot_addr, child)
            node_addr = child

    def lookup(self, key: int) -> int | None:
        self._check_key(key)
        if self._root_addr == _EMPTY:
            return None

        node_addr = self._root_addr
        for level in range(self._levels):
            slot_addr = node_addr + self._chunk(key, level) * _SLOT_SIZE
            slot = _read_slot(slot_addr)
            if slot == _EMPTY:
                return None
            if level == self._levels - 1:
                return slot
            node_addr = slot
        return None  # unreachable (the loop always returns on its last iteration)


__all__ = ["SparseRadixIndex"]
