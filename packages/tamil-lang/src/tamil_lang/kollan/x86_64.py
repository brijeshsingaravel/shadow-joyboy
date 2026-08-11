"""x86-64 (Win64 ABI) stencils -- CISC, variable-length, hand-encoded per RFC-0002 §7.4's
explicit "x86-64 on the dev machine now" choice. Calling convention: first int arg in ECX,
result in EAX (Microsoft x64) -- the only host this backend's execution half proves live.
"""

from __future__ import annotations

import struct

from tamil_lang.kollan.errors import UnsupportedOp
from tamil_lang.kollan.types import Abi, Op

# setCC opcode, keyed by the comparison this stencil computes directly against `cmp ecx,imm32`
# (no operand-swapping needed).
_SETCC: dict[Op, bytes] = {
    ">=": b"\x0f\x9d\xc0",  # setge al
    ">": b"\x0f\x9f\xc0",  # setg al
    "<=": b"\x0f\x9e\xc0",  # setle al
    "<": b"\x0f\x9c\xc0",  # setl al
    "==": b"\x0f\x94\xc0",  # sete al
    "!=": b"\x0f\x95\xc0",  # setne al
}

# The short-form Jcc opcode (0x70-0x7F range) for the INVERSE of each op -- "jump to the
# else-block when the comparison is FALSE", the standard compile-a-branch pattern (test the
# negation, fall through on true) every real compiler uses instead of computing a boolean first.
_JCC_ELSE: dict[Op, int] = {
    ">=": 0x7C,  # jl  (else if <)
    ">": 0x7E,  # jle (else if <=)
    "<=": 0x7F,  # jg  (else if >)
    "<": 0x7D,  # jge (else if >=)
    "==": 0x75,  # jne (else if !=)
    "!=": 0x74,  # je  (else if ==)
}


# G2: the first-int-arg register differs by OS ABI, not by CPU -- Win64 (Windows) passes it in
# ECX, System V (Linux/macOS) in EDI. `cmp <reg>, imm32`'s opcode is identical (0x81); only the
# ModRM byte's register field differs (0xf9=ECX vs 0xff=EDI).
_CMP_IMM32_OPCODE: dict[Abi, bytes] = {"win64": b"\x81\xf9", "sysv": b"\x81\xff"}


def emit_govern_check(op: Op, level: int, abi: Abi = "win64") -> bytes:
    """`int check(int rank)` -> 1 if `rank <op> level` else 0. Fixed byte template, one 4-byte
    patched hole (the comparison immediate) -- copy-and-patch, not a lowering pass. `abi` (G2)
    selects which register the incoming `rank` argument arrives in (Win64: ECX, SysV: EDI) --
    the OS calling convention, not the CPU ISA."""
    if op not in _SETCC:
        raise UnsupportedOp(f"no x86_64 stencil for operator {op!r} -- supported: {sorted(_SETCC)}")
    cmp_imm32 = _CMP_IMM32_OPCODE[abi] + struct.pack("<i", level)
    setcc_al = _SETCC[op]
    movzx_eax_al = b"\x0f\xb6\xc0"  # movzx eax, al
    ret = b"\xc3"
    return cmp_imm32 + setcc_al + movzx_eax_al + ret


# G2: the 3-int-arg register convention differs by OS ABI -- Win64: cond/a/b in ECX/EDX/R8D;
# System V: cond/a/b in EDI/ESI/EDX. `_TEST_ARG1_ARG1` = `test <arg1>,<arg1>`; `_MOV_EAX_ARG2` =
# `mov eax,<arg2>` (the then-block, a0=a); `_MOV_EAX_ARG3` = `mov eax,<arg3>` (the else-block,
# a0=b) -- note SysV's arg3 (EDX) needs no REX prefix, unlike Win64's R8D, so the two variants
# are genuinely different LENGTHS; every offset below is computed from real byte lengths, not
# hardcoded, so this is handled automatically.
_TEST_ARG1_ARG1: dict[Abi, bytes] = {"win64": b"\x85\xc9", "sysv": b"\x85\xff"}
_MOV_EAX_ARG2: dict[Abi, bytes] = {"win64": b"\x89\xd0", "sysv": b"\x89\xf0"}
_MOV_EAX_ARG3: dict[Abi, bytes] = {"win64": b"\x44\x89\xc0", "sysv": b"\x89\xd0"}


def emit_branch(abi: Abi = "win64") -> bytes:
    """Iter3 -- the `control-flow` (branch) stencil: `int branch(int cond, int a, int b)` ->
    `a` if `cond != 0` else `b`. `abi` (G2) selects Win64 (cond/a/b in ECX/EDX/R8D) vs System V
    (cond/a/b in EDI/ESI/EDX), result in EAX either way. Two real basic blocks (then/else)
    joined by a conditional (`jz`) and unconditional (`jmp`) short jump -- both rel8 deltas
    computed as plain arithmetic on each block's real byte length, not memorized offsets."""
    test_ecx_ecx = _TEST_ARG1_ARG1[abi]
    then_block = _MOV_EAX_ARG2[abi]  # a0 = a
    jump_to_end = b"\xeb\x00"  # jmp rel8 -- placeholder, patched below
    else_block = _MOV_EAX_ARG3[abi]  # a0 = b
    ret = b"\xc3"

    jz_len, then_len, jump_len = 2, len(then_block), len(jump_to_end)
    else_offset = jz_len + then_len + jump_len  # where else_block starts, from jz's end
    end_offset = else_offset + len(else_block)  # where ret starts, from jump_to_end's end

    # jz rel8 is relative to the END of the jz instruction itself, not to offset 0.
    jz_else = b"\x74" + bytes([else_offset - jz_len])
    jump_to_end = b"\xeb" + bytes([end_offset - jz_len - then_len - jump_len])

    return test_ecx_ecx + jz_else + then_block + jump_to_end + else_block + ret


def emit_branch_on_compare(op: Op, level: int, abi: Abi = "win64") -> bytes:
    """The `Expr`-lowering path into `emit_branch`'s shape: `int branch(int x, int a, int b)`
    -> `a` if `x <op> level` else `b`, fusing the comparison directly into the conditional jump
    (`cmp`+`Jcc`) instead of `emit_branch`'s separate compute-a-boolean-then-`test`-it shape --
    the idiomatic form every real x86-64 compiler emits for `if x <op> level {..} else {..}`,
    and the direct native counterpart of `tamil_lang.expr`'s `Compare(op, Name, Num)` node.
    `abi` (G2) selects the incoming 3-argument register convention, same as `emit_branch`."""
    if op not in _JCC_ELSE:
        raise UnsupportedOp(
            f"no x86_64 stencil for operator {op!r} -- supported: {sorted(_JCC_ELSE)}"
        )
    cmp_imm32 = _CMP_IMM32_OPCODE[abi] + struct.pack("<i", level)
    then_block = _MOV_EAX_ARG2[abi]  # a0 = a
    jump_to_end = b"\xeb\x00"  # jmp rel8 -- placeholder, patched below
    else_block = _MOV_EAX_ARG3[abi]  # a0 = b
    ret = b"\xc3"

    cmp_len, jcc_len = len(cmp_imm32), 2
    then_len, jump_len = len(then_block), len(jump_to_end)
    jcc_end = cmp_len + jcc_len  # where the Jcc instruction itself ends
    else_offset = jcc_end + then_len + jump_len  # where else_block starts (absolute) --
    # also where jump_to_end's own instruction ends, since else starts right after it.
    end_offset = else_offset + len(else_block)  # where ret starts (absolute)

    # Jcc/jmp rel8 is relative to the END of that instruction itself, not to offset 0.
    jcc_else = bytes([_JCC_ELSE[op]]) + bytes([else_offset - jcc_end])
    jump_to_end = b"\xeb" + bytes([end_offset - else_offset])

    return cmp_imm32 + jcc_else + then_block + jump_to_end + else_block + ret


def emit_jump(offset: int) -> bytes:
    """`jmp rel8` -- an unconditional short jump, `offset` bytes past its own end. The
    building block `compile_goal`'s recursive Branch compiler uses to skip over an else-block
    after a then-block finishes, generalizing `emit_branch`'s fixed `jmp` to an arbitrary
    (compiled, real) byte distance."""
    return b"\xeb" + struct.pack("<b", offset)


def emit_jump32(offset: int) -> bytes:
    """N2 -- `jmp rel32`: `emit_jump`'s NEAR sibling, `offset` bytes past its own end, using a
    4-byte signed displacement instead of `emit_jump`'s 1-byte one. Found NEEDED, not spec'd in
    advance: N2's cache-probe loop's own body genuinely exceeds rel8's +-127 byte range (~185
    bytes measured for one probe iteration) -- the same rel8 ceiling N1 already disclosed for a
    3-tier nested `match`, hit again here by a different construct. Opcode `0xE9` + a FIXED
    5-byte total length regardless of the offset's magnitude (unlike `emit_jump`'s dual disp8/
    disp32 branching elsewhere in this file) -- genuinely simpler to get right, not just bigger."""
    return b"\xe9" + struct.pack("<i", offset)


def emit_je32(offset: int) -> bytes:
    """N2 -- `je rel32`: `emit_je`'s NEAR sibling. Two-byte opcode `0F 84` (the near-Jcc form)
    + a 4-byte signed displacement -- a FIXED 6-byte total length."""
    return b"\x0f\x84" + struct.pack("<i", offset)


def emit_compare_and_jump_if_false(
    op: Op, level: int, skip_len: int, *, force_near: bool = False
) -> bytes:
    """`cmp eax, imm32` + `Jcc` -- jumps `skip_len` bytes past its own end (landing on the
    else-block's first byte) when `eax <op> level` is FALSE. `skip_len` is the CALLER's
    responsibility to compute as the full distance to skip -- typically
    `len(then_bytes) + len(emit_jump(...))`, i.e. the compiled then-block PLUS its own trailing
    unconditional jump over the else-block, not just the then-block alone. Also used for
    BACKWARD (negative `skip_len`) branches -- `_compile_loop`'s own back-edge test.

    This is the fragment form of `emit_branch_on_compare`'s cmp+Jcc prefix: that stencil
    compares a fixed argument in ECX and selects between two fixed values; this one compares
    whatever's already in EAX (typically just loaded from a local slot via `emit_load_local`)
    and lets the caller supply an ARBITRARY compiled then-block instead of a single `mov`,
    which is what actually makes recursive Branch compilation possible.

    `force_near` (N2, added when a large `cached`-with-args call inside a `Loop` was found to
    push a loop body's back-edge distance past rel8's +-127 range): when True, ALWAYS emits the
    NEAR Jcc form (`0F 8x` + a 4-byte rel32) instead of the short form (1-byte rel8),
    unconditionally -- a FIXED length regardless of `skip_len`'s magnitude (11 bytes vs the
    short form's 7), the same invariant `_compile_loop`'s own two-pass "measure with a
    placeholder, then build for real" scheme depends on. Deliberately NOT auto-selected by
    `skip_len`'s value the way `_local_offset`'s disp8/disp32 dual form is: the CALLER must
    decide once (using the SAME `force_near` for both its placeholder-length measurement and
    its real build) and get it right, because letting this function silently pick a different
    length for the placeholder call vs. the real call would break that exact invariant --
    `_compile_loop`'s own `assert len(test2_bytes) == test2_len` catches precisely this
    mistake if it's ever made, which is why this parameter exists at all."""
    if op not in _JCC_ELSE:
        raise UnsupportedOp(
            f"no x86_64 stencil for operator {op!r} -- supported: {sorted(_JCC_ELSE)}"
        )
    cmp_eax_imm32 = b"\x3d" + struct.pack("<i", level)  # cmp eax, imm32 (no ModRM needed for EAX)
    if force_near:
        near_opcode = _JCC_ELSE[op] + 0x10  # short Jcc (0x7?) -> near Jcc (0F 8?), +0x10 exactly
        jcc = b"\x0f" + bytes([near_opcode]) + struct.pack("<i", skip_len)  # Jcc rel32
    else:
        jcc = bytes([_JCC_ELSE[op]]) + struct.pack("<b", skip_len)  # Jcc rel8
    return cmp_eax_imm32 + jcc


# G2: the incoming argument's register for a `mov [rbp-4], <arg1>`-shaped store -- Win64: ECX,
# SysV: EDI (ModRM byte only; the opcode + disp8 stay identical).
_MOV_LOCAL_ARG1: dict[Abi, bytes] = {"win64": b"\x89\x4d", "sysv": b"\x89\x7d"}


def emit_symbol_roundtrip(abi: Abi = "win64") -> bytes:
    """`int roundtrip(int x)` -- stores its argument (Win64: ECX, SysV: EDI) into a stack-local
    slot [rbp-4] (a symbol-table entry) and loads it straight back into EAX, proving addressable
    local storage (a "variable"), not just a single-register comparison. `abi` (G2) selects the
    incoming register."""
    push_rbp = b"\x55"
    mov_rbp_rsp = b"\x48\x89\xe5"
    mov_local_arg = _MOV_LOCAL_ARG1[abi] + b"\xfc"  # mov [rbp-4], <arg1>
    mov_eax_local = b"\x8b\x45\xfc"  # mov eax, [rbp-4]
    pop_rbp = b"\x5d"
    ret = b"\xc3"
    return push_rbp + mov_rbp_rsp + mov_local_arg + mov_eax_local + pop_rbp + ret


_SLOT_SIZE = 8  # 8-byte-aligned local slots (only the low 32 bits are used, via EAX -- this
# just keeps the frame-size/alignment arithmetic below simple, not a real 64-bit-value need yet).


def frame_size(n_slots: int) -> int:
    """The outer stack-frame size for `compile_goal`'s Bind/Remember path (a real local
    symbol table, generalizing `emit_symbol_roundtrip`'s one hardcoded slot to N named ones).
    `push rbp` shifts RSP by 8, so the frame size must be _8 (mod 16)_ to preserve whatever
    16-byte alignment phase a nested `emit_capability_call` fragment assumes at ITS OWN entry
    (each already self-balances via its own `sub/add rsp,0x28`, correctly, only if its entry
    alignment phase matches what it was built for)."""
    needed = _SLOT_SIZE * max(n_slots, 1)
    size = 8
    while size < needed:
        size += 16
    return size


def emit_frame_prologue(n_slots: int) -> bytes:
    """`push rbp; mov rbp,rsp; sub rsp,frame_size(n_slots)` -- establishes the persistent local
    variable frame `compile_goal` needs once a goal has any `Bind`/`Remember`. A fragment, not a
    complete stencil: pairs with `emit_frame_epilogue()` around the compiled statement body."""
    push_rbp = b"\x55"
    mov_rbp_rsp = b"\x48\x89\xe5"
    sub_rsp = b"\x48\x81\xec" + struct.pack("<i", frame_size(n_slots))  # sub rsp, imm32
    return push_rbp + mov_rbp_rsp + sub_rsp


def emit_frame_epilogue(n_slots: int) -> bytes:
    """`mov rsp,rbp; pop rbp; ret` -- the matching teardown for `emit_frame_prologue`. Takes
    `n_slots` only for dispatch-signature symmetry with the RISC-V backend (whose `sp`-relative
    frame, unlike x86-64's `rbp`-relative one, needs to know the size to restore `sp` without a
    frame pointer) -- unused here since `mov rsp,rbp` restores it directly."""
    del n_slots
    mov_rsp_rbp = b"\x48\x89\xec"
    pop_rbp = b"\x5d"
    ret = b"\xc3"
    return mov_rsp_rbp + pop_rbp + ret


def _local_offset(slot: int) -> int:
    return -_SLOT_SIZE * (slot + 1)


def emit_store_local(slot: int) -> bytes:
    """Store EAX into local slot `slot` -- a fragment, assumes `emit_frame_prologue` already
    ran; not a complete callable stencil on its own."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x89\x45" + struct.pack("<b", offset)  # mov [rbp+disp8], eax
    return b"\x89\x85" + struct.pack("<i", offset)  # mov [rbp+disp32], eax


def emit_load_local(slot: int) -> bytes:
    """Load local slot `slot` into EAX -- a fragment, same frame assumption as
    `emit_store_local`."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x8b\x45" + struct.pack("<b", offset)  # mov eax, [rbp+disp8]
    return b"\x8b\x85" + struct.pack("<i", offset)  # mov eax, [rbp+disp32]


def emit_store_local64(slot: int) -> bytes:
    """T8.16 -- store the FULL 64-bit RAX into local slot `slot` (unlike `emit_store_local`'s
    32-bit-only EAX store) -- the same slot address `_local_offset` already computes, since
    `_SLOT_SIZE` was already 8 bytes in anticipation of exactly this. Used for a `fallible` Bind's
    result: the packed `(tag << 32) | value` (T8.15) needs both halves preserved, not truncated."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x48\x89\x45" + struct.pack("<b", offset)  # mov [rbp+disp8], rax
    return b"\x48\x89\x85" + struct.pack("<i", offset)  # mov [rbp+disp32], rax


def emit_load_local64(slot: int) -> bytes:
    """T8.16 -- load the FULL 64-bit local slot `slot` into RAX -- the read-back half of
    `emit_store_local64`, used to recover a `fallible` Bind's packed result before extracting its
    tag (`emit_shift_right_32`) or its value (already in the low 32 bits, no shift needed)."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x48\x8b\x45" + struct.pack("<b", offset)  # mov rax, [rbp+disp8]
    return b"\x48\x8b\x85" + struct.pack("<i", offset)  # mov rax, [rbp+disp32]


def emit_lea_local(slot: int) -> bytes:
    """G8 -- `lea rax, [rbp+disp]`: loads the REAL ADDRESS of local slot `slot` into RAX (opcode
    0x8D, "load effective address" -- same ModRM shape as `emit_load_local64`'s `mov` (0x8B),
    just computing the address instead of dereferencing it). Needed for `PyObject_Vectorcall`'s
    `args` parameter: a real C array pointer into a contiguous run of local slots holding boxed
    `PyObject*` values, not a value read out of one."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x48\x8d\x45" + struct.pack("<b", offset)  # lea rax, [rbp+disp8]
    return b"\x48\x8d\x85" + struct.pack("<i", offset)  # lea rax, [rbp+disp32]


def emit_xor_local(slot: int) -> bytes:
    """G3 -- `xor eax, [rbp+disp]`: XORs local slot `slot`'s 32-bit value into EAX in place --
    the running-accumulator step `verified field` access uses to recompute a record's checksum
    (load each field into EAX, XOR it against the accumulator held in a local slot, store back).
    Same disp8/disp32 dual-form shape `emit_load_local`/`emit_store_local` already use, just
    opcode `0x33` (XOR r32, r/m32) instead of `0x8b` (MOV)."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x33\x45" + struct.pack("<b", offset)  # xor eax, [rbp+disp8]
    return b"\x33\x85" + struct.pack("<i", offset)  # xor eax, [rbp+disp32]


def emit_shl_rax_32() -> bytes:
    """G3 -- `shl rax, 32`: shifts the match flag (0 or 1, computed by `verified field` access)
    into RAX's high 32 bits, the mirror of `emit_shift_right_32` -- the second half of packing
    `(match << 32) | value` into one 64-bit result, the SAME `(tag << 32) | value` convention
    T8.15/T8.16 already established for `fallible` (read back via the same `is_ok`/`payload`)."""
    return b"\x48\xc1\xe0\x20"


def emit_or_local64(slot: int) -> bytes:
    """G3 -- `or rax, [rbp+disp]`: ORs a local slot's FULL 64-bit value into RAX in place --
    combines the shifted match flag (already in RAX's high 32 bits after `emit_shl_rax_32`) with
    the target field's value (stashed in a local slot while the checksum was recomputed) into
    the final packed `(match << 32) | value` result. Same disp8/disp32 dual-form shape as
    `emit_load_local64`/`emit_store_local64`, opcode `0x0b` (OR r64, r/m64) instead of `0x8b`."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x48\x0b\x45" + struct.pack("<b", offset)  # or rax, [rbp+disp8]
    return b"\x48\x0b\x85" + struct.pack("<i", offset)  # or rax, [rbp+disp32]


def emit_eax_is_zero() -> bytes:
    """G3 -- `test eax,eax; sete al; movzx eax,al`: EAX becomes 1 if it was 0, else 0 -- the
    "does this checksum match exactly" test `verified field` access needs. Reuses the EXACT
    `sete al` (`_SETCC["=="]`) + `movzx eax, al` byte sequences T8.1's `emit_govern_check`
    already established, not duplicated -- only `test eax,eax` (opcode `0x85 /r`, ModRM
    mod=11,reg=rm=EAX=000 => 0xC0) is new."""
    test_eax_eax = b"\x85\xc0"
    sete_al = _SETCC["=="]
    movzx_eax_al = b"\x0f\xb6\xc0"
    return test_eax_eax + sete_al + movzx_eax_al


def emit_load_immediate(value: int) -> bytes:
    """`mov eax, imm32` -- a fragment that loads a literal into the return register (the
    `Remember` stencil's "value is a number, not a name" case)."""
    return b"\xb8" + struct.pack("<i", value)


def emit_add_immediate(value: int) -> bytes:
    """`add eax, imm32` -- adds a literal to EAX in place (no ModRM needed, same trick as
    `cmp eax,imm32`). The Loop counter's increment step (`i = i + 1`)."""
    return b"\x05" + struct.pack("<i", value)


def emit_read_cycle_counter() -> bytes:
    """N4 -- `rdtsc`: reads the CPU's own Time-Stamp Counter, placing its low 32 bits in EAX
    (high 32 in EDX, deliberately unused for v0 -- see `CyclesRead`'s own docstring for the
    wrap-around boundary this draws). Opcode `0F 31`, no ModRM, no operand -- a single real CPU
    instruction, zero syscalls, zero `capability_addresses` resolution needed at all. The most
    literal possible reading of D72's own "raw, no libc shim" philosophy: `.tamil`'s FIRST
    primitive that touches the host machine's own clock without crossing into the OS at all.

    Because EAX already holds exactly the 32-bit value `.tamil`'s existing arithmetic convention
    expects, `remember t = cycles()` composes with G11's `derive`/`Compute` subtraction UNCHANGED
    -- `derive elapsed = t1 - t0` needs zero new arithmetic stencils, only this one new read."""
    return b"\x0f\x31"


def emit_add_local(slot: int) -> bytes:
    """G11 -- `add eax, [rbp+disp]`: adds local slot `slot`'s 32-bit value into EAX in place --
    same disp8/disp32 dual-form shape `emit_xor_local` already established, opcode 0x03 (ADD
    r32, r/m32) instead of 0x33 (XOR). `derive`'s own Compute codegen (`x + y`)."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x03\x45" + struct.pack("<b", offset)  # add eax, [rbp+disp8]
    return b"\x03\x85" + struct.pack("<i", offset)  # add eax, [rbp+disp32]


def emit_sub_local(slot: int) -> bytes:
    """G11 -- `sub eax, [rbp+disp]`: opcode 0x2b (SUB r32, r/m32), same shape as
    `emit_add_local`. `derive`'s own Compute codegen (`x - y`)."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x2b\x45" + struct.pack("<b", offset)  # sub eax, [rbp+disp8]
    return b"\x2b\x85" + struct.pack("<i", offset)  # sub eax, [rbp+disp32]


def emit_mul_local(slot: int) -> bytes:
    """G11 -- `imul eax, [rbp+disp]`: opcode 0x0f 0xaf (IMUL r32, r/m32 -- a two-byte opcode,
    unlike ADD/SUB/XOR's single-byte form; still the SAME ModRM disp8/disp32 addressing).
    `derive`'s own Compute codegen (`x * y`)."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x0f\xaf\x45" + struct.pack("<b", offset)  # imul eax, [rbp+disp8]
    return b"\x0f\xaf\x85" + struct.pack("<i", offset)  # imul eax, [rbp+disp32]


def emit_div_local(slot: int) -> bytes:
    """G11 -- integer division: `cdq; idiv [rbp+disp]`. `idiv`'s r/m32 form implicitly divides
    EDX:EAX by the operand (quotient -> EAX, remainder -> EDX) -- EDX MUST be sign-extended from
    EAX first (`cdq`, opcode 0x99) or it holds whatever garbage the previous instruction left
    there, a real correctness bug if omitted, not a defensive guess. `idiv`'s own opcode is
    `0xf7 /7` (single-operand form, the ModRM `reg` field is the `/7` op-code EXTENSION, not a
    register) -- `derive`'s own Compute codegen (`x / y`)."""
    cdq = b"\x99"
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        idiv = b"\xf7\x7d" + struct.pack("<b", offset)  # idiv [rbp+disp8]
    else:
        idiv = b"\xf7\xbd" + struct.pack("<i", offset)  # idiv [rbp+disp32]
    return cdq + idiv


# G2: caller-side stack reservation before any outbound call, before the call itself and undone
# after -- differs by OS ABI, not CPU. Win64 requires 32 bytes of caller-reserved SHADOW SPACE
# (for the callee to optionally spill its own register args into) PLUS 16-byte alignment; System
# V needs NO shadow space, just the 16-byte alignment `sub rsp,0x8` already gives (matching this
# stencil's own entry-alignment offset, same reasoning `emit_symbol_roundtrip` doesn't need since
# it never itself calls out).
_CALL_STACK_RESERVE: dict[Abi, bytes] = {
    "win64": b"\x48\x83\xec\x28",  # sub rsp, 0x28 (40 = 32 shadow + 8 align)
    "sysv": b"\x48\x83\xec\x08",  # sub rsp, 0x08 (8 align only, no shadow space)
}
_CALL_STACK_RESTORE: dict[Abi, bytes] = {
    "win64": b"\x48\x83\xc4\x28",  # add rsp, 0x28
    "sysv": b"\x48\x83\xc4\x08",  # add rsp, 0x08
}
# G2: the 64-bit "mov <reg>, imm64" opcode for the 1st/2nd integer arg register -- Win64:
# RCX/RDX; System V: RDI/RSI. `mov reg, imm64` = REX.W + (B8+reg) -- opcode depends only on the
# low 3 bits of the register number (no REX.B needed for any of RCX/RDX/RDI/RSI, all < 8).
_MOV_ARG1_IMM64_OPCODE: dict[Abi, bytes] = {"win64": b"\x48\xb9", "sysv": b"\x48\xbf"}  # RCX/RDI
_XOR_ARG2_ARG2: dict[Abi, bytes] = {"win64": b"\x48\x31\xd2", "sysv": b"\x48\x31\xf6"}  # RDX/RSI


def emit_capability_call(target_addr: int, abi: Abi = "win64") -> bytes:
    """Iter4 -- the `capability-call` stencil: `int call(void)` that calls a real, resolved
    C-ABI function at `target_addr` (patched as a 64-bit immediate) and returns its result.
    `abi` (G2) selects the call-site stack-reservation convention: Win64 requires 32 bytes of
    caller shadow space plus 16-byte alignment (`sub rsp, 0x28` = 40 bytes); System V needs no
    shadow space, just the alignment (`sub rsp, 0x8`)."""
    sub_rsp = _CALL_STACK_RESERVE[abi]
    mov_rax_imm64 = b"\x48\xb8" + struct.pack("<Q", target_addr & 0xFFFFFFFFFFFFFFFF)
    call_rax = b"\xff\xd0"  # call rax
    add_rsp = _CALL_STACK_RESTORE[abi]
    ret = b"\xc3"
    return sub_rsp + mov_rax_imm64 + call_rax + add_rsp + ret


# G8 -- the ABI's own fixed integer-argument register ORDER (LuaJIT FFI / copy-and-patch's own
# "fixed positional register mapping, no allocator" pattern, GPL-LLM-OSS Radar s56): each name
# maps to (REX prefix byte, ModRM `reg` field) for `mov <reg>, [rbp+disp]` -- the same `_local_
# offset`-addressed read `emit_load_local64` already does for RAX, generalized to the other
# integer-arg registers. R8/R9 need REX.R set (register numbers 8/9 >= 8); RCX/RDX/RDI/RSI don't.
_ARG_REG_ENCODING: dict[str, tuple[int, int]] = {
    "rcx": (0x48, 0b001),
    "rdx": (0x48, 0b010),
    "r8": (0x4C, 0b000),
    "r9": (0x4C, 0b001),
    "rdi": (0x48, 0b111),
    "rsi": (0x48, 0b110),
}
_ARG_ORDER: dict[Abi, list[str]] = {
    "win64": ["rcx", "rdx", "r8", "r9"],
    "sysv": ["rdi", "rsi", "rdx", "rcx", "r8", "r9"],
}


def _mov_reg_from_local(reg: str, slot: int) -> bytes:
    """`mov <reg>, [rbp+disp]` for one of the fixed ABI argument registers -- same disp8/disp32
    dual-form shape `emit_load_local64` already uses for RAX, generalized via
    `_ARG_REG_ENCODING`."""
    rex, reg_field = _ARG_REG_ENCODING[reg]
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        modrm = 0x40 | (reg_field << 3) | 0b101
        return bytes([rex, 0x8B, modrm]) + struct.pack("<b", offset)
    modrm = 0x80 | (reg_field << 3) | 0b101
    return bytes([rex, 0x8B, modrm]) + struct.pack("<i", offset)


def _mov_rax_to_rsp_offset(disp: int) -> bytes:
    """`mov [rsp+disp8], rax` -- RSP as a ModRM base ALWAYS needs a SIB byte (rm=100 is the SIB
    escape, not a plain register), unlike RBP-relative locals. Used to place a stack-spilled
    argument (the 5th+ on Win64, 7th+ on SysV) at its fixed pre-call stack offset."""
    return b"\x48\x89\x44\x24" + struct.pack("<b", disp)


def _sub_rsp(n: int) -> bytes:
    if -128 <= n <= 127:
        return b"\x48\x83\xec" + struct.pack("<b", n)
    return b"\x48\x81\xec" + struct.pack("<i", n)


def _add_rsp(n: int) -> bytes:
    if -128 <= n <= 127:
        return b"\x48\x83\xc4" + struct.pack("<b", n)
    return b"\x48\x81\xc4" + struct.pack("<i", n)


def emit_call_with_args(target_addr: int, arg_slots: list[int], abi: Abi = "win64") -> bytes:
    """G8 -- `emit_capability_call`'s args-capable sibling: `int call(...)` that loads N REAL,
    RUNTIME values (each already sitting in a local slot) into the ABI's fixed argument-register
    ORDER (Win64: RCX,RDX,R8,R9; SysV: RDI,RSI,RDX,RCX,R8,R9), spilling any argument beyond that
    count to the stack at its correct pre-call offset (Win64: `[rsp+0x20]` onward, right after the
    32-byte shadow space; SysV: `[rsp+0]` onward, no shadow space) -- the LuaJIT-FFI/copy-and-patch
    "fixed positional mapping, no register allocator" pattern (GPL-LLM-OSS Radar, s56), generalizing
    G5's single-immediate-arg `emit_load_immediate_arg1` to N runtime slot values. Stack reservation
    is computed so RSP is 16-byte-aligned immediately before `call` (the same `entry RSP ≡ 8 (mod
    16)` invariant `emit_capability_call`'s own hardcoded `0x28`/`0x08` already encode, generalized
    to any stack-arg count via `reserve = needed + ((8 - needed) % 16)`)."""
    reg_names = _ARG_ORDER[abi]
    reg_args = arg_slots[: len(reg_names)]
    stack_args = arg_slots[len(reg_names) :]
    needed = (0x20 if abi == "win64" else 0) + 8 * len(stack_args)
    reserve = needed + ((8 - needed) % 16)

    fragments = [_sub_rsp(reserve)]
    stack_base_disp = 0x20 if abi == "win64" else 0x00
    for i, slot in enumerate(stack_args):
        fragments.append(emit_load_local64(slot))  # rax = the arg's real runtime value
        fragments.append(_mov_rax_to_rsp_offset(stack_base_disp + i * 8))
    for reg, slot in zip(reg_names, reg_args, strict=False):
        fragments.append(_mov_reg_from_local(reg, slot))
    fragments.append(b"\x48\xb8" + struct.pack("<Q", target_addr & 0xFFFFFFFFFFFFFFFF))
    fragments.append(b"\xff\xd0")  # call rax
    fragments.append(_add_rsp(reserve))
    fragments.append(b"\xc3")  # ret
    return b"".join(fragments)


def _mov_local_from_reg(reg: str, slot: int) -> bytes:
    """`mov [rbp+disp], <reg>` -- the callee-side MIRROR of `_mov_reg_from_local` (opcode 0x89,
    store direction, vs 0x8B's load direction; same ModRM shape). Used to spill an incoming ABI
    argument register into its named parameter's local slot at `fn` entry."""
    rex, reg_field = _ARG_REG_ENCODING[reg]
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        modrm = 0x40 | (reg_field << 3) | 0b101
        return bytes([rex, 0x89, modrm]) + struct.pack("<b", offset)
    modrm = 0x80 | (reg_field << 3) | 0b101
    return bytes([rex, 0x89, modrm]) + struct.pack("<i", offset)


def emit_store_args_to_locals(param_slots: list[int], abi: Abi = "win64") -> bytes:
    """G8 -- a `fn`'s own entry-time counterpart to `emit_call_with_args`: spills each incoming
    ABI argument register into its named parameter's local slot, in the SAME fixed register
    order (Win64: RCX,RDX,R8,R9; SysV: RDI,RSI,RDX,RCX,R8,R9). A fragment (no `ret`), meant to run
    immediately after `emit_frame_prologue`. v0 boundary (FnDef's own docstring): register-only
    -- more params than the ABI has argument registers raises `UnsupportedOp`, since reading an
    INCOMING stack-spilled param needs a positive-RBP-offset addressing mode this frame doesn't
    have yet (unlike the CALL SITE's own `emit_call_with_args`, which already spills unlimited
    OUTGOING args to the stack -- that direction needed no new addressing mode, only a write)."""
    reg_names = _ARG_ORDER[abi]
    if len(param_slots) > len(reg_names):
        raise UnsupportedOp(
            f"fn params beyond the {len(reg_names)} {abi} argument registers aren't supported "
            "yet -- reading an incoming stack-spilled param needs a positive-RBP-offset "
            "addressing mode not built yet (G8 v0 boundary)"
        )
    return b"".join(
        _mov_local_from_reg(reg, slot) for reg, slot in zip(reg_names, param_slots, strict=False)
    )


def emit_python_call(callable_ptr: int, api_addr: int, abi: Abi = "win64") -> bytes:
    """The real bridge Kollan was blocked on: no Madras capability is a raw C-ABI function --
    they're all live Python callables -- so `int call(void)` here calls CPython's own
    `PyObject_CallObject(callable, NULL)` (a real, resolvable-at-runtime C API export, `api_addr`
    resolved by the caller via `ctypes.pythonapi` since libpython's load address varies per
    process, unlike an OS DLL export). `callable_ptr` is the target Python object's own real
    address (`id(obj)`, valid for as long as the caller keeps a live reference during the call).
    `abi` (G2) selects Win64 (callable in RCX, `args=NULL` in RDX) vs System V (callable in RDI,
    `args=NULL` in RSI) -- returns the raw `PyObject*` result (or NULL on a Python exception);
    converting that back into a real Python object and checking for an exception is
    `madras.dsl.kollan_bridge`'s job, not this stencil's."""
    sub_rsp = _CALL_STACK_RESERVE[abi]
    mov_arg1_callable = _MOV_ARG1_IMM64_OPCODE[abi] + struct.pack(
        "<Q", callable_ptr & 0xFFFFFFFFFFFFFFFF
    )
    xor_arg2_arg2 = _XOR_ARG2_ARG2[abi]  # args = NULL
    mov_rax_api = b"\x48\xb8" + struct.pack("<Q", api_addr & 0xFFFFFFFFFFFFFFFF)
    call_rax = b"\xff\xd0"
    add_rsp = _CALL_STACK_RESTORE[abi]
    ret = b"\xc3"
    return sub_rsp + mov_arg1_callable + xor_arg2_arg2 + mov_rax_api + call_rax + add_rsp + ret


def emit_load_immediate64(value: int) -> bytes:
    """T8.15 -- loads a full 64-bit immediate into RAX: `mov rax, imm64`. The same byte pattern
    already used inside `emit_capability_call`/`emit_python_call`/`emit_alloc` for loading a
    target address, exposed here as its own reusable fragment -- the first half of the
    tagged-value packing convention `(tag << 32) | (value & 0xFFFFFFFF)` a future Result-as-value
    control-flow feature will build on. A leaf fragment (no `ret`, no calls) -- proven correct by
    round-trip (both 32-bit halves survive intact) before any real caller depends on it."""
    return b"\x48\xb8" + struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF)


def emit_shift_right_32() -> bytes:
    """T8.15 -- `shr rax, 32`: the second half of the tagged-value unpacking convention. Applied
    to a packed 64-bit value (low 32 bits = value, high 32 bits = tag), this leaves the TAG in
    RAX's low 32 bits (zero-extended -- SHR is a logical shift) -- ready for `emit_store_local`
    exactly like any other EAX-sized result. The VALUE half needs no shift at all: reading EAX
    directly (before this shift ever runs) already gives the low 32 bits, the same "just read
    EAX" convention every existing stencil already uses."""
    return b"\x48\xc1\xe8\x20"


def emit_load_absolute64(addr: int) -> bytes:
    """T8.17 -- reads a real 64-bit value from a known absolute address: `mov rax, [addr]`, the
    REX.W (64-bit) sibling of `emit_load_absolute`'s 32-bit `A1` form. Used to read a capability-
    call result-cache entry's whole packed `(populated << 32) | value` slot in one instruction."""
    opcode_a1 = b"\x48\xa1"
    moffs64 = struct.pack("<Q", addr & 0xFFFFFFFFFFFFFFFF)
    return opcode_a1 + moffs64


def emit_store_absolute64(addr: int) -> bytes:
    """T8.17 -- writes RAX to a known absolute address: `mov [addr], rax`, the store-direction
    sibling of `emit_load_absolute64` (`A3` is `A1`'s store-mirror opcode, same moffs64 shape, no
    ModRM/base-register addressing needed here either). Used to write a capability-call result
    back into its result-cache entry after a real (cache-miss) call."""
    opcode_a3 = b"\x48\xa3"
    moffs64 = struct.pack("<Q", addr & 0xFFFFFFFFFFFFFFFF)
    return opcode_a3 + moffs64


def emit_set_bit32() -> bytes:
    """T8.17 -- `bts rax, 32`: sets bit 32 of RAX (the result-cache's "populated" tag) to 1,
    leaving every other bit -- including the low 32 bits, a just-computed real call result --
    completely UNCHANGED. The whole reason this is used instead of a scratch-register OR: no
    scratch register is needed at all to turn a plain 32-bit result already sitting in RAX into
    the packed `(1 << 32) | value` shape `emit_store_absolute64` writes back to the cache."""
    return b"\x48\x0f\xba\xe8\x20"


def emit_load_absolute(addr: int) -> bytes:
    """T8.13 -- reads a real 4-byte int from a known absolute 64-bit address: `mov eax, [addr]`,
    encoded via the x86-64 `A1` opcode (`MOV EAX, moffs64`) -- the ONE x86-64 form that embeds a
    full 64-bit absolute memory address directly, with no ModRM/base-register/RIP-relative
    addressing needed at all. A FRAGMENT (no `ret`), same shape as `emit_load_local` -- used
    inside `compile_goal`'s array-index read, never as a standalone stencil.

    `addr` is always a real, already-materialized address (an array element's real location,
    computed by the CLOSED tree's allocator BEFORE `compile_goal` ever runs) -- this fragment
    only reads it, copy-and-patch style, exactly like `emit_capability_call`'s target address."""
    opcode_a1 = b"\xa1"
    moffs64 = struct.pack("<Q", addr & 0xFFFFFFFFFFFFFFFF)
    return opcode_a1 + moffs64


# G2: the incoming `size` argument's register for `movsxd r11, <reg>` -- Win64: ECX, SysV: EDI.
_MOVSXD_R11_ARG1: dict[Abi, bytes] = {"win64": b"\x4c\x63\xd9", "sysv": b"\x4c\x63\xdf"}


def emit_alloc(base_addr: int, offset_addr: int, abi: Abi = "win64") -> bytes:
    """T8.12 -- the bump-allocator stencil: `int64 alloc(int32 size)` -> a real address, bumping
    a live offset cell forward by `size` bytes each call. `base_addr` (the arena's own start) and
    `offset_addr` (an 8-byte cell holding the current offset) are both patched in as 64-bit
    immediates -- copy-and-patch, same shape as `emit_capability_call`/`emit_python_call` -- so
    the SAME compiled stencil bytes work for any arena, just re-emitted per allocator instance.

    `abi` (G2) selects the incoming `size` argument's register (Win64: ECX, SysV: EDI), result
    (a real 64-bit pointer, not a 32-bit int) always in RAX. A leaf function (calls nothing out)
    -- unlike `emit_python_call`, this needs no unwind-table registration
    (`_run_x86_64_with_unwind`); the plain runner is correct here.

    `madras.dsl.kollan_allocator.BumpAllocator` owns bounds-checking (in Python, before running
    this) -- this stencil has none, by design: a bump allocator's whole point is skipping any
    per-allocation cost beyond load/add/store."""
    mov_r9_offset_addr = b"\x49\xb9" + struct.pack("<Q", offset_addr & 0xFFFFFFFFFFFFFFFF)
    mov_rax_from_r9 = b"\x49\x8b\x01"  # mov rax, [r9]  (rax = current offset)
    mov_r10_base_addr = b"\x49\xba" + struct.pack("<Q", base_addr & 0xFFFFFFFFFFFFFFFF)
    add_r10_rax = b"\x49\x01\xc2"  # add r10, rax  (r10 = base + offset -- the result pointer)
    movsxd_r11_ecx = _MOVSXD_R11_ARG1[abi]  # sign-extend the size argument to 64-bit
    add_rax_r11 = b"\x4c\x01\xd8"  # add rax, r11  (rax = offset + size -- the new offset)
    mov_to_r9 = b"\x49\x89\x01"  # mov [r9], rax  (store the new offset back)
    mov_rax_r10 = b"\x49\x8b\xc2"  # mov rax, r10  (return the result pointer)
    ret = b"\xc3"
    return (
        mov_r9_offset_addr
        + mov_rax_from_r9
        + mov_r10_base_addr
        + add_r10_rax
        + movsxd_r11_ecx
        + add_rax_r11
        + mov_to_r9
        + mov_rax_r10
        + ret
    )


# G2 (D72) -- Linux x86-64's raw syscall convention: nr in RAX, up to 6 args in
# RDI/RSI/RDX/R10/R8/R9. NOT RCX for the 4th arg -- the `syscall` instruction itself clobbers
# RCX (return address) and R11 (flags), the standard reason this diverges from the SysV
# *function*-call convention at exactly that one register.
_SYSCALL_ARG_OPCODE: list[bytes] = [
    b"\x48\xbf",  # mov rdi, imm64  (arg0)
    b"\x48\xbe",  # mov rsi, imm64  (arg1)
    b"\x48\xba",  # mov rdx, imm64  (arg2)
    b"\x49\xba",  # mov r10, imm64  (arg3)
    b"\x49\xb8",  # mov r8,  imm64  (arg4)
    b"\x49\xb9",  # mov r9,  imm64  (arg5)
]


def emit_syscall(nr: int, *args: int) -> bytes:
    """G2 (D72) -- a raw Linux x86-64 syscall, emitted directly: no libc wrapper, no C shim.
    `int syscall_stencil(void)` loads the syscall number into RAX, up to 6 integer arguments
    into RDI/RSI/RDX/R10/R8/R9, executes the `syscall` instruction (opcode `0F 05`), and returns
    the raw result (a negative value is `-errno`, the kernel's own convention -- this stencil
    doesn't interpret it). Copy-and-patch: `nr`/`args` are baked in as immediates at EMIT time --
    this is a 0-arg CALLABLE stencil, matching every other Kollan stencil's "fixed template,
    patched holes" shape; the syscall's own arguments are fixed per compiled instance, not
    passed in by the caller. Linux-only by design (D72: Windows has no stable raw-syscall
    interface, goes through kernel32/ntdll exports by address instead) -- emitting these bytes
    doesn't itself require running on Linux; only EXECUTING them does (the closed-tree runner's
    job, not this function's)."""
    if len(args) > 6:
        raise UnsupportedOp(f"emit_syscall supports at most 6 arguments, got {len(args)}")
    mov_rax_nr = b"\x48\xb8" + struct.pack("<Q", nr & 0xFFFFFFFFFFFFFFFF)
    mov_args = b"".join(
        _SYSCALL_ARG_OPCODE[i] + struct.pack("<Q", arg & 0xFFFFFFFFFFFFFFFF)
        for i, arg in enumerate(args)
    )
    syscall = b"\x0f\x05"
    ret = b"\xc3"
    return mov_rax_nr + mov_args + syscall + ret


# G5 -- the incoming `size` argument's register for a runtime `emit_alloc` CALL SITE (as opposed
# to `_MOVSXD_R11_ARG1`, which is `emit_alloc`'s own callee-side read of that register): Win64
# ECX, SysV EDI -- same register `_CMP_IMM32_OPCODE` already keys by ABI, opcode 0xB8+reg (`mov
# r32, imm32`, no ModRM needed).
_LOAD_IMM_ARG1_OPCODE: dict[Abi, bytes] = {"win64": b"\xb9", "sysv": b"\xbf"}


def emit_load_immediate_arg1(value: int, abi: Abi = "win64") -> bytes:
    """G5 -- `mov ecx/edi, imm32`: loads a literal into the first-int-arg register, the CALLER
    side of invoking the UNCHANGED `emit_alloc` stencil (T8.12) as a real subroutine from within
    a bigger compiled goal's own instruction stream -- `list`/`map`'s runtime chunk size (16 or
    24 bytes) is compile-time-known, so this is the one new fragment needed to reuse `emit_alloc`
    wholesale for G5's runtime growth, exactly the "reuse before inventing" discipline every
    prior G-row followed (G3's verified read, G4's string load/store)."""
    return _LOAD_IMM_ARG1_OPCODE[abi] + struct.pack("<i", value)


def emit_store_immediate_indirect(value: int, offset: int = 0) -> bytes:
    """G5 -- `mov qword [rax+disp8], imm32` (sign-extended to 64-bit): writes a literal into the
    address currently held in RAX -- a freshly `emit_alloc`'d chunk's base (offset 0) or one of
    its later fields (a map chunk's value at offset 8). The write half of G5's runtime growth:
    an `emit_alloc`'d chunk has no existing content to overwrite via `emit_store_local64` (which
    only targets a STACK slot); this targets the HEAP-like arena address itself."""
    if offset == 0:
        return b"\x48\xc7\x00" + struct.pack("<i", value)  # mov qword [rax], imm32
    return b"\x48\xc7\x40" + struct.pack("<b", offset) + struct.pack("<i", value)


def emit_link_local_into_indirect(slot: int, offset: int) -> bytes:
    """G5 -- `mov r10, [rbp+disp8]; mov [rax+disp8], r10`: loads a local slot's 64-bit value
    (the list/map's PREVIOUS head, stashed in a scratch slot before `emit_alloc` clobbered RAX)
    into scratch register R10, then stores it into the freshly allocated chunk's `next` field
    (RAX + `offset`) -- the chunk-LINKING step that turns a bare allocation into a real prepend
    onto the existing chain, G5's chunked/segmented list layout (research: outlier candidate #1,
    Jai `Bucket_Array`/Odin arena-mode dynamic arrays -- growth never copies or moves existing
    chunks, only links a new one on)."""
    slot_offset = _local_offset(slot)
    load_r10 = b"\x4c\x8b\x55" + struct.pack("<b", slot_offset)  # mov r10, [rbp+disp8]
    store_r10 = b"\x4c\x89\x50" + struct.pack("<b", offset)  # mov [rax+disp8], r10
    return load_r10 + store_r10


def emit_mov_eax_edx() -> bytes:
    """N2 -- `mov eax, edx`: opcode `0x89` (MOV r/m32, r32), ModRM `0xD0` (mod=11 register-
    direct, reg=EDX(010), rm=EAX(000)). Moves `idiv`'s REMAINDER (always left in EDX,
    `emit_div_local`'s own documented convention) into EAX so it can flow through every
    existing EAX-based fragment (`emit_store_local`, etc.) -- the hash-table probe's initial
    slot index is `arg mod capacity`, i.e. exactly that remainder."""
    return b"\x89\xd0"


def emit_load_local_into_rcx(slot: int) -> bytes:
    """N2 -- loads local slot `slot`'s 64-bit value into RCX: `mov rcx, [rbp+disp8/32]`.
    Reuses `_ARG_REG_ENCODING`'s already-generalized RCX encoding (G8) for a purpose G8 didn't
    anticipate: here RCX is the SIB INDEX register `emit_load_keyed_slot`/
    `emit_store_keyed_slot_from_rdx` scale by 8 to address a hash-table slot -- nothing to do
    with argument passing. Requires the local slot to have been stored via a 64-bit store
    (`emit_store_local64`) -- reading a 32-bit-stored slot with this 64-bit load pulls
    uninitialized stack garbage into RCX's upper 32 bits (a real bug this project has already
    hit twice: N2's `_CACHE_DOUBLED`, N1a's loop induction variable below)."""
    return _mov_reg_from_local("rcx", slot)


def emit_load_local_into_ecx(slot: int) -> bytes:
    """N1a -- loads local slot `slot`'s 32-bit value into ECX (not RCX): `mov ecx,
    [rbp+disp8/32]`, opcode `8B` + ModRM (reg=001 ECX, rm=101 RBP-relative) -- same shape
    `emit_load_local`'s own EAX-target form already has, just the ModRM `reg` field swapped
    from 000(EAX) to 001(ECX). A 32-bit WRITE to ECX architecturally zero-extends RCX's upper
    32 bits (the x86-64 guarantee), so this is the SAFE way to get a local's value into RCX for
    SIB indexing when the slot was stored via the ORDINARY 32-bit `emit_store_local` (a loop's
    own induction variable, stored/incremented via `emit_store_local`/`emit_add_immediate`,
    never `emit_store_local64`) -- `emit_load_local_into_rcx`'s 64-bit load would read that
    slot's uninitialized upper 32 bits as real garbage; this reads only the clean 32 bits that
    actually exist and lets the CPU zero-extend the rest itself."""
    offset = _local_offset(slot)
    if -128 <= offset <= 127:
        return b"\x8b\x4d" + struct.pack("<b", offset)  # mov ecx, [rbp+disp8]
    return b"\x8b\x8d" + struct.pack("<i", offset)  # mov ecx, [rbp+disp32]


def emit_load_local_into_rdx(slot: int) -> bytes:
    """N2 -- loads local slot `slot`'s 64-bit value into RDX: `mov rdx, [rbp+disp8/32]`. RDX
    carries the value being compared against a slot's stored key, or written into a slot's key/
    value field via `emit_store_keyed_slot_from_rdx`."""
    return _mov_reg_from_local("rdx", slot)


def emit_load_indexed_scaled() -> bytes:
    """N1a -- `mov eax, [rax + rcx*4]`: reads one 4-byte array element at a RUNTIME index.
    Precondition (the caller's job): RAX holds the array's base address
    (`emit_load_immediate64`), RCX holds the index (`emit_load_local_into_rcx`).

    This is what `emit_load_absolute` can't do -- that stencil reads a COMPILE-TIME-fixed
    absolute address (`base + literal*4` folded by the compiler); this computes `base + i*4` in
    the addressing mode itself, at zero extra instruction cost, via x86-64's SIB scaled-index
    form. **No runtime bounds check, by construction, not by omission:** `_compile_ops` only
    emits this after PROVING the index's range fits the array (a loop induction variable's
    `range(start, stop)` bounds are already forced to be integer literals by `_compile_loop`,
    so the array access is proven in-bounds at compile time, the same "governed by
    construction" shape every other compile-time-checked boundary in this codebase already has)
    -- the check happens ONCE at compile time instead of on every access.

    Encoding: `8B` (MOV r32, r/m32) + ModRM `0x04` (mod=00 no-disp, reg=000 EAX dest, rm=100
    SIB-escape) + SIB `0x88` (scale=10/x4, the 4-byte element size, index=001 RCX, base=000
    RAX). No REX prefix: a 32-bit load with both registers in the low 8, no extension bits."""
    return b"\x8b\x04\x88"


def emit_load_keyed_slot(offset: int = 0) -> bytes:
    """N2 -- `mov rax, [rax + rcx*8 + offset]`, `offset` in {0, 8}: reads one 8-byte field
    (offset 0 = a hash-table slot's KEY, offset 8 = its VALUE) out of a 16-byte slot at
    `table_base(RAX) + probe_index(RCX)*8`. RCX here already holds `probe_index*2` (so the *8
    scale lands on the 16-byte slot boundary) -- the caller's job, mirroring
    `emit_load_indexed_scaled`'s own precondition shape. Encoding: REX.W(0x48) + `8B` (MOV
    r64, r/m64) + ModRM (mod=00/01, reg=000 RAX dest, rm=100 SIB-escape) + SIB `0xC8`
    (scale=11/x8, index=001 RCX, base=000 RAX) [+ disp8 when offset=8]."""
    if offset == 0:
        return b"\x48\x8b\x04\xc8"  # mov rax, [rax+rcx*8]
    if offset == 8:
        return b"\x48\x8b\x44\xc8\x08"  # mov rax, [rax+rcx*8+8]
    raise ValueError(f"emit_load_keyed_slot only supports offset in (0, 8), got {offset!r}")


def emit_store_keyed_slot_from_rdx(offset: int = 0) -> bytes:
    """N2 -- `mov [rax + rcx*8 + offset], rdx`, `offset` in {0, 8}: the write half of
    `emit_load_keyed_slot`, storing RDX into a hash-table slot's KEY (offset 0) or VALUE
    (offset 8) field. Same SIB precondition (RAX=table base, RCX=probe_index*2)."""
    if offset == 0:
        return b"\x48\x89\x14\xc8"  # mov [rax+rcx*8], rdx
    if offset == 8:
        return b"\x48\x89\x54\xc8\x08"  # mov [rax+rcx*8+8], rdx
    raise ValueError(
        f"emit_store_keyed_slot_from_rdx only supports offset in (0, 8), got {offset!r}"
    )


def emit_cmp_rax_rdx() -> bytes:
    """N2 -- `cmp rax, rdx`: REX.W(0x48) + `39` (CMP r/m64, r64) + ModRM `0xD0` (mod=11,
    reg=RDX(010), rm=RAX(000)). Sets flags for a following `emit_je`/`emit_jne` -- the
    hash-table probe's own "is this slot's stored key the one we're looking for" test, and
    (against the empty-slot sentinel) "is this slot empty" test."""
    return b"\x48\x39\xd0"


def emit_je(offset: int) -> bytes:
    """N2 -- `je rel8`: jump `offset` bytes past this instruction's own end IF the last compare
    was equal (ZF=1). Opcode `0x74` + a signed rel8 -- the same short-jump SHAPE `emit_jump`
    already has, conditional instead of unconditional (mirrors `_JCC_ELSE`'s inline bytes,
    exposed here as a reusable fragment since the probe loop composes its own branches freely,
    not through `emit_branch_on_compare`'s fixed cmp-immediate shape)."""
    return b"\x74" + struct.pack("<b", offset)


def emit_jne(offset: int) -> bytes:
    """N2 -- `jne rel8`: `emit_je`'s inverse (ZF=0). Opcode `0x75` + signed rel8."""
    return b"\x75" + struct.pack("<b", offset)


def emit_load_indirect_offset(offset: int = 0) -> bytes:
    """G5 -- `mov rax, [rax+disp8]`: follows a pointer held in RAX by `offset` bytes -- reused
    for BOTH a chunk-walk hop (offset 8, following a list/map chunk's `next` link) and the final
    field read (offset 0, a list's value or a map's key/value) once the walk reaches the target
    chunk. `list`/`map` read v0's honest boundary: the hop COUNT is compile-time-known (a literal
    index, or a map key's position resolved from its literal construction order, same "not a
    real runtime hash probe yet" scope `record_field_offsets` already draws for records) --
    only the WALK itself runs at runtime, correctly reading chunks built by either the literal
    constructor or a runtime `push`/`mapset`."""
    if offset == 0:
        return b"\x48\x8b\x00"  # mov rax, [rax]
    return b"\x48\x8b\x40" + struct.pack("<b", offset)  # mov rax, [rax+disp8]


__all__ = [
    "emit_add_immediate",
    "emit_alloc",
    "emit_branch",
    "emit_branch_on_compare",
    "emit_call_with_args",
    "emit_capability_call",
    "emit_cmp_rax_rdx",
    "emit_compare_and_jump_if_false",
    "emit_eax_is_zero",
    "emit_frame_epilogue",
    "emit_frame_prologue",
    "emit_govern_check",
    "emit_je",
    "emit_je32",
    "emit_jne",
    "emit_jump",
    "emit_jump32",
    "emit_lea_local",
    "emit_link_local_into_indirect",
    "emit_load_absolute",
    "emit_load_absolute64",
    "emit_load_immediate",
    "emit_load_immediate64",
    "emit_load_immediate_arg1",
    "emit_load_indexed_scaled",
    "emit_load_indirect_offset",
    "emit_load_keyed_slot",
    "emit_load_local",
    "emit_load_local64",
    "emit_load_local_into_ecx",
    "emit_load_local_into_rcx",
    "emit_load_local_into_rdx",
    "emit_mov_eax_edx",
    "emit_or_local64",
    "emit_python_call",
    "emit_read_cycle_counter",
    "emit_set_bit32",
    "emit_shift_right_32",
    "emit_shl_rax_32",
    "emit_store_absolute64",
    "emit_store_args_to_locals",
    "emit_store_immediate_indirect",
    "emit_store_keyed_slot_from_rdx",
    "emit_store_local",
    "emit_store_local64",
    "emit_symbol_roundtrip",
    "emit_syscall",
    "emit_xor_local",
    "frame_size",
]
