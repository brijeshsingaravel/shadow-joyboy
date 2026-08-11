"""kollan_arrays.py -- T8.13: materializes a goal's array literals into real memory, the closed-
tree half of real arrays.

`tamil_lang.nadi.nadi_arrays` is pure IR analysis (name -> declared literal elements, no
allocation). `compile_goal` itself never allocates (D58: any real memory materialization belongs
in the closed tree, never inside the open `tamil_lang.kollan` package -- confirmed this session
for exactly this reason). This module is the missing middle step: take what `nadi_arrays`
found, actually allocate+populate it via a real `BumpAllocator`, and hand back the real addresses
`compile_goal`'s `array_addresses` parameter expects -- the same "resolved-address-in" shape
`capability_addresses` already has.
"""

from __future__ import annotations

import ctypes

from tamil_lang.ast import Goal
from tamil_lang.nadi import lower_to_nadi, nadi_arrays

from madras.dsl.kollan_allocator import BumpAllocator

_ELEMENT_SIZE = 4  # each array element is a real int32, matching emit_load_absolute's 4-byte read


def materialize_arrays(goal: Goal, allocator: BumpAllocator) -> dict[str, int]:
    """Allocate + populate every array `goal` declares (via `nadi_arrays`) into `allocator`,
    returning name -> real base address -- ready to pass straight into `compile_goal`'s
    `array_addresses` parameter. Each element is written as a real int32 through the allocator's
    own returned pointer (the same live memory Kollan's compiled `emit_load_absolute` reads back
    from), not simulated."""
    addresses: dict[str, int] = {}
    for name, elements in nadi_arrays(lower_to_nadi(goal)).items():
        base = allocator.alloc(len(elements) * _ELEMENT_SIZE)
        for i, value in enumerate(elements):
            ctypes.cast(base + i * _ELEMENT_SIZE, ctypes.POINTER(ctypes.c_int32))[0] = value
        addresses[name] = base
    return addresses


__all__ = ["materialize_arrays"]
