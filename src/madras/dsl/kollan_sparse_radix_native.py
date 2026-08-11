"""K-phase -- the sparse page-index, row 3: the FIRST native proof. Not the full multi-level
tree yet (that's the loop-bearing, higher-risk version, deliberately next) -- a single table,
no loop, no branch-offset risk at all, proving only the base mechanism as real x86-64: allocate
a table, write a value into one slot, read it back, exit with what was found.

Reuses `tamil_lang.kollan.emit_alloc` INLINE exactly the way `kollan_standalone.emit_arena_
contraction` already does (strip the trailing `ret`, since it's a leaf stencil with no other
control flow to preserve) -- this is a `pre_exit` fragment, run between a minimal goal's body
and `kollan_standalone`'s own exit sequence, which reads ONLY EAX (`_exit_with_result`) --
nothing else the fragment touches (RBX/RCX/table-internal state) is read afterward.

Register choice deliberately avoids RSP/R12 as a base register for the slot address -- x86-64
requires a SIB byte whenever RSP or R12 is the base of a memory operand, which would have
complicated the encoding for no reason here; RBX has no such requirement.

Known, deliberate limitation of THIS row: the slot index is fixed to a small compile-time
constant so its byte offset (index*8) fits in a signed 8-bit displacement (max +127, so index
<= 15). The real multi-level tree (arbitrary index 0..255, offset up to 2040) needs a 32-bit
displacement instead -- a real, flagged difference for the next row, not silently assumed away.
"""

from __future__ import annotations

import struct

from tamil_lang.kollan import emit_alloc

_TABLE_BYTES = 2048  # RADIX_BITS=8 -> 256 slots * 8 bytes (kollan_sparse_index.RADIX_BITS)
_MAX_DISP8_INDEX = 15  # index*8 must fit in a signed 8-bit displacement (-128..127)


def emit_single_level_radix_proof(
    value: int, index: int, *, arena_base_addr: int, offset_addr: int
) -> bytes:
    """`pre_exit` fragment: allocate one 256-slot table in the SAME arena the surrounding
    standalone program already uses, write `value` into slot `index`, read it back into RAX --
    which becomes the exit code once `kollan_standalone`'s own exit sequence runs next.
    Correct if the exit code equals `value` (`0 <= value < 256`, since exit codes are the low
    byte only -- an OS truncation, not a limitation of this stencil)."""
    if not (0 <= index <= _MAX_DISP8_INDEX):
        raise ValueError(f"index {index} needs a disp8 offset (index*8); max is {_MAX_DISP8_INDEX}")
    if not (0 <= value < 256):
        raise ValueError(f"value {value} won't survive the OS's exit-code truncation to one byte")

    disp8 = index * 8

    mov_edi_size = b"\xbf" + struct.pack("<I", _TABLE_BYTES)  # size arg for emit_alloc (sysv=EDI)
    alloc = emit_alloc("x86_64", arena_base_addr, offset_addr, "sysv")[:-1]  # strip trailing `ret`
    mov_rbx_rax = b"\x48\x89\xc3"  # mov rbx, rax -- save the table's real address
    mov_rcx_value = b"\x48\xb9" + struct.pack("<Q", value)  # mov rcx, imm64
    write_slot = b"\x48\x89\x4b" + bytes([disp8])  # mov [rbx+disp8], rcx  (the INSERT)
    read_slot = b"\x48\x8b\x43" + bytes([disp8])  # mov rax, [rbx+disp8]  (the LOOKUP)

    return mov_edi_size + alloc + mov_rbx_rax + mov_rcx_value + write_slot + read_slot


__all__ = ["emit_single_level_radix_proof"]
