"""RISC-V (RV64I) stencils -- every instruction a fixed 32-bit word, register/immediate fields
at the same bit positions across formats (R/I/S). Unlike x86-64's memorized byte sequences,
each stencil here is *composed* from a small set of pure bit-packing encoder functions --
mathematically regular by the ISA's own design goal (simple, orthogonal compiler codegen).

Calling convention (RISC-V calling convention, not an OS ABI choice -- this package has no real
execution target on this dev machine, so nothing here has been proven against real silicon
yet; the Shakti QEMU/gem5 toolchain from T6 is the real execution path, later): first int arg
in `a0` (x10), result in `a0`. Verified here by encode/decode round-trip (`decode_riscv64`,
a small disassembler for the exact instructions these stencils use) rather than live execution
-- an honest substitute, not a claim of hardware-proven correctness.
"""

from __future__ import annotations

import struct

from tamil_lang.kollan.errors import UnsupportedOp
from tamil_lang.kollan.types import Op

ZERO, RA, SP, T0, A0, A1, A2 = 0, 1, 2, 5, 10, 11, 12

_OP_IMM = 0x13  # addi / slti / sltiu / xori / slli
_OP = 0x33  # R-type arithmetic (sltu)
_LOAD = 0x03  # lw / ld
_STORE = 0x23  # sw / sd
_BRANCH = 0x63  # beq
_JAL = 0x6F
_JALR = 0x67
_LUI = 0x37

_F3_ADDI = 0x0
_F3_SLLI = 0x1
_F3_SLTI = 0x2
_F3_SLTIU = 0x3
_F3_XORI = 0x4
_F3_LW = 0x2
_F3_LD = 0x3
_F3_SW = 0x2
_F3_SD = 0x3
_F3_SLTU = 0x3
_F3_BEQ = 0x0
_F3_BNE = 0x1
_F3_BLT = 0x4
_F3_BGE = 0x5
_F3_JALR = 0x0


def _i_type(imm12: int, rs1: int, funct3: int, rd: int, opcode: int) -> bytes:
    word = ((imm12 & 0xFFF) << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode
    return struct.pack("<I", word)


def _r_type(funct7: int, rs2: int, rs1: int, funct3: int, rd: int, opcode: int) -> bytes:
    word = ((funct7 & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode
    return struct.pack("<I", word)


def _s_type(imm12: int, rs2: int, rs1: int, funct3: int, opcode: int) -> bytes:
    imm = imm12 & 0xFFF
    imm_hi, imm_lo = imm >> 5, imm & 0x1F
    word = (imm_hi << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (imm_lo << 7) | opcode
    return struct.pack("<I", word)


def _b_type(imm13: int, rs2: int, rs1: int, funct3: int, opcode: int) -> bytes:
    """B-type: PC-relative, always even (the ISA's own 2-byte instruction-alignment unit) --
    bit 0 is never stored, exactly the way `jz`/`jmp` rel8 offsets in x86_64.py are also
    computed as arithmetic on real basic-block byte lengths, not memorized deltas."""
    imm = imm13 & 0x1FFF
    bit12, bit11, bits10_5, bits4_1 = (
        (imm >> 12) & 1,
        (imm >> 11) & 1,
        (imm >> 5) & 0x3F,
        (imm >> 1) & 0xF,
    )
    word = (
        (bit12 << 31)
        | (bits10_5 << 25)
        | (rs2 << 20)
        | (rs1 << 15)
        | (funct3 << 12)
        | (bits4_1 << 8)
        | (bit11 << 7)
        | opcode
    )
    return struct.pack("<I", word)


def _j_type(imm21: int, rd: int, opcode: int) -> bytes:
    imm = imm21 & 0x1FFFFF
    bit20, bits19_12, bit11, bits10_1 = (
        (imm >> 20) & 1,
        (imm >> 12) & 0xFF,
        (imm >> 11) & 1,
        (imm >> 1) & 0x3FF,
    )
    word = (bit20 << 31) | (bits10_1 << 21) | (bit11 << 20) | (bits19_12 << 12) | (rd << 7) | opcode
    return struct.pack("<I", word)


def _u_type(imm20: int, rd: int, opcode: int) -> bytes:
    """U-type (`lui`) -- the simplest format: no scrambling, the 20-bit immediate occupies the
    top bits directly."""
    word = ((imm20 & 0xFFFFF) << 12) | (rd << 7) | opcode
    return struct.pack("<I", word)


def _sign_extend(value: int, bits: int) -> int:
    mask = 1 << (bits - 1)
    return (value ^ mask) - mask


def decode_riscv64(word: int) -> dict[str, int]:
    """The inverse of the `_i_type`/`_r_type`/`_s_type`/`_b_type`/`_j_type` encoders above --
    decodes exactly the instruction shapes this module's stencils emit (opcode auto-selects the
    format), so `decode_riscv64(int.from_bytes(encode(...), "little"))` round-trips the
    original fields. Not a general RV64 disassembler."""
    opcode = word & 0x7F
    if opcode in (_OP_IMM, _LOAD, _JALR):
        imm12 = _sign_extend((word >> 20) & 0xFFF, 12)
        rs1 = (word >> 15) & 0x1F
        funct3 = (word >> 12) & 0x7
        rd = (word >> 7) & 0x1F
        return {"opcode": opcode, "rd": rd, "funct3": funct3, "rs1": rs1, "imm": imm12}
    if opcode == _STORE:
        imm_lo = (word >> 7) & 0x1F
        funct3 = (word >> 12) & 0x7
        rs1 = (word >> 15) & 0x1F
        rs2 = (word >> 20) & 0x1F
        imm_hi = (word >> 25) & 0x7F
        imm12 = _sign_extend((imm_hi << 5) | imm_lo, 12)
        return {"opcode": opcode, "funct3": funct3, "rs1": rs1, "rs2": rs2, "imm": imm12}
    if opcode == _OP:
        rd = (word >> 7) & 0x1F
        funct3 = (word >> 12) & 0x7
        rs1 = (word >> 15) & 0x1F
        rs2 = (word >> 20) & 0x1F
        funct7 = (word >> 25) & 0x7F
        return {
            "opcode": opcode,
            "rd": rd,
            "funct3": funct3,
            "rs1": rs1,
            "rs2": rs2,
            "funct7": funct7,
        }
    if opcode == _BRANCH:
        bit11 = (word >> 7) & 1
        bits4_1 = (word >> 8) & 0xF
        funct3 = (word >> 12) & 0x7
        rs1 = (word >> 15) & 0x1F
        rs2 = (word >> 20) & 0x1F
        bits10_5 = (word >> 25) & 0x3F
        bit12 = (word >> 31) & 1
        raw = (bit12 << 12) | (bit11 << 11) | (bits10_5 << 5) | (bits4_1 << 1)
        imm13 = _sign_extend(raw, 13)
        return {"opcode": opcode, "funct3": funct3, "rs1": rs1, "rs2": rs2, "imm": imm13}
    if opcode == _JAL:
        rd = (word >> 7) & 0x1F
        bits19_12 = (word >> 12) & 0xFF
        bit11 = (word >> 20) & 1
        bits10_1 = (word >> 21) & 0x3FF
        bit20 = (word >> 31) & 1
        raw = (bit20 << 20) | (bits19_12 << 12) | (bit11 << 11) | (bits10_1 << 1)
        imm21 = _sign_extend(raw, 21)
        return {"opcode": opcode, "rd": rd, "imm": imm21}
    if opcode == _LUI:
        rd = (word >> 7) & 0x1F
        imm20 = (word >> 12) & 0xFFFFF
        return {"opcode": opcode, "rd": rd, "imm": imm20}
    raise ValueError(f"decode_riscv64: unrecognized opcode {opcode:#04x}")


def _addi(rd: int, rs1: int, imm: int) -> bytes:
    return _i_type(imm, rs1, _F3_ADDI, rd, _OP_IMM)


def _slti(rd: int, rs1: int, imm: int) -> bytes:
    return _i_type(imm, rs1, _F3_SLTI, rd, _OP_IMM)


def _sltiu(rd: int, rs1: int, imm: int) -> bytes:
    return _i_type(imm, rs1, _F3_SLTIU, rd, _OP_IMM)


def _xori(rd: int, rs1: int, imm: int) -> bytes:
    return _i_type(imm, rs1, _F3_XORI, rd, _OP_IMM)


def _sltu(rd: int, rs1: int, rs2: int) -> bytes:
    return _r_type(0, rs2, rs1, _F3_SLTU, rd, _OP)


def _mv(rd: int, rs1: int) -> bytes:
    return _addi(rd, rs1, 0)


def _beq(rs1: int, rs2: int, imm: int) -> bytes:
    return _b_type(imm, rs2, rs1, _F3_BEQ, _BRANCH)


def _jal(rd: int, imm: int) -> bytes:
    return _j_type(imm, rd, _JAL)


def _jalr(rd: int, rs1: int, imm: int) -> bytes:
    return _i_type(imm, rs1, _F3_JALR, rd, _JALR)


def _lui(rd: int, imm20: int) -> bytes:
    return _u_type(imm20, rd, _LUI)


def _slli(rd: int, rs1: int, shamt: int) -> bytes:
    return _i_type(shamt & 0x3F, rs1, _F3_SLLI, rd, _OP_IMM)  # RV64 shamt is 6 bits


def _sd(rs2: int, offset: int, rs1: int) -> bytes:
    return _s_type(offset, rs2, rs1, _F3_SD, _STORE)


def _ld(rd: int, offset: int, rs1: int) -> bytes:
    return _i_type(offset, rs1, _F3_LD, rd, _LOAD)


def _li(rd: int, imm: int) -> bytes:
    """Load a full 64-bit immediate into `rd` -- the standard `lui`+`addi`(+recursive
    `slli`+`addi`) decomposition every RV64 assembler's `li` pseudo-op expands to for values
    that don't fit a single 12-bit `addi`. Handles the full signed 64-bit range, not just what
    fits in 32 bits (real addresses need all 64)."""
    imm = _sign_extend(imm & ((1 << 64) - 1), 64)
    lo12 = _sign_extend(imm & 0xFFF, 12)
    hi = (imm - lo12) >> 12  # exact: hi*4096 + lo12 == imm, by construction
    if -(2**19) <= hi < 2**19:
        # The remaining high bits fit in lui's 20-bit field directly -- base case.
        if hi == 0:
            return _addi(rd, ZERO, lo12)
        return _lui(rd, hi) + _addi(rd, rd, lo12)
    # Still doesn't fit: recurse on the high bits, then shift left 12 and add the low chunk --
    # this always terminates (each recursion strictly shrinks `hi`) and reaches the base case
    # within 5 rounds for any 64-bit value.
    return _li(rd, hi) + _slli(rd, rd, 12) + _addi(rd, rd, lo12)


def _ret() -> bytes:
    return _i_type(0, RA, _F3_JALR, ZERO, _JALR)  # jalr x0, 0(x1)


def emit_govern_check(op: Op, level: int) -> bytes:
    """`int check(int rank)` -> 1 if `rank <op> level` else 0. Each op composed from a small
    set of instructions (slti/sltiu/xori/sltu), not a memorized byte table -- RV64I's regular
    encoding makes this composition possible in the first place."""
    if op == "<":
        body = _slti(A0, A0, level)
    elif op == ">=":
        body = _slti(A0, A0, level) + _xori(A0, A0, 1)
    elif op == "<=":
        body = _slti(A0, A0, level + 1)
    elif op == ">":
        body = _slti(A0, A0, level + 1) + _xori(A0, A0, 1)
    elif op == "==":
        body = _xori(A0, A0, level) + _sltiu(A0, A0, 1)
    elif op == "!=":
        body = _xori(A0, A0, level) + _sltu(A0, ZERO, A0)
    else:
        raise UnsupportedOp(f"no riscv64 stencil for operator {op!r}")
    return body + _ret()


def emit_symbol_roundtrip() -> bytes:
    """`int roundtrip(int x)` -- stores A0 into a stack-local slot (`sw a0, 8(sp)`, a
    symbol-table entry) and loads it straight back (`lw a0, 8(sp)`), the RISC-V shape of the
    same variable-storage proof `x86_64.emit_symbol_roundtrip` gives for Win64."""
    alloc = _addi(SP, SP, -16)
    store = _s_type(8, A0, SP, _F3_SW, _STORE)
    load = _i_type(8, SP, _F3_LW, A0, _LOAD)
    dealloc = _addi(SP, SP, 16)
    return alloc + store + load + dealloc + _ret()


def emit_branch() -> bytes:
    """Iter3 -- the `control-flow` (branch) stencil: `int branch(int cond, int a, int b)` ->
    `a` if `cond != 0` else `b`. Two real basic blocks (then/else) joined by a conditional
    branch (`beq`) and an unconditional jump (`jal`) -- the branch/jump immediates are computed
    as plain integer arithmetic on each block's real byte length (4 bytes/instruction here),
    the same relative-offset-patching every real backend does for control flow, not a
    memorized table."""
    then_block = _mv(A0, A1)  # a0 = a
    jump_to_end = _jal(ZERO, 0)  # placeholder immediate, patched below
    else_block = _mv(A0, A2)  # a0 = b
    ret = _ret()

    beq_len, then_len, jump_len, else_len = 4, len(then_block), len(jump_to_end), len(else_block)
    else_offset = beq_len + then_len + jump_len  # where the else-block starts, from beq's PC
    end_offset = else_offset + else_len  # where `ret` starts, from the branch/jump PCs

    beq_zero = _beq(A0, ZERO, else_offset)  # if cond == 0, go to else_block
    jump_to_end = _jal(ZERO, end_offset - beq_len - then_len)  # unconditional jump to `ret`

    return beq_zero + then_block + jump_to_end + else_block + ret


# Each entry: (funct3, rs1, rs2) for the branch that fires when the ORIGINAL comparison is
# FALSE -- e.g. ">=" takes the then-block unless a0 < t0, so the else-branch is `blt a0,t0`.
_ELSE_BRANCH: dict[Op, tuple[int, int, int]] = {
    ">=": (_F3_BLT, A0, T0),  # else if a0 < t0
    ">": (_F3_BGE, T0, A0),  # else if t0 >= a0  (i.e. a0 <= t0)
    "<=": (_F3_BLT, T0, A0),  # else if t0 < a0   (i.e. a0 > t0)
    "<": (_F3_BGE, A0, T0),  # else if a0 >= t0
    "==": (_F3_BNE, A0, T0),  # else if a0 != t0
    "!=": (_F3_BEQ, A0, T0),  # else if a0 == t0
}


def emit_jump(offset: int) -> bytes:
    """`jal x0, offset` -- an unconditional jump, `offset` bytes from ITS OWN pc. The building
    block `compile_goal`'s recursive Branch compiler uses to skip over an else-block after a
    then-block finishes, generalizing `emit_branch`'s fixed `jal` to an arbitrary (compiled,
    real) byte distance."""
    return _jal(ZERO, offset)


def emit_compare_and_jump_if_false(op: Op, level: int, skip_len: int) -> bytes:
    """`_li(t0, level)` + a native branch -- jumps `skip_len` bytes past the BRANCH
    instruction's own pc (not past the `_li` sequence, whose length varies) when
    `a0 <op> level` is FALSE. `skip_len` is the caller's responsibility to compute as the full
    distance from the branch instruction to the else-block's first byte -- typically
    `4 + len(then_bytes) + len(emit_jump(...))` (the branch's own 4 bytes, the compiled
    then-block, and its own trailing unconditional jump over the else-block).

    Fragment form of `emit_branch_on_compare`'s branch: that stencil compares the fixed
    argument A0 and selects between two fixed values; this one lets the caller supply an
    ARBITRARY compiled then-block, which is what makes recursive Branch compilation possible."""
    if op not in _ELSE_BRANCH:
        raise UnsupportedOp(
            f"no riscv64 stencil for operator {op!r} -- supported: {sorted(_ELSE_BRANCH)}"
        )
    funct3, rs1, rs2 = _ELSE_BRANCH[op]
    load_level = _li(T0, level)
    branch = _b_type(skip_len, rs2, rs1, funct3, _BRANCH)
    return load_level + branch


def emit_branch_on_compare(op: Op, level: int) -> bytes:
    """The `Expr`-lowering path into `emit_branch`'s shape: `int branch(int x, int a, int b)`
    -> `a` if `x <op> level` else `b`, using RV64I's own native branch-compare instructions
    (`beq`/`bne`/`blt`/`bge`) directly instead of computing a boolean first (`emit_govern_check`'s
    shape). RV64 branches always compare two REGISTERS -- no reg-vs-immediate branch form
    exists -- so `level` is loaded into `t0` via `_li` first (handles the full 64-bit range,
    not just what fits a 12-bit `addi`)."""
    if op not in _ELSE_BRANCH:
        raise UnsupportedOp(
            f"no riscv64 stencil for operator {op!r} -- supported: {sorted(_ELSE_BRANCH)}"
        )
    funct3, rs1, rs2 = _ELSE_BRANCH[op]

    load_level = _li(T0, level)
    then_block = _mv(A0, A1)
    jump_to_end = _jal(ZERO, 0)  # placeholder immediate, patched below
    else_block = _mv(A0, A2)
    ret = _ret()

    branch_len, then_len, jump_len = 4, len(then_block), len(jump_to_end)
    else_offset = branch_len + then_len + jump_len  # where else_block starts (local to branch)
    end_offset = else_offset + len(else_block)

    branch_to_else = _b_type(else_offset, rs2, rs1, funct3, _BRANCH)
    jump_to_end = _jal(ZERO, end_offset - branch_len - then_len)

    return load_level + branch_to_else + then_block + jump_to_end + else_block + ret


_SLOT_SIZE = 8  # the natural RV64 register/doubleword width -- one slot per name.


def frame_size(n_slots: int) -> int:
    """The outer stack-frame size for `compile_goal`'s Bind/Remember path -- must stay a
    multiple of 16 (the RISC-V psABI's own stack-alignment requirement), unlike x86-64's
    `push rbp`-shifted ≡8 (mod 16) rule (RV64 has no `push`, so there's no extra 8-byte shift
    to compensate for)."""
    needed = _SLOT_SIZE * max(n_slots, 1)
    size = 16
    while size < needed:
        size += 16
    return size


def emit_frame_prologue(n_slots: int) -> bytes:
    """`addi sp,sp,-frame_size(n_slots)` -- establishes the persistent local-variable frame.
    No `ra` save needed at this level: this function's own body never calls anything directly
    (only nested `emit_capability_call` fragments do, and each already saves/restores `ra`
    around its own internal `jalr`)."""
    return _addi(SP, SP, -frame_size(n_slots))


def emit_frame_epilogue(n_slots: int) -> bytes:
    """`addi sp,sp,frame_size(n_slots); ret` -- the matching teardown."""
    return _addi(SP, SP, frame_size(n_slots)) + _ret()


def emit_store_local(slot: int) -> bytes:
    """Store A0 into local slot `slot` -- a fragment, assumes `emit_frame_prologue` already
    ran; not a complete callable stencil on its own."""
    return _sd(A0, _SLOT_SIZE * slot, SP)


def emit_load_local(slot: int) -> bytes:
    """Load local slot `slot` into A0 -- same frame assumption as `emit_store_local`."""
    return _i_type(_SLOT_SIZE * slot, SP, _F3_LD, A0, _LOAD)


def emit_load_immediate(value: int) -> bytes:
    """Load a literal into A0 (the `Remember` stencil's "value is a number, not a name" case) --
    reuses `_li`, the same full-64-bit-range loader `emit_capability_call` already proved."""
    return _li(A0, value)


def emit_add_immediate(value: int) -> bytes:
    """`addi a0, a0, value` -- adds a literal to A0 in place. The Loop counter's increment step
    (`i = i + 1`). Only supports what fits `addi`'s 12-bit signed immediate (-2048..2047) --
    loop increments/steps this small cover every real case this pass targets; a full 64-bit
    add would need `_li` into a scratch register first, not built here."""
    if not (-2048 <= value <= 2047):
        raise UnsupportedOp(f"emit_add_immediate only supports a 12-bit signed value, got {value}")
    return _addi(A0, A0, value)


def emit_capability_call(target_addr: int) -> bytes:
    """Iter4 -- the `capability-call` stencil: `int call(void)` that calls a real, resolved
    function at `target_addr` (patched as a full 64-bit immediate) and returns its result. `ra`
    is saved/restored around the internal call (`jalr ra,0(t0)` overwrites it with our own
    return address, which would otherwise clobber whatever *our* caller expects to find there)
    -- a real call-stack discipline requirement, not an x86-64-specific quirk."""
    save_ra = _addi(SP, SP, -16) + _sd(RA, 8, SP)
    load_target = _li(T0, target_addr)
    call = _jalr(RA, T0, 0)
    restore_ra = _ld(RA, 8, SP) + _addi(SP, SP, 16)
    return save_ra + load_target + call + restore_ra + _ret()


__all__ = [
    "decode_riscv64",
    "emit_add_immediate",
    "emit_branch",
    "emit_branch_on_compare",
    "emit_capability_call",
    "emit_compare_and_jump_if_false",
    "emit_frame_epilogue",
    "emit_frame_prologue",
    "emit_govern_check",
    "emit_jump",
    "emit_load_immediate",
    "emit_load_local",
    "emit_store_local",
    "emit_symbol_roundtrip",
    "frame_size",
]
