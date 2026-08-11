"""kollan_records.py -- G3: materializes a goal's/fn's record literals into real memory, the
closed-tree half of records (mirrors `kollan_arrays.py` exactly).

`tamil_lang.nadi.nadi_records` is pure IR analysis (name -> declared literal fields, in
declaration order, no allocation). `compile_goal`/`compile_fndef` themselves never allocate (D58);
this module is the missing middle step: allocate+populate real memory via a real `BumpAllocator`,
laying out `[field0, field1, ..., fieldN-1, checksum]` -- the checksum is a real XOR over the N
field values, computed here at materialization time and re-verified at READ time by a `verified
field` access's own compiled code (double-entry bookkeeping's own built-in cross-check, Pacioli
1494 -- a construction-time integrity signal, not a security boundary).
"""

from __future__ import annotations

import ctypes
from functools import reduce

from tamil_lang.ast import FnDef, Goal
from tamil_lang.nadi import lower_to_nadi, nadi_records

from madras.dsl.kollan_allocator import BumpAllocator

_FIELD_SIZE = 4  # each field is a real int32, matching emit_load_absolute's 4-byte read


def materialize_records(program: Goal | FnDef, allocator: BumpAllocator) -> dict[str, int]:
    """Allocate + populate every record `program` (a `Goal` or `FnDef`, G1) declares (via
    `nadi_records`) into `allocator`, returning name -> real base address -- ready to pass
    straight into `compile_goal`/`compile_fndef`'s `record_addresses` parameter. Field order
    (the byte layout) is the SAME insertion order `nadi_records` already preserves; the
    checksum word is written last, at `base + n_fields * 4`."""
    addresses: dict[str, int] = {}
    for name, fields in nadi_records(lower_to_nadi(program)).items():
        values = list(fields.values())
        base = allocator.alloc((len(values) + 1) * _FIELD_SIZE)  # + 1 for the checksum word
        for i, value in enumerate(values):
            ctypes.cast(base + i * _FIELD_SIZE, ctypes.POINTER(ctypes.c_int32))[0] = value
        checksum = reduce(lambda acc, v: acc ^ v, values, 0)
        checksum_addr = base + len(values) * _FIELD_SIZE
        ctypes.cast(checksum_addr, ctypes.POINTER(ctypes.c_int32))[0] = checksum
        addresses[name] = base
    return addresses


__all__ = ["materialize_records"]
