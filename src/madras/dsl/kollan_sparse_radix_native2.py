"""K-phase -- the sparse page-index, row 4a of the native version: multi-level CHAINING,
isolated from the (separate, harder) conditional-allocate branch -- see this module's test
file for the full reasoning behind that split.

Fixed at 2 levels (key_bits=16). ALWAYS allocates both tables fresh -- a DELIBERATE, EXPLICIT
limitation: this does not yet handle a second insert sharing the same top-level prefix
correctly (it would re-allocate the child table and lose the first insert's data). Closing
that gap is row 4b's job (a real conditional branch), kept separate on purpose.

Registers: RBX (root table addr), RDX (child table addr), RCX (the value) -- none of these are
RSP/R12, so no SIB byte is ever needed even with a disp32 memory operand (the same reason row 3
picked RBX/RCX). `emit_alloc` only touches RAX/R9/R10/R11, so RBX/RDX/RCX all survive it intact.
"""

from __future__ import annotations

import struct

from tamil_lang.kollan import emit_alloc

_TABLE_BYTES = 2048  # RADIX_BITS=8 -> 256 slots * 8 bytes (kollan_sparse_index.RADIX_BITS)
_KEY_BITS = 16


def emit_two_level_radix_proof(
    key: int, value: int, *, arena_base_addr: int, offset_addr: int
) -> bytes:
    """`pre_exit` fragment: allocate a root table, allocate a second (child) table, write the
    child's real address into the root's slot for `key`'s top byte, write `value` into the
    child's slot for `key`'s bottom byte -- then RE-READ both slots from scratch (not reusing
    a live register) to prove a genuine two-hop pointer chase, not a register-caching artifact.
    The final read lands in RAX, which becomes the exit code. Correct if the exit code equals
    `value` (`0 <= value < 256`, an OS exit-code truncation, not a stencil limitation)."""
    if not (0 <= key < (1 << _KEY_BITS)):
        raise ValueError(f"key {key} does not fit in {_KEY_BITS} bits")
    if not (0 <= value < 256):
        raise ValueError(f"value {value} won't survive the OS's exit-code truncation to one byte")

    chunk0 = (key >> 8) & 0xFF  # root-level chunk: key's top byte
    chunk1 = key & 0xFF  # child-level chunk: key's bottom byte

    mov_edi_size = b"\xbf" + struct.pack("<I", _TABLE_BYTES)  # size arg for emit_alloc (sysv=EDI)
    alloc = emit_alloc("x86_64", arena_base_addr, offset_addr, "sysv")[:-1]  # strip trailing `ret`

    alloc_root = mov_edi_size + alloc  # RAX = root table's real address
    save_root = b"\x48\x89\xc3"  # mov rbx, rax
    #   REX.W(48) + 89 /r (MOV r/m64,r64): reg=rax(000)=source, rm=rbx(011)=dest, mod=11
    #   -> 11 000 011 = 0xC3.

    alloc_child = mov_edi_size + alloc  # RAX = child table's real address (a fresh allocation)
    save_child = b"\x48\x89\xc2"  # mov rdx, rax
    #   reg=rax(000)=source, rm=rdx(010)=dest, mod=11 -> 11 000 010 = 0xC2.

    mov_rcx_value = b"\x48\xb9" + struct.pack("<Q", value)  # mov rcx, imm64

    write_slot0 = b"\x48\x89\x93" + struct.pack("<i", chunk0 * 8)  # mov [rbx+disp32], rdx
    #   reg=rdx(010)=source, rm=rbx(011)=base, mod=10(disp32) -> 10 010 011 = 0x93.
    write_slot1 = b"\x48\x89\x8a" + struct.pack("<i", chunk1 * 8)  # mov [rdx+disp32], rcx
    #   reg=rcx(001)=source, rm=rdx(010)=base, mod=10 -> 10 001 010 = 0x8A.

    reload_child = b"\x48\x8b\x83" + struct.pack("<i", chunk0 * 8)  # mov rax, [rbx+disp32]
    #   reg=rax(000)=dest, rm=rbx(011)=base, mod=10 -> 10 000 011 = 0x83.
    read_value = b"\x48\x8b\x80" + struct.pack("<i", chunk1 * 8)  # mov rax, [rax+disp32]
    #   reg=rax(000)=dest, rm=rax(000)=base, mod=10 -> 10 000 000 = 0x80. (rax as both base and
    #   destination is well-defined: the memory read uses rax's OLD value as the address, THEN
    #   the result overwrites rax.)

    return (
        alloc_root
        + save_root
        + alloc_child
        + save_child
        + mov_rcx_value
        + write_slot0
        + write_slot1
        + reload_child
        + read_value
    )


__all__ = ["emit_two_level_radix_proof"]
