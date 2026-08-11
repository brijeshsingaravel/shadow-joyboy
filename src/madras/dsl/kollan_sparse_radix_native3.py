"""K-phase -- the sparse page-index, row 4b of the native version: the conditional
allocate-if-empty branch, closing the gap row 4a deliberately left open (row 4a always
allocated a fresh child table, which would silently lose data on a second insert sharing the
same top-level prefix).

Every new opcode this row needs (`test`, `jnz`, the R8 register, `shl`) was cross-checked
against a real GNU assembler (WSL `as` + `objdump`) before being trusted here -- not derived
from memory alone, given this is exactly the class of bug (a wrong branch displacement) this
project has been bitten by before. The `jnz` displacement itself is never hand-counted: it is
computed as `len()` of the actual emitted skip-block bytes, so it is correct by construction.
"""

from __future__ import annotations

import struct

from tamil_lang.kollan import emit_alloc

_TABLE_BYTES = 2048  # RADIX_BITS=8 -> 256 slots * 8 bytes (kollan_sparse_index.RADIX_BITS)
_MAX_CHECKSUM_VALUE = 15  # value*16 + value must fit in one exit-code byte (0..255)


def _emit_ensure_child(chunk0: int, arena_base_addr: int, offset_addr: int) -> bytes:
    """Read the root table's slot for `chunk0`; if it already holds a real address (a prior
    insert touched this prefix), skip allocation entirely -- RAX already holds the correct
    child address. Otherwise allocate a fresh child table and store its address into the slot.
    RAX holds the (reused-or-fresh) child address either way once this block finishes."""
    read_slot0 = b"\x48\x8b\x83" + struct.pack("<i", chunk0 * 8)  # mov rax, [rbx+disp32]
    #   reg=rax(000)=dest, rm=rbx(011)=base, mod=10(disp32) -> 10 000 011 = 0x83.
    test_rax = b"\x48\x85\xc0"  # test rax, rax -- ZF=1 iff rax==0 (never touched)
    #   verified against a real assembler: `test rax,rax` -> 48 85 c0.

    mov_edi_size = b"\xbf" + struct.pack("<I", _TABLE_BYTES)  # size arg for emit_alloc (EDI)
    alloc = emit_alloc("x86_64", arena_base_addr, offset_addr, "sysv")[:-1]  # strip trailing `ret`
    write_slot0 = b"\x48\x89\x83" + struct.pack("<i", chunk0 * 8)  # mov [rbx+disp32], rax
    #   reg=rax(000)=source, rm=rbx(011)=base, mod=10 -> 10 000 011 = 0x83 (same modrm as the
    #   read above -- this opcode, 0x89, is "store", the read above used 0x8B, "load").
    alloc_block = mov_edi_size + alloc + write_slot0

    # jnz rel8: skip `alloc_block` entirely if the slot was already non-empty. The displacement
    # is exactly len(alloc_block) -- verified against a real assembler on a matching example
    # (a 12-byte skipped block produced rel8=0x0C, the identical formula used here -- never
    # hand-counted).
    jnz_skip_alloc = b"\x75" + bytes([len(alloc_block)])

    return read_slot0 + test_rax + jnz_skip_alloc + alloc_block


def emit_prefix_reuse_proof(
    top_byte: int,
    bottom_a: int,
    value_a: int,
    bottom_b: int,
    value_b: int,
    *,
    arena_base_addr: int,
    offset_addr: int,
) -> bytes:
    """`pre_exit` fragment: two inserts sharing the SAME top-level prefix (`top_byte`) -- the
    second insert's `_emit_ensure_child` call must take the "reuse" branch, not re-allocate,
    or the first insert's value would be silently lost. Both values are re-read from scratch
    afterward (not from a live register) and combined as `value_a*16 + value_b`, which becomes
    the exit code -- correct only if BOTH inserts survived intact. `value_a`/`value_b` must
    each be `0..15` so the combined checksum fits in one exit-code byte."""
    if not (0 <= value_a <= _MAX_CHECKSUM_VALUE and 0 <= value_b <= _MAX_CHECKSUM_VALUE):
        raise ValueError(f"value_a/value_b must each be 0..{_MAX_CHECKSUM_VALUE}")

    mov_edi_size = b"\xbf" + struct.pack("<I", _TABLE_BYTES)
    alloc = emit_alloc("x86_64", arena_base_addr, offset_addr, "sysv")[:-1]

    alloc_root = mov_edi_size + alloc  # RAX = root table's real address
    save_root = b"\x48\x89\xc3"  # mov rbx, rax -- root persists for the whole program

    # --- insert A: ensures the child table exists (fresh, since the root is brand new) ---
    ensure_child_a = _emit_ensure_child(top_byte, arena_base_addr, offset_addr)
    save_child_a = b"\x48\x89\xc2"  # mov rdx, rax
    mov_rcx_value_a = b"\x48\xb9" + struct.pack("<Q", value_a)
    write_a = b"\x48\x89\x8a" + struct.pack("<i", bottom_a * 8)  # mov [rdx+disp32], rcx

    # --- insert B: SAME top_byte -- _emit_ensure_child's branch must be TAKEN this time ---
    ensure_child_b = _emit_ensure_child(top_byte, arena_base_addr, offset_addr)
    save_child_b = b"\x48\x89\xc2"  # mov rdx, rax
    mov_rcx_value_b = b"\x48\xb9" + struct.pack("<Q", value_b)
    write_b = b"\x48\x89\x8a" + struct.pack("<i", bottom_b * 8)

    # --- lookup both, re-derived from scratch each time (never reused from a live register) ---
    reload_child_for_a = b"\x48\x8b\x83" + struct.pack("<i", top_byte * 8)  # mov rax,[rbx+disp32]
    read_a = b"\x48\x8b\x80" + struct.pack("<i", bottom_a * 8)  # mov rax, [rax+disp32]
    save_a = b"\x49\x89\xc0"  # mov r8, rax -- verified: 49 89 c0

    reload_child_for_b = b"\x48\x8b\x83" + struct.pack("<i", top_byte * 8)
    read_b = b"\x48\x8b\x80" + struct.pack("<i", bottom_b * 8)  # RAX = value_b

    # combined = value_a*16 + value_b
    mov_rcx_a = b"\x4c\x89\xc1"  # mov rcx, r8 -- verified: 4c 89 c1
    shl_rcx_4 = b"\x48\xc1\xe1\x04"  # shl rcx, 4 -- verified: 48 c1 e1 04
    add_rax_rcx = b"\x48\x01\xc8"  # add rax, rcx -- verified: 48 01 c8

    return (
        alloc_root
        + save_root
        + ensure_child_a
        + save_child_a
        + mov_rcx_value_a
        + write_a
        + ensure_child_b
        + save_child_b
        + mov_rcx_value_b
        + write_b
        + reload_child_for_a
        + read_a
        + save_a
        + reload_child_for_b
        + read_b
        + mov_rcx_a
        + shl_rcx_4
        + add_rax_rcx
    )


__all__ = ["emit_prefix_reuse_proof"]
