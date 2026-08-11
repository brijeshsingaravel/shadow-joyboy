"""kollan_strings.py -- G4: materializes a goal's/fn's string literals into real memory, the
closed-tree half of strings (mirrors `kollan_arrays.py`/`kollan_records.py` exactly).

`tamil_lang.nadi.nadi_strings` is pure IR analysis (name -> declared literal UTF-8 text, no
allocation). `compile_goal`/`compile_fndef` themselves never allocate (D58); this module allocates
+populates the real UTF-8 bytes via a real `BumpAllocator` and hands back the real base addresses
`string_addresses` expects -- the same "resolved-address-in" shape `array_addresses`/
`record_addresses` already have. A string is a `(pointer, length)` slice (Zig `[]const u8` / Rust
`&str` precedent) -- only the pointer is held in a local slot (`compile_goal`); the length is
recoverable from the SAME bytes this module writes (`len(text.encode())`), never stored at
runtime.
"""

from __future__ import annotations

import ctypes

from tamil_lang.ast import FnDef, Goal
from tamil_lang.nadi import lower_to_nadi, nadi_strings

from madras.dsl.kollan_allocator import BumpAllocator


def materialize_strings(program: Goal | FnDef, allocator: BumpAllocator) -> dict[str, int]:
    """Allocate + populate every string `program` (a `Goal` or `FnDef`, G1) declares (via
    `nadi_strings`, read from the lowered module) into `allocator`, returning name -> real base
    address -- ready to pass straight into `compile_goal`/`compile_fndef`'s `string_addresses`
    parameter. Each literal is
    UTF-8 encoded (a real correctness choice for `.tamil` itself, not ASCII-only) and written as
    real bytes through the allocator's own returned pointer, not simulated."""
    addresses: dict[str, int] = {}
    for name, text in nadi_strings(lower_to_nadi(program)).items():
        encoded = text.encode("utf-8")
        base = allocator.alloc(len(encoded))
        ctypes.memmove(base, encoded, len(encoded))
        addresses[name] = base
    return addresses


__all__ = ["materialize_strings"]
