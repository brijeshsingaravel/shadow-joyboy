"""Kollan (கொல்லன், "smith/forge") -- the own machine-code emitter (RFC-0002 §7.4, T8). This
package is the codegen half only: pure bytes in (a kernel node + a target ISA), pure bytes out
(real machine code) -- no memory allocation, no execution. Per the same D58 open/closed split
T3.1's `ffi_bridge` already established: *declaring/emitting* is open (mechanical, reusable, no
Agent-OS dependency); *executing* emitted native code is exactly the "fuzzy/untrusted work" the
Sandboxed law exists for, and stays in the closed tree (`madras.dsl.kollan`).

**Copy-and-patch, not a general IR lowering pass.** A 2026 outlier-emitter survey (LLVM/GCC:
optimize-aggressively-compile-slowly; Cranelift/QBE: fast-but-still-a-general-SSA-backend;
Deegen/LuaJIT-Remake's Copy-and-Patch: pre-built machine-code *stencils* per opcode, patched
with constant holes at emit time, no runtime IR at all) found Copy-and-Patch the structural
match for Kollan: the 6-node kernel is frozen (D50/D60), so "one stencil per kernel node kind"
needs no general lowering machinery.

**ISA-parametrized from the start (founder decision, s55, re-confirming RFC-0002 §7.4's own
flagged tradeoff).** Two backends, `x86_64` and `riscv64`, share one stencil-construction layer
built from a small per-ISA `Encoder` protocol (prologue/epilogue, store/load a local, return) --
not two independent copies of each stencil. x86-64 is CISC/variable-length (REX/ModRM prefixes,
memorized byte sequences); RISC-V (RV64I) is exactly what its name promises -- every instruction
a fixed 32-bit word, register/immediate fields at the same bit positions across formats -- so
its encoder is a pure bit-packing function, not a lookup table, and is genuinely less
error-prone (x86-64's governance-check stencil needed a real ABI bug fixed on its first run;
RISC-V's regular encoding removes that whole class of mistake). Execution is asymmetric on
purpose: x86-64 runs live on real hardware here (`madras.dsl.kollan.run_*`); RISC-V has no real
execution target on this dev machine, so it's verified by encode/decode round-trip (a full RV64I
disassembler in this same package) rather than overclaiming live execution it can't back up --
Shakti QEMU/gem5 (from T6) remains the real RISC-V execution path for later, once needed.

**RFC-0002 §7.4's own incremental order, followed (not skipped):** Iter1 int/math (T8.1,
`governance-check`) → Iter2 variables/symbol-table (`emit_symbol_roundtrip`) → Iter3
control-flow/basic-blocks (`emit_branch`) → Iter4 functions/call-stack (this increment,
`emit_capability_call` -- where `capability-call` actually lives). Iter4 needed a full 64-bit
immediate loader on RISC-V (`_li`, the standard `lui`/`addi`/`slli` recursive decomposition) and
explicit save/restore of the link register `ra` around the internal call -- real call-stack
discipline, not an x86-64-specific quirk (Win64's shadow-space requirement is the x86-64 shape
of the same underlying problem: a function that itself calls out must protect its own caller's
state across that call).
"""

from __future__ import annotations

from typing import Literal

from tamil_lang.ast import (
    ArrayLiteral,
    Bind,
    Branch,
    Call,
    Compute,
    Derive,
    FnDef,
    Goal,
    Govern,
    ListLiteral,
    Loop,
    MapLiteral,
    MapSet,
    Match,
    Parallel,
    Project,
    Push,
    Recall,
    RecordLiteral,
    Remember,
    Return,
    Statement,
    StringLiteral,
)
from tamil_lang.expr import BinOp, Compare, Expr, Name, Num, parse_condition, parse_expr
from tamil_lang.kollan import riscv64 as _riscv64
from tamil_lang.kollan import x86_64 as _x86_64
from tamil_lang.kollan.errors import UnsupportedIsa, UnsupportedNode, UnsupportedOp
from tamil_lang.kollan.riscv64 import decode_riscv64
from tamil_lang.kollan.types import Abi, Isa, Op

# Row 3c-ii: the codegen reads the IR one statement kind at a time (see `_compile_ops`). Safe
# direction -- `nadi` imports only `ast`/`mugavari`, so this adds no cycle.
from tamil_lang.nadi import (
    NadiOp,
    assign_mugavari_ids,
    lower_each_with_defs,
    lower_to_nadi,
    nadi_arrays,
    nadi_maps,
    nadi_records,
    nadi_symbols,
)

_BACKENDS = {"x86_64": _x86_64, "riscv64": _riscv64}

# The trailing `ret` instruction's byte length per backend -- used only by `compile_goal` to
# fall multiple self-contained call stencils through into each other (strip every stencil's own
# `ret` except the last, so control genuinely falls from one call into the next rather than
# returning after the first).
_RET_LEN = {"x86_64": 1, "riscv64": 4}


def _reject_non_default_abi(isa: Isa, abi: Abi) -> None:
    """G2: `abi` (Win64 vs System V) only means something for x86_64 -- riscv64 has one calling
    convention, no OS-ABI split. Passing a non-default `abi` for any other ISA is a caller
    mistake, rejected loudly rather than silently ignored."""
    if abi != "win64":
        raise UnsupportedIsa(f"abi={abi!r} only applies to isa='x86_64', not {isa!r}")


def emit_govern_check(isa: Isa, op: Op, level: int, abi: Abi = "win64") -> bytes:
    """Iter1 (T8.1) -- the `governance-check` stencil: `int check(int rank)` -> 1 if
    `rank <op> level` else 0. Dispatches to the requested ISA's own encoder. `abi` (G2, Win64 vs
    System V) only applies to x86_64 -- riscv64 has one calling convention, no OS-ABI split."""
    if isa not in _BACKENDS:
        raise UnsupportedIsa(f"no backend for isa {isa!r} -- supported: {sorted(_BACKENDS)}")
    if isa == "x86_64":
        return _x86_64.emit_govern_check(op, level, abi)
    _reject_non_default_abi(isa, abi)
    return _BACKENDS[isa].emit_govern_check(op, level)


def emit_symbol_roundtrip(isa: Isa, abi: Abi = "win64") -> bytes:
    """Iter2 -- the `memory-ref`/variables stencil: `int roundtrip(int x)` that stores its
    argument into a local stack slot (a symbol-table entry, the essence of a variable) and
    loads it straight back -- proving store/load through addressable local storage works,
    not just single-register comparisons (Iter1's whole surface). `abi` (G2) selects the
    incoming argument's register (Win64: ECX, SysV: EDI) -- x86_64 only."""
    if isa not in _BACKENDS:
        raise UnsupportedIsa(f"no backend for isa {isa!r} -- supported: {sorted(_BACKENDS)}")
    if isa == "x86_64":
        return _x86_64.emit_symbol_roundtrip(abi)
    _reject_non_default_abi(isa, abi)
    return _BACKENDS[isa].emit_symbol_roundtrip()


def emit_branch(isa: Isa, abi: Abi = "win64") -> bytes:
    """Iter3 -- the `control-flow` (branch) stencil: `int branch(int cond, int a, int b)` ->
    `a` if `cond != 0` else `b`. Two real basic blocks (then/else) joined by a conditional
    branch and an unconditional jump, with both jump targets computed as arithmetic on each
    block's real byte length -- the genuinely hard, general part of control-flow codegen
    (relative-offset backpatching), not a memorized template. `abi` (G2) selects the incoming
    3-argument register convention (Win64: ECX/EDX/R8D, SysV: EDI/ESI/EDX) -- x86_64 only."""
    if isa not in _BACKENDS:
        raise UnsupportedIsa(f"no backend for isa {isa!r} -- supported: {sorted(_BACKENDS)}")
    if isa == "x86_64":
        return _x86_64.emit_branch(abi)
    _reject_non_default_abi(isa, abi)
    return _BACKENDS[isa].emit_branch()


def emit_branch_on_compare(isa: Isa, op: Op, level: int, abi: Abi = "win64") -> bytes:
    """The native-comparison-fused counterpart to `emit_branch`: `int branch(int x, int a, int
    b)` -> `a` if `x <op> level` else `b`, with the comparison folded directly into the
    conditional jump (x86-64's `cmp`+`Jcc`, RISC-V's native `beq`/`bne`/`blt`/`bge`) instead of
    `emit_branch`'s separate compute-a-boolean-then-`test`-it shape -- the idiomatic form a real
    compiler emits for `if x <op> level {..} else {..}`. `abi` (G2) selects the incoming
    3-argument register convention -- x86_64 only."""
    if isa not in _BACKENDS:
        raise UnsupportedIsa(f"no backend for isa {isa!r} -- supported: {sorted(_BACKENDS)}")
    if isa == "x86_64":
        return _x86_64.emit_branch_on_compare(op, level, abi)
    _reject_non_default_abi(isa, abi)
    return _BACKENDS[isa].emit_branch_on_compare(op, level)


def lower_branch(expr: Expr, isa: Isa, abi: Abi = "win64") -> bytes:
    """**Lowers a parsed `Expr` (from `tamil_lang.expr`) into a native `emit_branch`-shaped
    stencil.** v0 scope: only `Compare(op, Name, Num)` -- a single named argument compared
    against an integer literal (`x > 5`, `count >= 0`) -- the shape `emit_branch_on_compare`
    already has a stencil for and the single most common real `.tamil` condition form. Anything
    else (comparing two names, arithmetic on either side, non-Compare exprs) raises
    `UnsupportedNode`: an honest scope boundary -- those need a real register/variable
    allocator (multiple live values, not just "the one incoming argument"), not built yet."""
    if not isinstance(expr, Compare):
        raise UnsupportedNode(
            f"lower_branch only supports a Compare expression so far, got {type(expr).__name__}"
        )
    if not isinstance(expr.left, Name) or not isinstance(expr.right, Num):
        raise UnsupportedNode(
            "lower_branch only supports `name <op> literal` so far (e.g. `x > 5`) -- "
            "comparing two names or an arithmetic sub-expression needs a real variable "
            "allocator, not built yet"
        )
    return emit_branch_on_compare(isa, expr.op, expr.right.value, abi)


def emit_capability_call(isa: Isa, target_addr: int, abi: Abi = "win64") -> bytes:
    """Iter4 -- the `capability-call` stencil: `int call(void)` that calls a real, resolved
    function at `target_addr` and returns its result. This is the node every real `.tamil`
    program actually uses -- calling a name-resolved capability -- reduced to its honest
    minimum: a 0-arg call to an already-known address, not name resolution itself (that's the
    interpreter's `resolve_toolsets()` job, not the emitter's). `abi` (G2) selects the call-site
    convention: Win64 reserves 32 bytes of caller shadow space before any call; System V needs
    none -- only x86_64 has this split."""
    if isa not in _BACKENDS:
        raise UnsupportedIsa(f"no backend for isa {isa!r} -- supported: {sorted(_BACKENDS)}")
    if isa == "x86_64":
        return _x86_64.emit_capability_call(target_addr, abi)
    _reject_non_default_abi(isa, abi)
    return _BACKENDS[isa].emit_capability_call(target_addr)


def emit_call_with_args(
    isa: Isa, target_addr: int, arg_slots: list[int], abi: Abi = "win64"
) -> bytes:
    """G8 -- `emit_capability_call`'s args-capable sibling: loads N real runtime local-slot
    values into the ABI's fixed argument-register order, spilling beyond that to the stack.
    x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_call_with_args only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_call_with_args(target_addr, arg_slots, abi)


def emit_store_args_to_locals(isa: Isa, param_slots: list[int], abi: Abi = "win64") -> bytes:
    """G8 -- a `fn`'s own entry-time counterpart to `emit_call_with_args`: spills each incoming
    ABI argument register into its named parameter's local slot. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_store_args_to_locals only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_store_args_to_locals(param_slots, abi)


def emit_python_call(isa: Isa, callable_ptr: int, api_addr: int, abi: Abi = "win64") -> bytes:
    """The real capability-resolution bridge: no Madras capability is a raw C-ABI function
    (they're all live Python callables), so this calls CPython's own
    `PyObject_CallObject(callable, NULL)` instead -- `callable_ptr`/`api_addr` are both real,
    resolved-at-runtime addresses (`madras.dsl.kollan_bridge`'s job to resolve, not the
    emitter's). x86-64 only so far -- the only backend with live execution on this dev
    machine; RISC-V's equivalent bridge is real future work, not stubbed here. `abi` (G2)
    selects Win64 (callable in RCX) vs System V (callable in RDI) argument-register + shadow-
    space convention."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_python_call only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_python_call(callable_ptr, api_addr, abi)


def emit_alloc(isa: Isa, base_addr: int, offset_addr: int, abi: Abi = "win64") -> bytes:
    """T8.12 -- the bump-allocator stencil dispatcher: `int64 alloc(int32 size)` -> a real
    address, bumping a live offset cell (at `offset_addr`) forward within a fixed arena (starting
    at `base_addr`) each call. The first concrete implementation behind the `memory-ref` kernel
    node (D50/D60) -- the node itself stays frozen; THIS is one swappable allocation strategy
    (Zig's explicit-arena model, not Rust's ownership or Swift's ARC) a developer could later
    replace without touching the kernel. x86-64 only so far -- a leaf stencil, no execution-side
    unwind-table concern (unlike `emit_python_call`), but still only proven live on this backend.
    `abi` (G2) selects the incoming `size` argument's register (Win64: ECX, SysV: EDI)."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_alloc only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_alloc(base_addr, offset_addr, abi)


def emit_load_immediate_arg1(isa: Isa, value: int, abi: Abi = "win64") -> bytes:
    """G5 -- loads a literal into the first-int-arg register, the caller-side half of invoking
    the UNCHANGED `emit_alloc` stencil as a real subroutine call from within a bigger compiled
    goal (`list`/`map`'s runtime `push`/`mapset` growth). x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_load_immediate_arg1 only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_load_immediate_arg1(value, abi)


def emit_store_immediate_indirect(isa: Isa, value: int, offset: int = 0) -> bytes:
    """G5 -- writes a literal into the address held in RAX (a freshly `emit_alloc`'d chunk).
    x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_store_immediate_indirect only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_store_immediate_indirect(value, offset)


def emit_lea_local(isa: Isa, slot: int) -> bytes:
    """G8 -- loads the real ADDRESS of local slot `slot` into RAX (`lea`, not `mov`) -- needed
    for `PyObject_Vectorcall`'s `args` parameter, a real C array pointer into a contiguous run
    of local slots. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_lea_local only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_lea_local(slot)


def emit_link_local_into_indirect(isa: Isa, slot: int, offset: int) -> bytes:
    """G5 -- links a stashed local slot (the list/map's previous head) into a freshly allocated
    chunk's `next` field. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_link_local_into_indirect only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_link_local_into_indirect(slot, offset)


def emit_load_indirect_offset(isa: Isa, offset: int = 0) -> bytes:
    """G5 -- follows a pointer held in RAX by `offset` bytes -- a list/map chunk-walk hop.
    x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_load_indirect_offset only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_load_indirect_offset(offset)


def emit_syscall(isa: Isa, nr: int, *args: int) -> bytes:
    """G2 (D72) -- dispatch to the raw-syscall stencil. x86_64 only (riscv64's own syscall
    convention is real future work). The STENCIL is Linux-only by design (no libc wrapper, no C
    shim -- Windows goes through kernel32/ntdll instead), but EMITTING the bytes doesn't itself
    require running on Linux; only executing them does (the closed-tree runner's job)."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_syscall only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_syscall(nr, *args)


def emit_load_absolute(isa: Isa, addr: int) -> bytes:
    """T8.13 -- reads a real 4-byte int from a known absolute address (an array element's real,
    already-materialized location). x86-64 only so far -- the `A1` opcode (`MOV EAX, moffs64`)
    this reduces to has no RISC-V equivalent (RV64I has no single-instruction full-64-bit-absolute
    memory operand); a RISC-V array-index stencil would need a `_li`-loaded base register plus a
    regular offset load, real future work, not stubbed here."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_load_absolute only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_load_absolute(addr)


def emit_load_immediate64(isa: Isa, value: int) -> bytes:
    """T8.15 -- loads a full 64-bit immediate into RAX, the first half of the tagged-value
    packing convention `(tag << 32) | (value & 0xFFFFFFFF)` a future Result-as-value
    control-flow feature will build on. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_load_immediate64 only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_load_immediate64(value)


def emit_frame_prologue(isa: Isa, n_slots: int) -> bytes:
    """G8 -- public dispatcher for `emit_frame_prologue` (previously only reachable via the
    internal `backend` object inside `_compile_ops`/`compile_fndef`), needed by closed-tree
    callers (`madras.dsl.kollan_bridge`) that compose a real multi-local frame calling non-leaf
    code OUTSIDE the `.tamil` compile pipeline itself. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_frame_prologue only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_frame_prologue(n_slots)


def emit_frame_epilogue(isa: Isa, n_slots: int) -> bytes:
    """G8 -- `emit_frame_prologue`'s public dispatcher sibling. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_frame_epilogue only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_frame_epilogue(n_slots)


def frame_size(isa: Isa, n_slots: int) -> int:
    """G8 -- public dispatcher for `frame_size` (the byte size `emit_frame_prologue`/
    `emit_frame_epilogue` reserve for `n_slots` locals), needed by closed-tree callers that must
    know a frame's real size to register correct unwind info for it
    (`_build_framed_unwind_info`). x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"frame_size only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.frame_size(n_slots)


def emit_shift_right_32(isa: Isa) -> bytes:
    """T8.15 -- the second half of the tagged-value unpacking convention: extracts the tag from
    a packed 64-bit value's high 32 bits into RAX's low 32 bits (zero-extended). x86-64 only so
    far -- RV64 has no equivalent-shaped need yet (this trick exists specifically to pack two
    32-bit values into ONE Win64-ABI return register; RISC-V's own calling convention isn't
    constrained the same way, real future work if/when it needs this)."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_shift_right_32 only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_shift_right_32()


def emit_store_local64(isa: Isa, slot: int) -> bytes:
    """T8.16 -- store the FULL 64-bit RAX into local slot `slot`, used for a `fallible` Bind's
    packed result. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_store_local64 only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_store_local64(slot)


def emit_load_local64(isa: Isa, slot: int) -> bytes:
    """T8.16 -- load the FULL 64-bit local slot `slot` into RAX, the read-back half of
    `emit_store_local64`. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_load_local64 only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_load_local64(slot)


def emit_mov_eax_edx(isa: Isa) -> bytes:
    """N2 -- moves `idiv`'s remainder (EDX) into EAX. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_mov_eax_edx only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_mov_eax_edx()


def emit_load_local_into_rcx(isa: Isa, slot: int) -> bytes:
    """N2 -- loads a local slot's value into RCX, the SIB index register the cache probe's
    keyed-slot addressing scales. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_load_local_into_rcx only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_load_local_into_rcx(slot)


def emit_load_local_into_ecx(isa: Isa, slot: int) -> bytes:
    """N1a -- loads a 32-bit-stored local slot's value into ECX (zero-extending into RCX by
    the x86-64 architectural guarantee), the SIB index register `emit_load_indexed_scaled`
    scales. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_load_local_into_ecx only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_load_local_into_ecx(slot)


def emit_load_local_into_rdx(isa: Isa, slot: int) -> bytes:
    """N2 -- loads a local slot's value into RDX, used both as a compare operand and as the
    source register `emit_store_keyed_slot_from_rdx` writes. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_load_local_into_rdx only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_load_local_into_rdx(slot)


def emit_load_indexed_scaled(isa: Isa) -> bytes:
    """N1a -- reads a 4-byte array element at a RUNTIME index: `[RAX + RCX*4]`. x86-64 only so
    far -- RV64I has no scaled-index addressing mode (it needs an explicit `slli`+`add` into a
    scratch register, then `lw`), real future work rather than a stub."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_load_indexed_scaled only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_load_indexed_scaled()


def emit_load_keyed_slot(isa: Isa, offset: int = 0) -> bytes:
    """N2 -- reads a hash-table slot's KEY (offset 0) or VALUE (offset 8) field at a RUNTIME
    index (`table_base(RAX) + probe_index(RCX)*8`). x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_load_keyed_slot only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_load_keyed_slot(offset)


def emit_store_keyed_slot_from_rdx(isa: Isa, offset: int = 0) -> bytes:
    """N2 -- `emit_load_keyed_slot`'s write direction. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_store_keyed_slot_from_rdx only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_store_keyed_slot_from_rdx(offset)


def emit_cmp_rax_rdx(isa: Isa) -> bytes:
    """N2 -- `cmp rax, rdx`, sets flags for a following `emit_je`/`emit_jne`. x86-64 only so
    far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_cmp_rax_rdx only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_cmp_rax_rdx()


def emit_je(isa: Isa, offset: int) -> bytes:
    """N2 -- `je rel8`, the conditional sibling of `emit_jump`. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_je only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_je(offset)


def emit_jne(isa: Isa, offset: int) -> bytes:
    """N2 -- `jne rel8`, `emit_je`'s inverse. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_jne only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_jne(offset)


def emit_jump32(isa: Isa, offset: int) -> bytes:
    """N2 -- `jmp rel32`, `emit_jump`'s NEAR sibling (fixed 5-byte length, +-2GB range) --
    needed once N2's cache-probe loop's own body was found to exceed rel8's +-127 byte range.
    x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_jump32 only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_jump32(offset)


def emit_je32(isa: Isa, offset: int) -> bytes:
    """N2 -- `je rel32`, `emit_je`'s NEAR sibling (fixed 6-byte length). x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_je32 only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_je32(offset)


def emit_load_absolute64(isa: Isa, addr: int) -> bytes:
    """T8.17 -- reads a real 64-bit value from a known absolute address, the REX.W sibling of
    `emit_load_absolute`. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_load_absolute64 only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_load_absolute64(addr)


def emit_store_absolute64(isa: Isa, addr: int) -> bytes:
    """T8.17 -- writes RAX to a known absolute address, the store-direction sibling of
    `emit_load_absolute64`. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_store_absolute64 only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_store_absolute64(addr)


def emit_set_bit32(isa: Isa) -> bytes:
    """T8.17 -- sets bit 32 of RAX (the result-cache's "populated" tag), leaving every other bit
    -- incl. a just-computed real call result in the low 32 bits -- unchanged; no scratch
    register needed. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_set_bit32 only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_set_bit32()


def emit_read_cycle_counter(isa: Isa) -> bytes:
    """N4 -- reads the CPU's Time-Stamp Counter (`rdtsc`) into EAX's low 32 bits -- a genuinely
    NATIVE (not FFI) clock read, zero syscalls. x86-64 only so far -- RV64I has no equivalent
    single-instruction TSC read (it has its own `rdcycle` CSR read, a real, separate primitive
    for later, not a stub)."""
    if isa != "x86_64":
        raise UnsupportedIsa(
            f"emit_read_cycle_counter only supports isa='x86_64' so far, got {isa!r}"
        )
    return _x86_64.emit_read_cycle_counter()


def emit_xor_local(isa: Isa, slot: int) -> bytes:
    """G3 -- XORs local slot `slot`'s 32-bit value into EAX in place, the running-accumulator
    step `verified field` access uses to recompute a record's checksum. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_xor_local only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_xor_local(slot)


def emit_add_local(isa: Isa, slot: int) -> bytes:
    """G11 -- adds local slot `slot`'s 32-bit value into EAX in place -- `derive`'s own Compute
    codegen (`x + y`). x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_add_local only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_add_local(slot)


def emit_sub_local(isa: Isa, slot: int) -> bytes:
    """G11 -- subtracts local slot `slot`'s 32-bit value from EAX in place -- `derive`'s own
    Compute codegen (`x - y`). x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_sub_local only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_sub_local(slot)


def emit_mul_local(isa: Isa, slot: int) -> bytes:
    """G11 -- multiplies EAX by local slot `slot`'s 32-bit value in place -- `derive`'s own
    Compute codegen (`x * y`). x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_mul_local only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_mul_local(slot)


def emit_div_local(isa: Isa, slot: int) -> bytes:
    """G11 -- integer-divides EAX by local slot `slot`'s 32-bit value in place (`cdq` sign-
    extends EAX into EDX first, since `idiv`'s r/m32 form divides EDX:EAX) -- `derive`'s own
    Compute codegen (`x / y`). x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_div_local only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_div_local(slot)


def emit_shl_rax_32(isa: Isa) -> bytes:
    """G3 -- shifts a match flag into RAX's high 32 bits, the mirror of `emit_shift_right_32` --
    the second half of packing `(match << 32) | value`. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_shl_rax_32 only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_shl_rax_32()


def emit_or_local64(isa: Isa, slot: int) -> bytes:
    """G3 -- ORs a local slot's full 64-bit value into RAX in place -- combines the shifted
    match flag with the target field's value into the final packed result. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_or_local64 only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_or_local64(slot)


def emit_eax_is_zero(isa: Isa) -> bytes:
    """G3 -- EAX becomes 1 if it was 0, else 0 -- the "checksum matches" test `verified field`
    access needs. x86-64 only so far."""
    if isa != "x86_64":
        raise UnsupportedIsa(f"emit_eax_is_zero only supports isa='x86_64' so far, got {isa!r}")
    return _x86_64.emit_eax_is_zero()


def collect_arrays(goal: Goal) -> dict[str, list[int]]:
    """Pure AST analysis (no allocation, no side effects, no execution): returns every array a
    goal's `Remember` statements declare, name -> its literal integer elements, in
    first-appearance order, recursing into `Branch`/`Loop` bodies exactly like `_collect_symbols`
    does. The CLOSED tree (`madras.dsl`) calls this to know what to actually allocate+populate
    via a real allocator BEFORE calling `compile_goal` -- `compile_goal` itself never allocates
    (D58), it only reads back whatever real addresses the caller resolved."""
    arrays: dict[str, list[int]] = {}
    _collect_arrays_into(goal.body, arrays)
    return arrays


def _collect_arrays_into(stmts: list, arrays: dict[str, list[int]]) -> None:
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Remember) and isinstance(stmt.value, ArrayLiteral):
            arrays[stmt.key] = [int(e) for e in stmt.value.elements]
        elif isinstance(stmt, Branch):
            _collect_arrays_into(stmt.then, arrays)
            _collect_arrays_into(stmt.otherwise, arrays)
        elif isinstance(stmt, Loop):
            _collect_arrays_into(stmt.body, arrays)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_arrays_into(arm.body, arrays)


def collect_records(goal: Goal | FnDef) -> dict[str, dict[str, int]]:
    """G3 -- pure AST analysis (no allocation, no side effects, no execution), same shape as
    `collect_arrays`: returns every record a goal's/fn's (G1) `Remember` statements declare,
    name -> its literal integer fields IN DECLARATION ORDER (a real `dict`, insertion-ordered --
    the ordinal IS the field's byte offset, the closed tree's materialization contract),
    recursing into `Branch`/`Loop` bodies. The CLOSED tree calls this to know what to actually
    allocate+populate (fields + the XOR checksum) via a real allocator BEFORE calling
    `compile_goal`/`compile_fndef` -- neither ever allocates (D58)."""
    records: dict[str, dict[str, int]] = {}
    _collect_records_into(goal.body, records)
    return records


def _collect_records_into(stmts: list, records: dict[str, dict[str, int]]) -> None:
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Remember) and isinstance(stmt.value, RecordLiteral):
            records[stmt.key] = {name: int(v) for name, v in stmt.value.fields.items()}
        elif isinstance(stmt, Branch):
            _collect_records_into(stmt.then, records)
            _collect_records_into(stmt.otherwise, records)
        elif isinstance(stmt, Loop):
            _collect_records_into(stmt.body, records)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_records_into(arm.body, records)


def collect_strings(program: Goal | FnDef) -> dict[str, str]:
    """G4 -- pure AST analysis (no allocation, no side effects, no execution), same shape as
    `collect_arrays`/`collect_records`: returns every string a goal's/fn's `Remember` statements
    declare, name -> its literal UTF-8 text, recursing into `Branch`/`Loop` bodies. The CLOSED
    tree calls this to know what to actually allocate+populate (real UTF-8 bytes) via a real
    allocator BEFORE calling `compile_goal`/`compile_fndef` -- neither ever allocates (D58). A
    string is a `(pointer, length)` slice (Zig/Rust precedent) -- the byte length is derivable
    from `len(text.encode())` here, never stored at runtime, same treatment `array_lengths`
    already gets; only the real base ADDRESS needs a caller."""
    strings: dict[str, str] = {}
    _collect_strings_into(program.body, strings)
    return strings


def _collect_strings_into(stmts: list, strings: dict[str, str]) -> None:
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Remember) and isinstance(stmt.value, StringLiteral):
            strings[stmt.key] = stmt.value.text
        elif isinstance(stmt, Branch):
            _collect_strings_into(stmt.then, strings)
            _collect_strings_into(stmt.otherwise, strings)
        elif isinstance(stmt, Loop):
            _collect_strings_into(stmt.body, strings)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_strings_into(arm.body, strings)


def collect_lists(program: Goal | FnDef) -> dict[str, list[int]]:
    """G5 -- pure AST analysis (no allocation, no side effects, no execution), same shape as
    `collect_arrays`: returns every list a goal's/fn's `Remember` statements declare, name ->
    its literal INITIAL integer elements, in declaration order. Unlike `collect_arrays`, this
    is only the STARTING content -- a list is mutable at runtime (`Push`), so the real element
    count after execution can exceed what this reports; the CLOSED tree uses this only to
    materialize the initial chunk chain (real allocation + chunk-linking, same shape a
    `push` performs at runtime) BEFORE `compile_goal`/`compile_fndef` runs (D58)."""
    lists: dict[str, list[int]] = {}
    _collect_lists_into(program.body, lists)
    return lists


def _collect_lists_into(stmts: list, lists: dict[str, list[int]]) -> None:
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Remember) and isinstance(stmt.value, ListLiteral):
            lists[stmt.key] = [int(e) for e in stmt.value.elements]
        elif isinstance(stmt, Branch):
            _collect_lists_into(stmt.then, lists)
            _collect_lists_into(stmt.otherwise, lists)
        elif isinstance(stmt, Loop):
            _collect_lists_into(stmt.body, lists)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_lists_into(arm.body, lists)


def collect_maps(program: Goal | FnDef) -> dict[str, dict[str, int]]:
    """G5 -- pure AST analysis, same shape as `collect_records`: returns every map a goal's/fn's
    `Remember` statements declare, name -> its literal integer fields IN DECLARATION ORDER (a
    real `dict`, insertion-ordered) -- the SAME "position is the address" contract
    `collect_records` already has, except a map's position is a runtime chunk-chain HOP COUNT,
    not a fixed byte offset (the honest v0 boundary `FieldAccess`-on-a-map draws: only keys
    inserted by THIS literal are resolvable, not a later `mapset`)."""
    maps: dict[str, dict[str, int]] = {}
    _collect_maps_into(program.body, maps)
    return maps


def _collect_maps_into(stmts: list, maps: dict[str, dict[str, int]]) -> None:
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Remember) and isinstance(stmt.value, MapLiteral):
            maps[stmt.key] = {name: int(v) for name, v in stmt.value.fields.items()}
        elif isinstance(stmt, Branch):
            _collect_maps_into(stmt.then, maps)
            _collect_maps_into(stmt.otherwise, maps)
        elif isinstance(stmt, Loop):
            _collect_maps_into(stmt.body, maps)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_maps_into(arm.body, maps)


def _collect_map_set_targets_into(stmts: list, targets: set[str]) -> None:
    """G5 -- every map name a `MapSet` anywhere in the program grows. A REAL correctness gap
    caught by live execution, not assumed: `mapset` PREPENDS onto the SAME head slot a
    `MapLiteral`'s own fields chain from, so it silently SHIFTS every existing field's hop count
    by one -- a plain `FieldAccess` compiled against the ORIGINAL (pre-`mapset`) hop count would
    then read the WRONG chunk, not raise an error. `compile_goal`/`compile_fndef` use this to
    drop any such map from `map_field_positions` entirely -- `FieldAccess`-on-a-map for a
    `mapset`-mutated map fails LOUDLY (`UnsupportedNode`), never silently returns stale data."""
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, MapSet):
            targets.add(stmt.map_name)
        elif isinstance(stmt, Branch):
            _collect_map_set_targets_into(stmt.then, targets)
            _collect_map_set_targets_into(stmt.otherwise, targets)
        elif isinstance(stmt, Loop):
            _collect_map_set_targets_into(stmt.body, targets)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_map_set_targets_into(arm.body, targets)


def _stmts_have_match(stmts: list) -> bool:
    """G9 -- does this statement list contain a `match` anywhere (recursing into Branch/Loop/
    Match's own arms)? `compile_goal`/`compile_fndef` use this to reserve `__match_tag__`
    unconditionally on USE, the SAME over-reservation-on-use pattern `__verify_scratch__`/
    `__push_scratch__`/`__ffi_frame__` already established (a harmless 8 bytes, never wrong,
    even for a program whose only `match` turns out to be literal-only and never needs it)."""
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Match):
            return True
        if isinstance(stmt, Branch) and (
            _stmts_have_match(stmt.then) or _stmts_have_match(stmt.otherwise)
        ):
            return True
        if isinstance(stmt, Loop) and _stmts_have_match(stmt.body):
            return True
    return False


def _stmts_have_compute(stmts: list) -> bool:
    """G11 -- does this (already-lowered) statement list contain a `Remember(Compute(...))`
    anywhere (recursing into Branch/Loop/Match, the SAME shape `_stmts_have_match` already has)?
    `compile_goal`/`compile_fndef` use this to reserve `__compute_scratch__` unconditionally on
    USE, the SAME over-reservation-on-use pattern `__verify_scratch__`/`__match_tag__` already
    established -- a harmless 8 bytes, never wrong, even when every `Compute`'s right operand
    happens to already be a bound name (no literal ever needs materializing into it)."""
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Remember) and isinstance(stmt.value, Compute):
            return True
        if isinstance(stmt, Branch) and (
            _stmts_have_compute(stmt.then) or _stmts_have_compute(stmt.otherwise)
        ):
            return True
        if isinstance(stmt, Loop) and _stmts_have_compute(stmt.body):
            return True
        if isinstance(stmt, Match) and any(_stmts_have_compute(arm.body) for arm in stmt.arms):
            return True
    return False


def _stmts_have_cached_call_with_args(stmts: list) -> bool:
    """N2 -- does this statement list contain a `cached bind` whose call has ARGUMENTS anywhere
    (recursing into Branch/Loop/Match, the SAME shape `_stmts_have_match`/`_stmts_have_compute`
    already have)? `compile_goal`/`compile_fndef` use this to reserve the content-addressed
    probe loop's scratch slots unconditionally on USE, the same over-reservation-on-use pattern
    every other scratch-slot family already established."""
    for stmt in _validate_and_filter(stmts):
        if (
            isinstance(stmt, Bind)
            and stmt.cached
            and isinstance(stmt.call, Call)
            and stmt.call.args
        ):
            return True
        if isinstance(stmt, Branch) and (
            _stmts_have_cached_call_with_args(stmt.then)
            or _stmts_have_cached_call_with_args(stmt.otherwise)
        ):
            return True
        if isinstance(stmt, Loop) and _stmts_have_cached_call_with_args(stmt.body):
            return True
        if isinstance(stmt, Match) and any(
            _stmts_have_cached_call_with_args(arm.body) for arm in stmt.arms
        ):
            return True
    return False


# N2 -- the content-addressed cache probe's internal scratch slots (reserved on USE, same
# pattern __compute_scratch__/__verify_scratch__ already established). Capacity is fixed at 4
# (a power of 2, small but enough for real per-call-site reuse; a real growth/eviction policy
# is disclosed, separate future work -- see N2's plan row).
_CACHE_PROBE_CAPACITY = 4
_CACHE_ARG = "__cache_arg__"
_CACHE_IDX = "__cache_idx__"
_CACHE_TRIES = "__cache_tries__"
_CACHE_CAP = "__cache_cap__"
_CACHE_DOUBLED = "__cache_doubled__"
_CACHE_SLOT_KEY = "__cache_slot_key__"
_CACHE_PROBE_SCRATCH = (
    _CACHE_ARG,
    _CACHE_IDX,
    _CACHE_TRIES,
    _CACHE_CAP,
    _CACHE_DOUBLED,
    _CACHE_SLOT_KEY,
)


def _emit_cached_call_with_arg(
    *,
    isa: Isa,
    abi: Abi,
    backend,
    op: Bind,
    arg_slot: int,
    target_slot: int,
    table_addr: int,
    symbols: dict[str, int],
    call_body,
) -> bytes:
    """N2 -- content-addressed cache probe: `cached bind r = call f(arg)` where `arg`'s VALUE
    (not just the call SITE) determines whether this is a real cache hit. T8.17's original
    cached-Bind path (above) is call-SITE-addressed only -- confirmed live to return STALE data
    for a cached call whose argument varies across invocations of the same site (a `Loop`'s own
    induction variable, `test_kollan_cache_loop_risk.py`). This is the fix: a small (capacity 4)
    open-addressed hash table, LIVE at `table_addr`, one per cached-with-args call SITE (still
    scoped per-mugavari_id, not yet shared globally across different call sites -- disclosed,
    separate future work, matching Unison's fuller content-addressing ambition).

    Radar-grounded (s56): Unison's content-addressed code (identity = hash of structure, not
    position) is the pattern; the STRUCTURE itself (a bounded open-addressed table, linear
    probing on collision) is this row's own minimal, correct mechanism -- not RadixAttention's
    radix tree, which doesn't generalize to a non-sequence key (see the radar's own correction).

    Slot layout: 16 bytes = [key:int64][value:int64], capacity 4, empty sentinel key = -1 (a
    disclosed v0 boundary: an actual argument value of exactly -1 is reserved, not cacheable).
    On table-full (every slot probed, no match, no empty), gracefully degrades to an UNCACHED
    real call rather than corrupting anything or looping forever -- the probe is bounded to
    `_CACHE_PROBE_CAPACITY` iterations by construction, not by a defensive guess."""
    cap = _CACHE_PROBE_CAPACITY
    arg = symbols[_CACHE_ARG]
    idx = symbols[_CACHE_IDX]
    tries = symbols[_CACHE_TRIES]
    cap_slot = symbols[_CACHE_CAP]
    doubled = symbols[_CACHE_DOUBLED]
    slot_key = symbols[_CACHE_SLOT_KEY]

    # 1. stash the arg as a clean, zero-extended 64-bit value (a 32-bit load into EAX always
    #    zero-extends RAX's upper 32 bits, an x86-64 architectural guarantee -- the 64-bit STORE
    #    right after it is what makes later 64-bit reads/compares of this slot safe; storing via
    #    the 32-bit `emit_store_local` would leave the upper 32 bits as uninitialized stack
    #    garbage, a real bug caught by reasoning through the encoding before it shipped, the
    #    SAME "reasoned through, not live" class of bug G11's own operand-ordering fix was).
    setup = (
        backend.emit_load_local(arg_slot)
        + emit_store_local64(isa, arg)
        + backend.emit_load_immediate(cap)
        + backend.emit_store_local(cap_slot)
        + backend.emit_load_local(arg_slot)
        + emit_div_local(isa, cap_slot)
        + emit_mov_eax_edx(isa)
        + backend.emit_store_local(idx)
        + backend.emit_load_immediate(0)
        + backend.emit_store_local(tries)
    )

    def _load_table_base_and_doubled_rcx() -> bytes:
        # A REAL bug caught by isolated live testing (an access violation, root-caused by
        # decoding + hand-verifying every jump target first -- ruled OUT -- then narrowing to
        # this): `emit_load_local_into_rcx` is a 64-BIT load, but a 32-bit `emit_store_local`
        # leaves `doubled`'s upper 32 bits as UNINITIALIZED STACK GARBAGE -- RCX (the SIB index
        # register for every keyed-slot read/write) could carry that garbage into the address
        # computation, corrupting memory unpredictably. Same bug CLASS already fixed for
        # `_CACHE_ARG`'s stash, missed here -- `emit_store_local64` (a clean, explicitly
        # zero-extended 64-bit store, EAX's upper 32 bits are ALWAYS zero after a 32-bit load)
        # is the fix, not a wider read.
        return (
            backend.emit_load_local(idx)
            + emit_add_local(isa, idx)
            + emit_store_local64(isa, doubled)
            + emit_load_local_into_rcx(isa, doubled)
            + emit_load_immediate64(isa, table_addr)
        )

    # Everything below is assembled STRICTLY BOTTOM-UP: every jump distance is `len()` of a
    # concrete bytes object that already exists at the point it's computed -- never a formula
    # re-deriving another block's length in parallel (the earlier draft's real mistake, caught
    # before it was tested). Every jump in this construct uses the NEAR (rel32) form
    # (`emit_jump32`/`emit_je32`) -- found NEEDED, not planned: one probe iteration measures
    # ~185 bytes, the backward loop-branch's own distance ~240 -- both past rel8's +-127 range.

    # Physically LAST alternative: no trailing jump needed, falls through to whatever
    # `_compile_ops` appends after this whole cached-Bind statement.
    full_body = call_body + backend.emit_store_local(target_slot)

    # Collision-advance: idx = (idx+1) mod cap, tries += 1, then EAX holds `tries` for the
    # bounded-loop test right after this block.
    advance = (
        backend.emit_load_local(idx)
        + backend.emit_add_immediate(1)
        + backend.emit_store_local(idx)
        + backend.emit_load_local(idx)
        + emit_div_local(isa, cap_slot)
        + emit_mov_eax_edx(isa)
        + backend.emit_store_local(idx)
        + backend.emit_load_local(tries)
        + backend.emit_add_immediate(1)
        + backend.emit_store_local(tries)
        + backend.emit_load_local(tries)
    )
    # `if tries < cap: jump BACKWARD to the iteration's own start; else fall through into
    # `full_body`. The backward jump's own encoded LENGTH is fixed (5 bytes, rel32) regardless
    # of its target value, so its length is known before its target is -- built as a REAL
    # 5-byte instruction here with a placeholder target (0), then reassembled with the correct
    # target once `iteration_head`'s real length is known (same length, different last 4 bytes).
    _backjump_len = len(emit_jump32(isa, 0))
    loop_test = advance + backend.emit_compare_and_jump_if_false("<", cap, _backjump_len)

    # MISS (empty slot found): populate it -- write the key, run the REAL call, store its
    # result both into `r` and the slot's value field. Registers are RECOMPUTED after the call
    # (a real ABI call clobbers every volatile register) -- `_load_table_base_and_doubled_rcx`
    # re-reads `idx`/`doubled` from LOCAL STACK slots, which the call can't touch.
    write_key = (
        _load_table_base_and_doubled_rcx()
        + emit_load_local_into_rdx(isa, arg)
        + emit_store_keyed_slot_from_rdx(isa, 0)
    )
    do_call = call_body + backend.emit_store_local(target_slot)
    write_value = (
        b"\x48\x89\xc2"  # mov rdx, rax -- stash the just-computed result before it's clobbered
        + _load_table_base_and_doubled_rcx()
        + emit_store_keyed_slot_from_rdx(isa, 8)
    )
    miss_populate = write_key + do_call + write_value
    # miss's own trailing jump must skip `loop_test` + the (placeholder-length-only) backward
    # jump + `full_body` to reach "done" -- all three already have known lengths.
    miss_jump_to_end = emit_jump32(isa, len(loop_test) + _backjump_len + len(full_body))
    miss_body = miss_populate + miss_jump_to_end

    # HIT: re-derive the slot address fresh (registers aren't guaranteed live across the
    # earlier compares) and read back the VALUE field.
    hit_body = (
        emit_load_immediate64(isa, table_addr)
        + emit_load_local_into_rcx(isa, doubled)
        + emit_load_keyed_slot(isa, 8)
        + backend.emit_store_local(target_slot)
    )

    # -- the check selecting MISS-empty vs collision: probed slot's key (stashed) vs -1. Built
    #    BEFORE `hit_jump_to_end` -- a REAL bug caught by decoding the emitted bytes and
    #    checking every jump's landing offset by hand (not assumed correct): `hit_jump_to_end`
    #    sits BEFORE this block in the final layout and must skip OVER it too, on top of
    #    `miss_body`/`loop_test`/the backward jump/`full_body` -- omitting these 3 blocks'
    #    length landed `hit_jump_to_end` 28 bytes short, inside `loop_test`'s own `advance`
    #    code instead of past the whole construct. --
    check_empty = (
        emit_load_immediate64(isa, -1)
        + emit_load_local_into_rdx(isa, slot_key)
        + emit_cmp_rax_rdx(isa)
    )
    skip_miss = emit_jump32(isa, len(miss_body))
    branch_to_miss = emit_je32(isa, len(skip_miss))

    hit_jump_to_end = emit_jump32(
        isa,
        len(check_empty)
        + len(branch_to_miss)
        + len(skip_miss)
        + len(miss_body)
        + len(loop_test)
        + _backjump_len
        + len(full_body),
    )

    # -- the check selecting HIT vs (empty-or-collision): probed slot's key vs `arg`. --
    check_arg = emit_load_local_into_rdx(isa, arg) + emit_cmp_rax_rdx(isa)
    skip_hit = emit_jump32(isa, len(hit_body) + len(hit_jump_to_end))
    branch_to_hit = emit_je32(isa, len(skip_hit))

    iteration_head = (
        _load_table_base_and_doubled_rcx()
        + emit_load_keyed_slot(isa, 0)
        + emit_store_local64(isa, slot_key)
        + check_arg
        + branch_to_hit
        + skip_hit
        + hit_body
        + hit_jump_to_end
        + check_empty
        + branch_to_miss
        + skip_miss
        + miss_body
    )
    # The REAL backward jump: from right after `iteration_head + loop_test`, back to
    # `iteration_head`'s own first byte -- computed from the ACTUAL assembled length of both
    # blocks, not a hand re-derivation. A SECOND real bug caught by the same byte-decoder
    # verification: `rel32` is relative to the JUMP INSTRUCTION'S OWN END, not its start --
    # omitting the jump's own length (`_backjump_len`) landed 5 bytes past the true target,
    # inside `setup` instead of at `iteration_head`'s first byte.
    real_backjump = emit_jump32(isa, -(len(iteration_head) + len(loop_test) + _backjump_len))
    assert len(real_backjump) == _backjump_len  # same encoded length, only the target differs

    probe_iteration = iteration_head + loop_test + real_backjump + full_body
    return setup + probe_iteration


def _compute_operands(derive: Derive) -> tuple[str, Literal["+", "-", "*", "/"], str]:
    """G11 -- parse a `Derive`'s raw `expr` text (the SAME flat round-trip representation
    `Branch.condition`/`MatchArm.guard` already use) via `expr.py`'s EXISTING `parse_expr()`/
    `BinOp` machinery, and validate it draws the SAME narrow v0 boundary `lower_branch` already
    established for conditions: exactly one operator, both operands each a bound name or an
    integer literal -- never a nested sub-expression. Returns `(left, op, right)` -- `op` keeps
    `BinOp`'s own narrow `Literal` type (not widened to plain `str`) so it type-checks straight
    into a `Compute` node with no cast needed."""
    try:
        parsed = parse_expr(derive.expr)
    except Exception as exc:
        raise UnsupportedNode(
            f"derive {derive.key!r}'s expression {derive.expr!r} could not be parsed: {exc}"
        ) from exc
    if not isinstance(parsed, BinOp):
        raise UnsupportedNode(
            f"derive {derive.key!r} only supports `name <op> name`/`name <op> literal` so far, "
            f"got {derive.expr!r}"
        )
    if not isinstance(parsed.left, (Name, Num)) or not isinstance(parsed.right, (Name, Num)):
        raise UnsupportedNode(
            f"derive {derive.key!r}'s operands must each be a bound name or an integer literal "
            "(no nested sub-expressions) so far, got {derive.expr!r}"
        )
    left = parsed.left.name if isinstance(parsed.left, Name) else str(parsed.left.value)
    right = parsed.right.name if isinstance(parsed.right, Name) else str(parsed.right.value)
    return left, parsed.op, right


def _collect_derives(stmts: list[Statement]) -> list[Derive]:
    """G11 -- every `Derive` node in this statement list, recursing into Branch/Loop/Match (the
    SAME shape `_collect_symbols` already has) -- NOT into `Parallel`'s own branches (G10: each
    is compiled+lowered separately as its own isolated `FnDef`, so a `derive` inside one only
    ever sees names bound within that same branch, `remember`'s own existing scoping rule).
    Deliberately does NOT go through `_validate_and_filter` (unlike `_stmts_have_match`'s own
    same-shaped walk): this runs on a `fndef.body` BEFORE `_strip_returns_for_symbol_collection`
    (`_lower_derives` must PRESERVE a trailing `Return`, not have it rejected as an unknown
    node kind) -- a real bug caught live, the SAME class G1's own `Return` addition first found
    in `_collect_symbols`/`_collect_fallible_binds`."""
    found: list[Derive] = []
    for stmt in stmts:
        if isinstance(stmt, Derive):
            found.append(stmt)
        elif isinstance(stmt, Branch):
            found.extend(_collect_derives(stmt.then))
            found.extend(_collect_derives(stmt.otherwise))
        elif isinstance(stmt, Loop):
            found.extend(_collect_derives(stmt.body))
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                found.extend(_collect_derives(arm.body))
    return found


def _cascade(changed_name: str, direct_deps: dict[str, set[str]]) -> list[str]:
    """G11 -- every derive key that must recompute (transitively, in a valid topological order)
    when `changed_name` is written -- a fixed-point expansion over `direct_deps` (derive key ->
    the names it reads), so a chain of derives (`derive y = x + 1`, `derive z = y + 1`) cascades
    correctly when `x` changes: `y` first, then `z` (added only once `y` -- one of `z`'s own
    dependencies -- is already in the changed set). Small/simple by design: v0 programs have few
    derives, so an O(D^2) fixed-point scan over `direct_deps` is the honest, un-clever choice."""
    changed = {changed_name}
    order: list[str] = []
    added = True
    while added:
        added = False
        for key, deps in direct_deps.items():
            if key in changed:
                continue
            if deps & changed:
                changed.add(key)
                order.append(key)
                added = True
    return order


def _written_name(stmt: Statement) -> str | None:
    """G11 -- the name a statement binds, if any (`Remember.key`/`Bind.target`) -- what
    `_lower_derives` checks after every statement to decide whether any derive needs to
    re-cascade. `Push`/`MapSet` mutate a list/map in place, not a scalar a `derive` could ever
    depend on (v0's `Compute` operands are names/integer literals only) -- correctly excluded."""
    if isinstance(stmt, Remember):
        return stmt.key
    if isinstance(stmt, Bind):
        return stmt.target
    return None


def _lower_derives_stmts(
    stmts: list[Statement],
    derive_of: dict[str, tuple[str, Literal["+", "-", "*", "/"], str]],
    direct_deps: dict[str, set[str]],
    declared: set[str],
) -> list[Statement]:
    """G11 -- the actual compile-time rewrite: replace each `Derive` with its own initial
    `Remember(key, Compute(...))`, and after EVERY statement that writes a tracked dependency
    (anywhere in this straight-line scope -- top-level, or inside a `Branch`/`Loop`/`Match` arm
    it recurses into, the SAME scoping `remember` itself already has), splice in re-emitted
    copies of every derive that cascades from it, in topological order. Mirrors `_lower_result_
    arms`/`_lower_literal_arms`'s own "reuse existing nodes" shape (G9) -- `_compile_ops` never
    sees a raw `Derive`, only the `Remember`/`Compute` it lowers to, zero new stencils needed for
    the REACTIVITY mechanism itself (only `Compute`'s own arithmetic codegen is genuinely new).

    `declared` -- the set of derive keys already emitted via their OWN declaration point earlier
    in this same compile-time pass, mutated in place as processing proceeds sequentially (a
    Loop's body is still ONE sequential path in program structure, even though it runs multiple
    times at runtime, so writes inside it correctly mark the outer scope's `declared` too;
    Branch/Match arms are MUTUALLY EXCLUSIVE alternatives -- each gets an ISOLATED COPY, since a
    derive declared in one arm was never guaranteed to have run if a DIFFERENT arm executes).

    A real bug found live, not assumed (caught composing N3's dot-product accumulator,
    `derive sum = sum + x` inside a `Loop`): before `declared` existed, `_cascade` had no notion
    of "has this derive's own declaration point even been reached yet in program order" -- a
    write to `x` BEFORE `sum`'s derive was textually declared (its declaration sits inside a
    LATER loop) still triggered a premature cascade re-emit of `sum`, computing an extra,
    unintended addition before the loop's own iterations even started. Filtering the cascade to
    `declared` keys only fixes this without touching the cascade's own topological-order logic
    (`_cascade` itself was already correct; the bug was calling it with keys that hadn't
    "existed" yet)."""

    def _recompute(key: str) -> Remember:
        left, op, right = derive_of[key]
        return Remember(key=key, value=Compute(left=left, op=op, right=right))

    out: list[Statement] = []
    for stmt in stmts:
        if isinstance(stmt, Derive):
            stmt = _recompute(stmt.key)
            declared.add(stmt.key)
        elif isinstance(stmt, Branch):
            stmt = stmt.model_copy(
                update={
                    "then": _lower_derives_stmts(stmt.then, derive_of, direct_deps, set(declared)),
                    "otherwise": _lower_derives_stmts(
                        stmt.otherwise, derive_of, direct_deps, set(declared)
                    ),
                }
            )
        elif isinstance(stmt, Loop):
            stmt = stmt.model_copy(
                update={"body": _lower_derives_stmts(stmt.body, derive_of, direct_deps, declared)}
            )
        elif isinstance(stmt, Match):
            stmt = stmt.model_copy(
                update={
                    "arms": [
                        arm.model_copy(
                            update={
                                "body": _lower_derives_stmts(
                                    arm.body, derive_of, direct_deps, set(declared)
                                )
                            }
                        )
                        for arm in stmt.arms
                    ]
                }
            )
        out.append(stmt)
        written = _written_name(stmt)
        if written is not None:
            for key in _cascade(written, direct_deps):
                if key in declared:
                    out.append(_recompute(key))
    return out


def _lower_derives(stmts: list[Statement]) -> list[Statement]:
    """G11 -- `compile_goal`/`compile_fndef`'s own entry point: collect every `derive` anywhere
    in `stmts` (recursing into Branch/Loop/Match), validate + parse each one's expression, build
    its direct-dependency set, then rewrite the WHOLE statement list. Returns a new list --
    `stmts` itself is never mutated, matching every other AST-rewrite pass in this module
    (`_strip_returns_for_symbol_collection`, `_lower_result_arms`)."""
    derives = _collect_derives(stmts)
    if not derives:
        return stmts
    derive_of: dict[str, tuple[str, Literal["+", "-", "*", "/"], str]] = {}
    direct_deps: dict[str, set[str]] = {}
    for d in derives:
        left, op, right = _compute_operands(d)
        derive_of[d.key] = (left, op, right)
        deps = set()
        for operand in (left, right):
            try:
                int(operand)
            except ValueError:
                deps.add(operand)
        direct_deps[d.key] = deps
    return _lower_derives_stmts(stmts, derive_of, direct_deps, set())


def _walk_parallels(stmts: list[Statement]) -> list[Parallel]:
    """G10 -- every `Parallel` node in this statement list, recursing into `Branch.then`/
    `Branch.otherwise`/`Loop.body`/`Match` arm bodies (the SAME recursion shape
    `_stmts_have_match` already has) -- but deliberately NOT into a `Parallel`'s own `body`:
    nesting one `parallel` inside another's branch is out of v0 scope (each branch is compiled
    as an isolated 0-arg `FnDef`, and `compile_fndef` has no notion of a nested `parallel`
    needing its OWN placement pass). `compile_goal`/`compile_fndef` use this list for two
    things that must stay in sync (the SAME list, walked once): reserving each branch's own
    `__parallel_{id(p)}_handle_{i}__` slot, and (the closed tree's job) `extract_parallel_
    branches`'s own placement."""
    found: list[Parallel] = []
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Parallel):
            found.append(stmt)
        elif isinstance(stmt, Branch):
            found.extend(_walk_parallels(stmt.then))
            found.extend(_walk_parallels(stmt.otherwise))
        elif isinstance(stmt, Loop):
            found.extend(_walk_parallels(stmt.body))
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                found.extend(_walk_parallels(arm.body))
    return found


def extract_parallel_branches(stmts: list[Statement]) -> dict[str, FnDef]:
    """G10 -- pure AST analysis (no allocation, no execution, D58): every `Parallel` node's own
    top-level statements, each synthesized into its own independently-compilable, 0-arg `FnDef`
    named `__parallel_{id(p)}_branch_{i}__` (keyed by the `Parallel` node's own Python object
    identity, stable across a single compile call's placeholder-then-real-address passes --
    the SAME two-pass scheme `run_compiled_fndefs` already established, G1). The CLOSED tree
    (`madras.dsl.kollan`) places each of these at a real executable address BEFORE compiling
    the enclosing goal/fn, then hands their addresses back in via `capability_addresses` --
    the same "resolved-address-in, bytes-out" shape every other capability already has."""
    branches: dict[str, FnDef] = {}
    for p in _walk_parallels(stmts):
        for i, branch_stmt in enumerate(p.body):
            name = f"__parallel_{id(p)}_branch_{i}__"
            # `compile_fndef` requires an explicit trailing `return` (unlike `compile_goal`'s
            # own tail-call-keeps-its-ret shape) -- a thread body's return value is discarded
            # (the OS thread-proc convention this lowers to has one but nothing reads it), so a
            # synthesized `return 0` closes every branch out, regardless of its own last
            # statement's shape.
            branches[name] = FnDef(name=name, params=[], body=[branch_stmt, Return(value="0")])
    return branches


def collect_recalls(goal: Goal) -> set[str]:
    """T8.14 -- pure AST analysis (no allocation, no side effects, no execution): returns every
    distinct key a goal's `Remember` statements recall (`Remember(key, Recall(other_key))`),
    recursing into `Branch`/`Loop` bodies exactly like `collect_arrays` does. v0 scope: only
    `Remember`'s own value position -- a `Recall` used as a `Call` argument or a `Loop`/`Range`
    bound is a distinct, not-yet-tackled gap (same honest incremental scoping T8.10 already drew
    around `Recall`-bound ranges). The CLOSED tree resolves each key to a real provider BEFORE
    calling `compile_goal` -- this only reports what needs resolving."""
    recalls: set[str] = set()
    _collect_recalls_into(goal.body, recalls)
    return recalls


def _collect_recalls_into(stmts: list, recalls: set[str]) -> None:
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Remember) and isinstance(stmt.value, Recall):
            recalls.add(stmt.value.key)
        elif isinstance(stmt, Branch):
            _collect_recalls_into(stmt.then, recalls)
            _collect_recalls_into(stmt.otherwise, recalls)
        elif isinstance(stmt, Loop):
            _collect_recalls_into(stmt.body, recalls)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_recalls_into(arm.body, recalls)


def cached_key_map(program: Goal | FnDef) -> dict[int, str]:
    """Map each cached `Bind` (by object identity) to its IR Mugavari address.

    The cache key moves to the IR here. It cannot be read off `_compile_ops`' own ops: those come
    from `lower_each`, which lowers each statement list independently and therefore has no
    whole-module context. Addressing per statement list would be WRONG rather than merely absent
    -- every recursive `_compile_ops` call restarts depth and order at zero, so a top-level cached
    bind and one inside a branch would collide, which is precisely the stale-result bug
    `test_kollan_cache_loop_risk.py` pins.

    So the module is addressed ONCE here and paired with the AST by walk order. Both walks are
    pre-order over the same program -- `_collect_cached_binds_into` visits statements in order and
    recurses where a control-flow statement sits, and `NadiOp.walk` visits an op before its
    regions -- so the two sequences correspond element-for-element. `strict=True` makes a
    divergence an error rather than a silent mis-pairing.

    Keyed by `id()` deliberately: object identity is stable within a single compile call, the same
    property `extract_parallel_branches` already relies on for its branch names.
    """
    module = lower_to_nadi(program)
    assign_mugavari_ids(module)
    ir_ids = [
        op.mugavari_id
        for op in module.walk()
        if op.kind == "compose-bind" and op.attrs.get("cached") and op.mugavari_id is not None
    ]
    ast_binds: list[Bind] = []
    _collect_cached_bind_nodes(program.body, ast_binds)
    return {id(b): i for b, i in zip(ast_binds, ir_ids, strict=True)}


def _collect_cached_bind_nodes(stmts: list, out: list) -> None:
    """Pre-order, mirroring `_collect_cached_binds_into`'s ORDER so the sequence matches the IR's.

    Deliberately does NOT go through `_validate_and_filter`, unlike its sibling: that helper
    rejects `Return`, which `compile_fndef` REQUIRES every fn to end with -- the latent limitation
    that makes the AST collectors unusable on any real `FnDef`. Validation is not this walk's job
    anyway; it only needs to find cached binds in program order, and a `Govern` or `Return` is
    simply not one."""
    for stmt in stmts:
        if isinstance(stmt, Bind) and stmt.cached:
            out.append(stmt)
        elif isinstance(stmt, Branch):
            _collect_cached_bind_nodes(stmt.then, out)
            _collect_cached_bind_nodes(stmt.otherwise, out)
        elif isinstance(stmt, Loop):
            _collect_cached_bind_nodes(stmt.body, out)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_cached_bind_nodes(arm.body, out)


def collect_cached_binds(goal: Goal) -> set[str]:
    """T8.17 -- pure AST analysis (no allocation, no side effects, no execution): returns every
    Mugavari ID a goal's cached `Bind` statements need a real result-cache slot for, recursing
    into `Branch`/`Loop` bodies exactly like `collect_arrays`/`collect_recalls` do. Requires
    `mugavari.assign_ids(goal)` to have already run -- a cached Bind with no `mugavari_id` raises
    `UnsupportedNode`, since caching without a stable per-call-site key is meaningless. The CLOSED
    tree (`madras.dsl.kollan_cache.ResultCache`) resolves each ID to a real, persistent arena
    address BEFORE calling `compile_goal` -- this only reports what needs resolving."""
    ids: set[str] = set()
    _collect_cached_binds_into(goal.body, ids)
    return ids


def _collect_cached_binds_into(stmts: list, ids: set[str]) -> None:
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Bind) and stmt.cached:
            if stmt.mugavari_id is None:
                raise UnsupportedNode(
                    f"Bind({stmt.target!r}) is cached but has no mugavari_id -- run "
                    "mugavari.assign_ids(goal) before collect_cached_binds()/compile_goal"
                )
            ids.add(stmt.mugavari_id)
        elif isinstance(stmt, Branch):
            _collect_cached_binds_into(stmt.then, ids)
            _collect_cached_binds_into(stmt.otherwise, ids)
        elif isinstance(stmt, Loop):
            _collect_cached_binds_into(stmt.body, ids)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_cached_binds_into(arm.body, ids)


def collect_cached_binds_with_args(goal: Goal) -> set[str]:
    """N2 -- the SUBSET of `collect_cached_binds(goal)`'s Mugavari IDs whose call has
    ARGUMENTS -- these need the content-addressed probe table (16-byte slots * capacity 4,
    KEY fields pre-filled with the -1 empty sentinel), not T8.17's original single 8-byte
    zero-initialized slot. `madras.dsl.kollan_cache.ResultCache.resolve()` uses this to decide
    which allocation shape + initialization each Mugavari ID needs."""
    ids: set[str] = set()
    _collect_cached_binds_with_args_into(goal.body, ids)
    return ids


def _collect_cached_binds_with_args_into(stmts: list, ids: set[str]) -> None:
    for stmt in _validate_and_filter(stmts):
        if (
            isinstance(stmt, Bind)
            and stmt.cached
            and isinstance(stmt.call, Call)
            and stmt.call.args
        ):
            if stmt.mugavari_id is None:
                raise UnsupportedNode(
                    f"Bind({stmt.target!r}) is cached but has no mugavari_id -- run "
                    "mugavari.assign_ids(goal) before collect_cached_binds_with_args()/compile_goal"
                )
            ids.add(stmt.mugavari_id)
        elif isinstance(stmt, Branch):
            _collect_cached_binds_with_args_into(stmt.then, ids)
            _collect_cached_binds_with_args_into(stmt.otherwise, ids)
        elif isinstance(stmt, Loop):
            _collect_cached_binds_with_args_into(stmt.body, ids)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_cached_binds_with_args_into(arm.body, ids)


def _validate_and_filter(stmts: list) -> list:
    """Strip `Govern` (a static spawn-time gate, never compiled to a runtime check); keep
    `Call`/`Bind`/`Remember`/`Branch`/`Loop` as compilable ops (real range/iterable-shape
    validation for `Loop` happens in `_compile_loop`, not here, since it depends on the
    iterable's actual value, not just its kind); raise `UnsupportedNode` for anything else."""
    ops: list = []
    for stmt in stmts:
        if isinstance(stmt, Govern):
            continue
        if isinstance(
            stmt, (Call, Bind, Remember, Branch, Loop, Push, MapSet, Match, Parallel, Derive)
        ):
            ops.append(stmt)
        else:
            raise UnsupportedNode(f"compile_goal doesn't support {stmt.kind!r} yet")
    return ops


def _collect_symbols(stmts: list, symbols: dict[str, int]) -> None:
    """Walk a statement list (recursing into every `Branch.then`/`Branch.otherwise` and
    `Loop.body`) and assign every `Bind.target`/`Remember.key`/`Loop.var` its own slot, in
    first-appearance order -- a flat, goal-wide namespace (no per-branch/per-loop scoping yet),
    matching `Remember`'s existing "must be bound earlier in this same goal" rule."""
    for stmt in _validate_and_filter(stmts):
        if isinstance(stmt, Bind):
            symbols.setdefault(stmt.target, len(symbols))
        elif isinstance(stmt, Remember):
            if isinstance(stmt.value, ArrayLiteral):
                continue  # a compile-time-only declaration (see collect_arrays) -- no runtime
                # slot: the array's real address is resolved entirely outside the symbol table.
            symbols.setdefault(stmt.key, len(symbols))
        elif isinstance(stmt, Branch):
            _collect_symbols(stmt.then, symbols)
            _collect_symbols(stmt.otherwise, symbols)
        elif isinstance(stmt, Loop):
            symbols.setdefault(stmt.var, len(symbols))
            _collect_symbols(stmt.body, symbols)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                if arm.bind is not None:
                    symbols.setdefault(arm.bind, len(symbols))
                _collect_symbols(arm.body, symbols)


_OWNING_VALUE_TYPES = (ListLiteral, MapLiteral, RecordLiteral, StringLiteral)


def check_moves(program: Goal | FnDef) -> None:
    """G6 (plan-local D70, stage 1 of "moves + arenas") -- Austral-style linear/single-pass
    move-checking: a list/map/record/string value has exactly ONE live owning binding at a time.
    Referencing the WHOLE value again by a bare name (as another `Remember`'s value, a `Call`
    argument, or a `Return`) after its first such reference raises `UnsupportedNode` -- a real
    compile-time error, not a runtime bug. v0 scope, deliberately narrow (matches this codebase's
    own "not a full dataflow engine" discipline): only `ListLiteral`/`MapLiteral`/`RecordLiteral`/
    `StringLiteral`-declared names are tracked (a plain int is implicitly `Copy`, same "literal is
    cheap, structure is not" treatment G3/G4/G5 already established); `push`/`mapset`'s own
    name reference is an in-place MUTATION, not a moving use (Rust would call this `&mut self`, a
    BORROW -- stage 2, not built yet, D70's own staging); `ArrayIndex`/`FieldAccess` reads a
    FIELD, never the whole value, so it's never a move either."""
    _check_moves_stmts(program.body, set(), set())


def _check_move_use(value, owning: set[str], moved: set[str]) -> None:
    if not isinstance(value, str) or value not in owning:
        return  # not a bare-name reference to an owning value -- nothing to check
    if value in moved:
        raise UnsupportedNode(
            f"{value!r} is used after being moved -- a list/map/record/string value has a "
            "single owner (G6, plan-local D70); read its fields via ArrayIndex/FieldAccess "
            "instead of referencing the whole name a second time"
        )
    moved.add(value)


def _check_moves_stmts(stmts: list, owning: set[str], moved: set[str]) -> None:
    """Mutates `owning`/`moved` in place for straight-line flow; `Branch`/`Loop` isolate their
    own copies and merge back conservatively (a name moved on ANY path is moved after the
    branch -- the same "moved on some paths" rule Rust's own checker uses, just without a real
    CFG: this is a single recursive AST walk, matching `_collect_symbols`'s own shape)."""
    for stmt in stmts:
        if isinstance(stmt, Govern):
            continue
        if isinstance(stmt, Remember):
            _check_move_use(stmt.value, owning, moved)
            if isinstance(stmt.value, _OWNING_VALUE_TYPES):
                owning.add(stmt.key)
        elif isinstance(stmt, Bind):
            if isinstance(stmt.call, Call):
                for arg in stmt.call.args:
                    _check_move_use(arg, owning, moved)
        elif isinstance(stmt, Call):
            for arg in stmt.args:
                _check_move_use(arg, owning, moved)
        elif isinstance(stmt, Return):
            _check_move_use(stmt.value, owning, moved)
        elif isinstance(stmt, Branch):
            then_moved, otherwise_moved = set(moved), set(moved)
            _check_moves_stmts(stmt.then, owning, then_moved)
            _check_moves_stmts(stmt.otherwise, owning, otherwise_moved)
            moved |= then_moved | otherwise_moved
        elif isinstance(stmt, Match):
            # SAME conservative "moved on any path is moved after" merge as Branch, generalized
            # to N arms (exactly one arm's body ever actually runs, same shape as then/otherwise).
            for arm in stmt.arms:
                arm_moved = set(moved)
                _check_moves_stmts(arm.body, owning, arm_moved)
                moved |= arm_moved
        elif isinstance(stmt, Loop):
            # A loop body might run 0, 1, or many times -- "moved exactly once" can't be proven
            # statically across an unknown iteration count. v0 boundary: ANY owning-name move
            # inside a Loop body is rejected outright, not silently under- or over-approximated.
            loop_moved: set[str] = set()
            _check_moves_stmts(stmt.body, owning, loop_moved)
            if loop_moved:
                raise UnsupportedNode(
                    f"a Loop body moves {sorted(loop_moved)!r} -- a value can't be statically "
                    "proven moved exactly once across an unknown number of iterations (G6 v0 "
                    "boundary); restructure so the loop body only reads/grows "
                    "(ArrayIndex/FieldAccess/push/mapset), never moves, a value bound outside it"
                )
        # Push/MapSet: an in-place mutation reference (v0 scope, see check_moves' own docstring),
        # never a moving use.


def _collect_fallible_binds(stmts: list, names: set[str]) -> None:
    """Walk a statement list (recursing into Branch/Loop bodies, same shape as `_collect_symbols`)
    and record every `Bind.target` whose call is `capability_kind="fallible"` OR a G3
    `VerifiedFieldAccess` -- T8.16's own correctness check, extended: `is_ok(name)`/
    `payload(name)` must reference a name that genuinely holds a packed `(tag, value)` result
    (whether from a `fallible` call or a verified field read -- the SAME packed-result shape),
    not a plain scalar (whose high 32 bits would be undefined stack garbage, not a real tag).
    `VerifiedFieldAccess` has no `capability_kind` attribute at all (it isn't a `Call`) -- checked
    via `isinstance` FIRST, not assumed, the same class of bug G1's `Return` addition already
    found once in `_validate_and_filter`'s own callers."""
    for stmt in _validate_and_filter(stmts):
        if (
            isinstance(stmt, Bind)
            and isinstance(stmt.call, Project)
            and stmt.call.selector == "verified-field"
        ):
            names.add(stmt.target)
        elif (
            isinstance(stmt, Bind)
            and isinstance(stmt.call, Call)
            and stmt.call.capability_kind == "fallible"
        ):
            names.add(stmt.target)
        elif isinstance(stmt, Branch):
            _collect_fallible_binds(stmt.then, names)
            _collect_fallible_binds(stmt.otherwise, names)
        elif isinstance(stmt, Loop):
            _collect_fallible_binds(stmt.body, names)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_fallible_binds(arm.body, names)


def _collect_verified_field_binds(stmts: list, names: set[str]) -> None:
    """G9 -- the narrower sibling of `_collect_fallible_binds`, recording ONLY `VerifiedFieldAccess`
    Binds. A REAL bug found live, not assumed: `fallible`'s own packed-tag convention (T8.15) is
    `0=ok, 1=err`, but `VerifiedFieldAccess`'s (G3) is the OPPOSITE -- `1=match(ok), 0=corrupted
    (err)`, since a checksum MATCHING is the "good" case. `_lower_result_arms` needs to know
    WHICH convention a given `match` scrutinee uses to pick the correct polarity for its `ok`/
    `err` arms -- `fallible_binds` alone conflates both under one flat set (correct for `is_ok`/
    `payload`'s own "just read the raw tag, the PROGRAM decides what it means" philosophy, wrong
    for `match`'s own `ok`/`err` pattern NAMES, which must mean the SAME thing regardless of
    which mechanism produced the value)."""
    for stmt in _validate_and_filter(stmts):
        if (
            isinstance(stmt, Bind)
            and isinstance(stmt.call, Project)
            and stmt.call.selector == "verified-field"
        ):
            names.add(stmt.target)
        elif isinstance(stmt, Branch):
            _collect_verified_field_binds(stmt.then, names)
            _collect_verified_field_binds(stmt.otherwise, names)
        elif isinstance(stmt, Loop):
            _collect_verified_field_binds(stmt.body, names)
        elif isinstance(stmt, Match):
            for arm in stmt.arms:
                _collect_verified_field_binds(arm.body, names)


_NEGATE_OP: dict[str, str] = {">=": "<", ">": "<=", "<=": ">", "<": ">=", "==": "!=", "!=": "=="}


def _compile_loop(
    loop: Loop,
    *,
    irop: NadiOp,
    isa: Isa,
    abi: Abi = "win64",
    backend,
    capability_addresses: dict[str, int],
    array_addresses: dict[str, int],
    array_lengths: dict[str, int],
    record_addresses: dict[str, int],
    string_addresses: dict[str, int],
    list_addresses: dict[str, int],
    map_addresses: dict[str, int],
    map_field_positions: dict[str, dict[str, int]],
    record_field_offsets: dict[str, dict[str, int]],
    recall_addresses: dict[str, int],
    fallible_binds: set[str],
    verified_field_binds: set[str],
    cache_addresses: dict[str, int],
    cached_keys: dict[int, str],
    python_api_addr: int | None,
    symbols: dict[str, int],
    ret_len: int,
    induction_ranges: dict[str, tuple[int, int]] | None = None,
) -> bytes:
    """Compiles `for var in range(start, stop) { body }` via **loop rotation** (the standard
    LLVM/GCC while->do-while canonicalization, researched before writing this): test once
    before entry (skip the loop entirely if empty), then a single back-edge test per
    iteration -- one branch per iteration, not two. `start`/`stop` must both be integer
    literals (a `Recall`-bound range, or any iterable that isn't a `RangeLiteral` at all --
    i.e. a real array/collection -- raises `UnsupportedNode`: no native array representation
    exists, and this is a genuinely separate, undesigned data-layout question a loop compiler
    alone can't resolve)."""
    # MIGRATED (row 3c-ii, slice 8): the loop's own fields come from the IR. A `range` iterable
    # is `type="range"` with its bounds in attrs; anything else -- including a `Recall` bound,
    # which lowers to an operand rather than a literal -- simply isn't that shape, so the two
    # separate isinstance rejections below collapse into one check on the form.
    # BOTH bounds, not just `start`: a `Recall` bound is a structured value, which the lowering
    # records as an operand rather than an attr, so `range(0, recall(n))` yields `start` present
    # and `stop` ABSENT. Checking only `start` let that fall through to a KeyError instead of the
    # fail-closed rejection -- caught by `test_compile_goal_rejects_a_recall_bound_range`.
    if irop.attrs.get("type") != "range" or not {"start", "stop"} <= irop.attrs.keys():
        raise UnsupportedNode(
            "compile_goal only supports `range(start, stop)` iteration with integer-literal "
            "bounds so far -- a real array/collection has no native representation yet (a "
            "separate, undesigned data-layout question), and a Recall bound needs the same "
            "native<->Python FFI callback bridge Remember's Recall case does, not built yet"
        )
    raw_start, raw_stop = irop.attrs["start"], irop.attrs["stop"]
    try:
        start, stop = int(raw_start), int(raw_stop)
    except ValueError as exc:
        raise UnsupportedNode(
            f"compile_goal's range(...) bounds must be integer literals, got "
            f"{raw_start!r}/{raw_stop!r}"
        ) from exc

    i_slot = symbols[str(irop.attrs["var"])]
    init = backend.emit_load_immediate(start) + backend.emit_store_local(i_slot)
    # N1a -- `start`/`stop` were just forced to be integer literals above, so this loop's
    # induction variable has a PROVEN compile-time range. Publish it to the body so an
    # `arr[i]` inside can be bounds-proven and compile to a check-free indexed load. Shadowing
    # is handled naturally: an inner loop reusing the same var name overwrites the entry,
    # matching `_collect_symbols`'s own flat, last-binding-wins namespace.
    body_ranges = {**(induction_ranges or {}), str(irop.attrs["var"]): (start, stop)}
    body_bytes = _compile_ops(
        loop.body,
        isa=isa,
        abi=abi,
        backend=backend,
        capability_addresses=capability_addresses,
        array_addresses=array_addresses,
        array_lengths=array_lengths,
        record_addresses=record_addresses,
        string_addresses=string_addresses,
        list_addresses=list_addresses,
        map_addresses=map_addresses,
        map_field_positions=map_field_positions,
        record_field_offsets=record_field_offsets,
        recall_addresses=recall_addresses,
        fallible_binds=fallible_binds,
        verified_field_binds=verified_field_binds,
        cache_addresses=cache_addresses,
        cached_keys=cached_keys,
        python_api_addr=python_api_addr,
        symbols=symbols,
        ret_len=ret_len,
        tail_keep_ret=False,
        induction_ranges=body_ranges,
    )
    increment = (
        backend.emit_load_local(i_slot)
        + backend.emit_add_immediate(1)
        + backend.emit_store_local(i_slot)
    )

    # range(start, stop) is always `i < stop` -- the fixed comparison every `for i in range(...)`
    # loop reduces to (no other operator makes sense for a range's own semantics).
    op = "<"
    test1_load = backend.emit_load_local(i_slot)
    test2_load = backend.emit_load_local(i_slot)

    # test2's length is invariant to the skip value itself (fixed-width encoding either way) --
    # build once with a placeholder to learn the real length, then rebuild with the true
    # (negative, backward-pointing) offset once everything before it is known.
    test2_len = len(backend.emit_compare_and_jump_if_false(_NEGATE_OP[op], stop, 0))
    back_to_body = -(len(body_bytes) + len(increment) + len(test2_load) + test2_len)
    # N2 -- found NEEDED, not planned: a large `cached`-with-args call inside this loop's body
    # can push `back_to_body` past rel8's +-127 range. x86-64 only so far (RISC-V's own branch
    # encoding has a different, wider immediate and was never observed to need this -- widening
    # it is real, separate future work, not attempted here; a cached-with-args call is already
    # x86-64-only, so RISC-V never reaches this via that path). When it doesn't fit, redo the
    # SAME two-pass measurement with `force_near=True` -- the length changes (7 bytes -> 11), so
    # `back_to_body` must be recomputed against the NEW length, not just re-encoded.
    force_near = isa == "x86_64" and not (-128 <= back_to_body <= 127)
    if force_near:
        test2_len = len(
            backend.emit_compare_and_jump_if_false(_NEGATE_OP[op], stop, 0, force_near=True)
        )
        back_to_body = -(len(body_bytes) + len(increment) + len(test2_load) + test2_len)
        test2_bytes = backend.emit_compare_and_jump_if_false(
            _NEGATE_OP[op], stop, back_to_body, force_near=True
        )
    else:
        test2_bytes = backend.emit_compare_and_jump_if_false(_NEGATE_OP[op], stop, back_to_body)
    assert len(test2_bytes) == test2_len  # the invariant this whole scheme depends on

    skip_past_loop = len(body_bytes) + len(increment) + len(test2_load) + len(test2_bytes)
    test1_bytes = (
        backend.emit_compare_and_jump_if_false(op, stop, skip_past_loop, force_near=True)
        if force_near
        else backend.emit_compare_and_jump_if_false(op, stop, skip_past_loop)
    )

    return init + test1_load + test1_bytes + body_bytes + increment + test2_load + test2_bytes


def _compile_branch(
    branch: Branch,
    *,
    irop: NadiOp,
    isa: Isa,
    abi: Abi = "win64",
    backend,
    capability_addresses: dict[str, int],
    array_addresses: dict[str, int],
    array_lengths: dict[str, int],
    record_addresses: dict[str, int],
    string_addresses: dict[str, int],
    list_addresses: dict[str, int],
    map_addresses: dict[str, int],
    map_field_positions: dict[str, dict[str, int]],
    record_field_offsets: dict[str, dict[str, int]],
    recall_addresses: dict[str, int],
    fallible_binds: set[str],
    verified_field_binds: set[str],
    cache_addresses: dict[str, int],
    cached_keys: dict[int, str],
    python_api_addr: int | None,
    symbols: dict[str, int],
    ret_len: int,
) -> bytes:
    """Recursively compiles a `Branch`'s condition + both bodies -- arbitrary-length then/else
    statement lists, not `emit_branch`'s fixed two-value select. Only `name <op> literal`
    conditions are supported (the same `Compare(op, Name, Num)` shape `lower_branch` handles);
    the compared name must already be bound by an earlier `Bind`/`Remember` in this goal (the
    only source of local names `compile_goal` knows about -- there's no function-argument path
    for a top-level goal's own condition variable)."""
    # MIGRATED: the condition text comes from the IR. `.tamil` stores conditions as source
    # strings in the AST itself (`Branch.condition: str`), so the IR is faithful to what it was
    # given -- making expressions structured is an AST-level change, upstream of this seam.
    condition = str(irop.attrs["condition"])
    expr = parse_expr(condition)
    if (
        not isinstance(expr, Compare)
        or not isinstance(expr.left, Name)
        or not isinstance(expr.right, Num)
    ):
        raise UnsupportedNode(
            f"compile_goal only supports a `name <op> literal` Branch condition so far "
            f"(got {condition!r}) -- comparing two names or an arithmetic "
            "sub-expression needs a real variable allocator, not built yet"
        )
    if expr.left.name not in symbols:
        raise UnsupportedNode(
            f"Branch condition references {expr.left.name!r}, which isn't a name bound "
            "earlier in this same goal by an earlier Bind/Remember"
        )

    load_cond = backend.emit_load_local(symbols[expr.left.name])
    then_bytes = _compile_ops(
        branch.then,
        isa=isa,
        abi=abi,
        backend=backend,
        capability_addresses=capability_addresses,
        array_addresses=array_addresses,
        array_lengths=array_lengths,
        record_addresses=record_addresses,
        string_addresses=string_addresses,
        list_addresses=list_addresses,
        map_addresses=map_addresses,
        map_field_positions=map_field_positions,
        record_field_offsets=record_field_offsets,
        recall_addresses=recall_addresses,
        fallible_binds=fallible_binds,
        verified_field_binds=verified_field_binds,
        cache_addresses=cache_addresses,
        cached_keys=cached_keys,
        python_api_addr=python_api_addr,
        symbols=symbols,
        ret_len=ret_len,
        tail_keep_ret=False,
    )
    else_bytes = _compile_ops(
        branch.otherwise,
        isa=isa,
        abi=abi,
        backend=backend,
        capability_addresses=capability_addresses,
        array_addresses=array_addresses,
        array_lengths=array_lengths,
        record_addresses=record_addresses,
        string_addresses=string_addresses,
        list_addresses=list_addresses,
        map_addresses=map_addresses,
        map_field_positions=map_field_positions,
        record_field_offsets=record_field_offsets,
        recall_addresses=recall_addresses,
        fallible_binds=fallible_binds,
        verified_field_binds=verified_field_binds,
        cache_addresses=cache_addresses,
        cached_keys=cached_keys,
        python_api_addr=python_api_addr,
        symbols=symbols,
        ret_len=ret_len,
        tail_keep_ret=False,
    )
    jump_over_else = backend.emit_jump(len(else_bytes))
    skip_len = len(then_bytes) + len(jump_over_else)
    compare_and_branch = backend.emit_compare_and_jump_if_false(expr.op, expr.right.value, skip_len)
    return load_cond + compare_and_branch + then_bytes + jump_over_else + else_bytes


def _lower_result_arms(match_op: Match, verified_field_binds: set[str]) -> list:
    """G9 -- Result matching's lowering: `ok(v)`/`err(e)` over a `fallible`/`verified` Bind's
    packed tag (research: Erlang/Elixir's tagged-tuple matching, GPL-LLM-OSS Radar s56) becomes
    `Remember(__match_tag__, ResultOk(scrutinee))` + a SINGLE `Branch` whose arms each prepend a
    `Remember(bind, ResultValue(scrutinee))` before the arm's own body -- reusing `is_ok`/
    `payload`'s existing packed-result reader machinery wholesale, zero new stencils.

    **A real polarity bug found live, not assumed**: `fallible` (T8.15) packs `0=ok, 1=err`, but
    `VerifiedFieldAccess` (G3) packs the OPPOSITE -- `1=match(ok), 0=corrupted(err)`. `match`'s
    `ok`/`err` pattern NAMES must mean the same thing regardless of which mechanism produced the
    value, so `verified_field_binds` (a NEW, narrower sibling of `fallible_binds`) picks the
    correct comparison per scrutinee, instead of a single hardcoded polarity that was silently
    wrong for one of the two mechanisms.

    v0 boundary: exactly two arms (`ok` and `err`, both required, no wildcard -- inherently
    exhaustive since the packed tag is always 0 or 1); no guards (a failed guard on an
    already-exhaustive 2-arm match has no well-defined next arm to fall through to)."""
    ok_arm = next((a for a in match_op.arms if a.pattern == "ok"), None)
    err_arm = next((a for a in match_op.arms if a.pattern == "err"), None)
    if ok_arm is None or err_arm is None or len(match_op.arms) != 2:
        raise UnsupportedNode(
            f"match {match_op.scrutinee!r} matches a Result (a fallible/verified Bind) -- v0 "
            "requires EXACTLY two arms, `ok(name)` and `err(name)` (both present, no wildcard, "
            "no other patterns), inherently exhaustive since the packed tag is always 0 or 1"
        )
    if ok_arm.guard is not None or err_arm.guard is not None:
        raise UnsupportedNode(
            f"match {match_op.scrutinee!r}'s `ok`/`err` arms don't support a guard in v0 -- "
            "Result matching is exhaustive by construction, so a failed guard would have no "
            "well-defined next arm to fall through to; guards are only meaningful for literal "
            "matching's ordered arm list"
        )
    ok_body = (
        [
            Remember(
                key=ok_arm.bind, value=Project(source=match_op.scrutinee, selector="result-payload")
            )
        ]
        if ok_arm.bind
        else []
    ) + ok_arm.body
    err_body = (
        [
            Remember(
                key=err_arm.bind,
                value=Project(source=match_op.scrutinee, selector="result-payload"),
            )
        ]
        if err_arm.bind
        else []
    ) + err_arm.body
    ok_tag = 1 if match_op.scrutinee in verified_field_binds else 0
    return [
        Remember(
            key="__match_tag__", value=Project(source=match_op.scrutinee, selector="result-tag")
        ),
        Branch(condition=f"__match_tag__ == {ok_tag}", then=ok_body, otherwise=err_body),
    ]


def _lower_literal_arms(scrutinee: str, arms: list) -> list:
    """G9 -- literal matching's lowering: an ordered arm list becomes a RIGHT-NESTED chain of
    `Branch` nodes (`scrutinee == pattern`), reusing `_compile_branch`'s existing recursive
    compare-and-jump codegen wholesale -- zero new stencils. A guarded arm's failed guard falls
    through to the REST of the arms (a nested `Branch` whose `otherwise` re-enters this SAME
    lowering for the remaining arms), not straight to the wildcard -- real ordered-arm
    fall-through semantics, not just if/else with new syntax."""
    if not arms:
        return []
    arm, rest = arms[0], arms[1:]
    if arm.pattern == "_":
        return arm.body
    remaining = _lower_literal_arms(scrutinee, rest)
    matched_body: list[Statement] = (
        [Branch(condition=arm.guard, then=arm.body, otherwise=remaining)]
        if arm.guard is not None
        else arm.body
    )
    return [
        Branch(condition=f"{scrutinee} == {arm.pattern}", then=matched_body, otherwise=remaining)
    ]


def _compile_match(
    match_op: Match,
    *,
    irop: NadiOp,
    isa: Isa,
    abi: Abi = "win64",
    backend,
    capability_addresses: dict[str, int],
    array_addresses: dict[str, int],
    array_lengths: dict[str, int],
    record_addresses: dict[str, int],
    string_addresses: dict[str, int],
    list_addresses: dict[str, int],
    map_addresses: dict[str, int],
    map_field_positions: dict[str, dict[str, int]],
    record_field_offsets: dict[str, dict[str, int]],
    recall_addresses: dict[str, int],
    fallible_binds: set[str],
    verified_field_binds: set[str],
    cache_addresses: dict[str, int],
    cached_keys: dict[int, str],
    python_api_addr: int | None,
    symbols: dict[str, int],
    ret_len: int,
) -> bytes:
    """G9 -- `match name { ... }`'s own compile-time lowering into a chain of nested `Branch`
    nodes (`_lower_result_arms`/`_lower_literal_arms`), then handed straight to `_compile_ops`
    -- `Match` never reaches a new stencil at all, only composition of what `Branch`/`Remember`
    already have. Whether this is Result matching or literal matching is resolved here by
    checking `fallible_binds` membership (the SAME set `is_ok`/`payload` already validate
    against), not a separate flag on the AST node."""
    # MIGRATED (row 3c-ii, slice 9). The scrutinee is the match's OPERAND -- which is what it
    # actually is: a value defined earlier that the match consumes. Each arm's `pattern` comes
    # from `attrs["arms"]`, the metadata carried parallel to `regions` since the s59 fix that
    # stopped arms losing pattern/bind/guard entirely -- so this validation now rests on that
    # fix rather than merely coexisting with it. Arm BODIES stay on the AST: they feed the
    # `_compile_ops` recursion, which compiles a statement list and lowers its own input.
    scrutinee = str(irop.operands[0])
    arm_meta: list[dict[str, object]] = list(irop.attrs.get("arms", []))
    if scrutinee not in symbols:
        raise UnsupportedNode(
            f"match {scrutinee!r} references a name that isn't bound earlier in this "
            "same goal/fn"
        )
    is_result_match = scrutinee in fallible_binds
    has_ok_err_pattern = any(a["pattern"] in ("ok", "err") for a in arm_meta)
    if is_result_match != has_ok_err_pattern:
        got = (
            "a Result scrutinee with non-ok/err arms"
            if is_result_match
            else "ok/err arms on a non-Result scrutinee"
        )
        raise UnsupportedNode(
            f"match {scrutinee!r}: `ok(..)`/`err(..)` patterns require a "
            f"fallible/verified Bind scrutinee, and vice versa -- got {got}"
        )
    if is_result_match:
        lowered = _lower_result_arms(match_op, verified_field_binds)
    else:
        if not arm_meta or arm_meta[-1]["pattern"] != "_":
            raise UnsupportedNode(
                f"match {scrutinee!r} (literal matching) requires a trailing `_` "
                "wildcard arm for totality (G9 v0 boundary -- no closed sum-type domain exists "
                "to check real exhaustiveness against)"
            )
        if any(a["pattern"] == "_" for a in arm_meta[:-1]):
            raise UnsupportedNode(
                f"match {scrutinee!r}'s wildcard `_` must be the LAST arm"
            )
        lowered = _lower_literal_arms(scrutinee, match_op.arms)
    return _compile_ops(
        lowered,
        isa=isa,
        abi=abi,
        backend=backend,
        capability_addresses=capability_addresses,
        array_addresses=array_addresses,
        array_lengths=array_lengths,
        record_addresses=record_addresses,
        string_addresses=string_addresses,
        list_addresses=list_addresses,
        map_addresses=map_addresses,
        map_field_positions=map_field_positions,
        record_field_offsets=record_field_offsets,
        recall_addresses=recall_addresses,
        fallible_binds=fallible_binds,
        verified_field_binds=verified_field_binds,
        cache_addresses=cache_addresses,
        cached_keys=cached_keys,
        python_api_addr=python_api_addr,
        symbols=symbols,
        ret_len=ret_len,
        tail_keep_ret=False,
    )


_PARALLEL_ZERO = "__parallel_zero__"
_PARALLEL_INFINITE = "__parallel_infinite__"
_PARALLEL_SCRATCH_A = "__parallel_scratch_a__"
_PARALLEL_SCRATCH_B = "__parallel_scratch_b__"


def _compile_parallel(
    op: Parallel,
    *,
    isa: Isa,
    abi: Abi,
    capability_addresses: dict[str, int],
    symbols: dict[str, int],
    ret_len: int,
) -> bytes:
    """G10 -- `parallel { stmt1 stmt2 ... }`'s own compile-time lowering: unlike `Match` (which
    lowers to existing `Branch`/`Remember` nodes and re-enters `_compile_ops`), a `parallel`
    block's own N branches were ALREADY compiled and placed at real addresses by the closed
    tree (`extract_parallel_branches` + `madras.dsl.kollan`, BEFORE this function ever runs,
    D58) -- this only emits the SPAWN-then-JOIN call sequence, reusing `emit_call_with_args`
    wholesale (zero new stencils): real OS-thread fork-join (Windows `CreateThread`/
    `WaitForSingleObject`, Linux `pthread_create`/`pthread_join`, both resolved-by-address the
    SAME way any other OS/libc capability already is, under the reserved names
    `__thread_spawn__`/`__thread_join__`)."""
    if isa != "x86_64":
        raise UnsupportedNode(f"parallel only supports isa='x86_64' so far, got {isa!r}")
    if not op.body:
        return b""
    for reserved in ("__thread_spawn__", "__thread_join__"):
        if reserved not in capability_addresses:
            raise UnsupportedNode(
                f"parallel needs a resolved {reserved!r} address -- madras.dsl.kollan must "
                "resolve the real OS thread-create/join primitive before calling "
                "compile_goal/compile_fndef"
            )
    spawn_addr = capability_addresses["__thread_spawn__"]
    join_addr = capability_addresses["__thread_join__"]
    zero_slot = symbols[_PARALLEL_ZERO]
    infinite_slot = symbols[_PARALLEL_INFINITE]
    scratch_a = symbols[_PARALLEL_SCRATCH_A]
    scratch_b = symbols[_PARALLEL_SCRATCH_B]

    fragments: list[bytes] = [
        emit_load_immediate64(isa, 0),
        emit_store_local64(isa, zero_slot),
        emit_load_immediate64(isa, 0xFFFFFFFF),
        emit_store_local64(isa, infinite_slot),
    ]

    handle_slots: list[int] = []
    for i in range(len(op.body)):
        branch_name = f"__parallel_{id(op)}_branch_{i}__"
        if branch_name not in capability_addresses:
            raise UnsupportedNode(
                f"parallel branch {i} has no resolved address ({branch_name!r}) -- "
                "madras.dsl.kollan must place every branch (extract_parallel_branches) "
                "before calling compile_goal/compile_fndef"
            )
        handle_name = f"__parallel_{id(op)}_handle_{i}__"
        if handle_name not in symbols:
            raise UnsupportedNode(
                f"parallel branch {i} has no reserved handle slot ({handle_name!r}) -- "
                "compile_goal/compile_fndef must reserve it before calling _compile_ops "
                "(this is an internal-contract error, not a user one)"
            )
        handle_slot = symbols[handle_name]
        handle_slots.append(handle_slot)
        branch_addr = capability_addresses[branch_name]

        fragments.append(emit_load_immediate64(isa, branch_addr))
        fragments.append(emit_store_local64(isa, scratch_a))
        if abi == "win64":
            # CreateThread(lpThreadAttributes=0, dwStackSize=0, lpStartAddress=branch_addr,
            # lpParameter=0, dwCreationFlags=0, lpThreadId=0) -- 6 args, the first 4 stack-
            # spilling beyond RCX/RDX/R8/R9 (emit_call_with_args's own existing capability, G8).
            arg_slots = [zero_slot, zero_slot, scratch_a, zero_slot, zero_slot, zero_slot]
        else:
            # pthread_create(&tid, NULL, branch_addr, NULL) -- &tid (a real OUT-param address,
            # emit_lea_local, G8's own Vectorcall precedent) staged in scratch_b; pthread_create
            # itself WRITES the real pthread_t value into the handle slot it points at.
            fragments.append(emit_lea_local(isa, handle_slot))
            fragments.append(emit_store_local64(isa, scratch_b))
            arg_slots = [scratch_b, zero_slot, scratch_a, zero_slot]
        fragments.append(emit_call_with_args(isa, spawn_addr, arg_slots, abi)[:-ret_len])
        if abi == "win64":
            fragments.append(emit_store_local64(isa, handle_slot))
        # SysV: pthread_create already wrote the real pthread_t into handle_slot via &tid --
        # no store needed (unlike win64's CreateThread, which RETURNS the handle in RAX).

    for handle_slot in handle_slots:
        join_args = [handle_slot, infinite_slot if abi == "win64" else zero_slot]
        fragments.append(emit_call_with_args(isa, join_addr, join_args, abi)[:-ret_len])

    return b"".join(fragments)


def _compile_ops(
    stmts: list,
    *,
    isa: Isa,
    abi: Abi = "win64",
    backend,
    capability_addresses: dict[str, int],
    array_addresses: dict[str, int],
    array_lengths: dict[str, int],
    record_addresses: dict[str, int],
    string_addresses: dict[str, int],
    list_addresses: dict[str, int],
    map_addresses: dict[str, int],
    map_field_positions: dict[str, dict[str, int]],
    record_field_offsets: dict[str, dict[str, int]],
    recall_addresses: dict[str, int],
    fallible_binds: set[str],
    verified_field_binds: set[str],
    cache_addresses: dict[str, int],
    cached_keys: dict[int, str],
    python_api_addr: int | None,
    symbols: dict[str, int],
    ret_len: int,
    tail_keep_ret: bool,
    induction_ranges: dict[str, tuple[int, int]] | None = None,
) -> bytes:
    """Compile an already-validated statement list into bytes. `tail_keep_ret`: only the
    top-level goal body (with no locals at all) keeps its very last `Call`'s own `ret` -- the
    original T8.5 chain-of-calls shape; every other case (any goal with locals, and every
    Branch then/else body regardless) strips it, because something else always supplies the
    real return (a frame epilogue, or the code right after the branch).

    `induction_ranges` (N1a) maps an enclosing loop's induction variable to its PROVEN
    `[start, stop)` range -- `_compile_loop` already forces `range(...)` bounds to be integer
    literals, so a loop variable's exact range is a compile-time fact even though its VALUE
    isn't. That's what lets `arr[i]` compile to a plain runtime-indexed load with NO runtime
    bounds check: the compiler proves the access instead of paying for it at runtime. v0
    boundary (disclosed, narrower than full generality): only threaded through `Loop` bodies
    directly nested in THIS statement list, not through `Branch`/`Match` arms -- a loop var
    referenced from inside a nested Branch/Match isn't provably-indexable yet, real separate
    future work, not needed for this row's actual use (a straight loop, no nesting)."""
    ranges = induction_ranges or {}

    def _capability_addr(name: str) -> int:
        if name not in capability_addresses:
            raise UnsupportedNode(f"no resolved address for capability {name!r}")
        return capability_addresses[name]

    def _describe_arg(arg: object) -> str:
        """Render an argument the way the AUTHOR wrote it, not the way the IR names it.

        Arguments now come from the IR, where a value produced by an operation is referred to by
        its SSA name -- so `call f(recall(k))` reached this error as `'%recall.0'`, which tells a
        user nothing and tells them it in jargon they have never seen. The definition map knows
        what produced it, so the message can say `recall('k')` instead. A compiler that reports
        its own internal bookkeeping is a compiler that feels hostile to the person writing the
        program, which is the opposite of what this language is for."""
        producer = ir_defs.get(arg) if isinstance(arg, str) else None
        if producer is None:
            return repr(arg)
        if producer.kind == "memory-ref" and producer.op == "read":
            return f"recall({producer.attrs.get('key')!r})"
        return f"the result of `{producer.op}`"

    def _call_body(name: str, args: list, *, keep_ret: bool) -> bytes:
        target = _capability_addr(name)
        if not args:
            code = emit_capability_call(isa, target, abi)
            return code if keep_ret else code[:-ret_len]
        # G8 -- args-capable calls: reuses `emit_call_with_args` (fixed ABI register order +
        # stack-spill, LuaJIT-FFI/copy-and-patch "no register allocator" pattern, GPL-LLM-OSS
        # Radar s56). v0 boundary: every argument must be a NAME bound earlier in this same
        # goal/fn -- a bare literal argument needs a `remember`/`bind` first (the same "literal
        # vs. bound name" honesty G5's list index/G6's Push value already draw, just the mirror
        # direction: here the NAME is required, not the literal).
        if isa != "x86_64":
            raise UnsupportedNode(
                f"Call({name!r}) with args only supports isa='x86_64' so far, got {isa!r}"
            )
        arg_slots: list[int] = []
        for arg in args:
            if not isinstance(arg, str) or arg not in symbols:
                raise UnsupportedNode(
                    f"Call({name!r})'s argument {_describe_arg(arg)} must be a name bound "
                    "earlier in this "
                    "same goal/fn (G8 v0 boundary) -- a literal argument needs a "
                    "`remember`/`bind` first"
                )
            arg_slots.append(symbols[arg])
        code = emit_call_with_args(isa, target, arg_slots, abi)
        return code if keep_ret else code[:-ret_len]

    ops = _validate_and_filter(stmts)
    # ROW 3c-ii, slice 1 (s59): each statement paired 1:1 with its lowered IR op. A statement
    # KIND that has migrated reads from `ir[i]`; the rest still read the AST node. That is what
    # makes a ~1,234-line codegen migration checkable -- every slice is verified byte-identical
    # against `tests/test_dsl/test_kollan_golden_bytes.py` before the next one starts, rather
    # than one sweep whose only proof is that it still compiles.
    ir, ir_defs = lower_each_with_defs(ops)
    fragments: list[bytes] = []
    for i, op in enumerate(ops):
        irop = ir[i]
        # MIGRATED: dispatch on the kernel kind, not the Python class. `capability-call`/`call`
        # is the frozen primitive; `Call` is merely how today's surface spells it.
        if irop.kind == "capability-call" and irop.op == "call":
            keep_ret = (i == len(ops) - 1) and tail_keep_ret
            call_name = str(irop.attrs["name"])
            call_args = list(irop.attrs.get("args", []))
            if irop.attrs.get("capability_kind") == "ffi_bridge":
                if python_api_addr is None:
                    raise UnsupportedNode(
                        f"Call({call_name!r}) is ffi_bridge, but compile_goal was never given a "
                        "resolved python_api_addr"
                    )
                # G8 -- T9a's own one-bare-call-per-goal restriction, lifted: `emit_python_call`
                # is embedded as a mid-function FRAGMENT (its trailing `ret` stripped, the SAME
                # "reused as a fragment" shape every other capability call already has) whenever
                # this ISN'T the exact original T8.11/T9.2 shape (a single bare ffi_bridge call,
                # the goal's entire body, no locals at all) -- `keep_ret` already computes to
                # False for every case EXCEPT that one, because `compile_goal` forces a real
                # frame (`__ffi_frame__`) the moment an ffi_bridge call appears alongside
                # anything else, making `tail_keep_ret` False downstream. This preserves the
                # EXISTING byte-identical-to-`emit_python_call`-alone contract for the original
                # shape (tested directly, never touched) while allowing any other shape through.
                py_call = emit_python_call(isa, _capability_addr(call_name), python_api_addr, abi)
                fragments.append(py_call if keep_ret else py_call[:-ret_len])
            else:
                # Arguments come from the IR's `operands`, which is exactly the right shape: G8's
                # v0 boundary already requires every argument to be a NAME bound earlier in this
                # same goal/fn, and a name is precisely what an SSA operand is. Order is preserved
                # by the lowering, and order is the ABI register assignment.
                fragments.append(_call_body(call_name, call_args, keep_ret=keep_ret))
        elif irop.kind == "compose-bind":
            # MIGRATED (row 3c-ii, slice 2). `compose-bind` is the frozen kernel primitive;
            # `Bind` is merely how today's surface spells it. The PRODUCING operation -- a
            # capability call, or the one legal `verified-field` projection -- is the op in the
            # bind's region, which is also what SSA already says: a bind consumes exactly one
            # defined value, so the definition is reachable without an isinstance test.
            bind_target = str(irop.attrs["target"])
            bind_cached = bool(irop.attrs.get("cached"))
            inner = irop.regions[0][0]
            inner_selector = inner.attrs.get("selector")
            inner_name = str(inner.attrs.get("name", ""))
            inner_args = list(inner.attrs.get("args", []))
            if inner_selector == "verified-field":
                if isa != "x86_64":
                    raise UnsupportedNode(
                        f"Bind({bind_target!r}) is a verified field access, but the verified-field "
                        f"codegen only supports isa='x86_64' so far, got {isa!r}"
                    )
                record_name = str(inner.attrs["source"])
                field_name = str(inner.attrs["selector_key"])
                if record_name not in record_field_offsets:
                    raise UnsupportedNode(
                        f"Bind({bind_target!r}) verifies {record_name!r}.{field_name}, but "
                        f"{record_name!r} isn't a record bound earlier in this same goal"
                    )
                if record_name not in record_addresses:
                    raise UnsupportedNode(
                        f"record {record_name!r} was declared but never materialized before "
                        "compile_goal ran"
                    )
                field_offsets = record_field_offsets[record_name]
                if field_name not in field_offsets:
                    raise UnsupportedNode(
                        f"Bind({bind_target!r}) verifies {record_name!r}.{field_name}, which "
                        f"isn't a field {record_name!r} declares -- known fields: "
                        f"{sorted(field_offsets)}"
                    )
                if "__verify_scratch__" not in symbols:
                    raise UnsupportedNode(
                        "a verified field access needs an internal scratch slot that wasn't "
                        "reserved -- compile_goal/compile_fndef must reserve it before calling "
                        "_compile_ops (this is an internal-contract error, not a user one)"
                    )
                n_fields = len(field_offsets)
                base = record_addresses[record_name]
                scratch = symbols["__verify_scratch__"]

                # 1. XOR all N fields together into `scratch` (the running checksum accumulator).
                fragments.append(emit_load_absolute(isa, base))  # eax = field 0
                fragments.append(backend.emit_store_local(scratch))  # acc = field 0
                for i in range(1, n_fields):
                    fragments.append(emit_load_absolute(isa, base + i * 4))  # eax = field i
                    fragments.append(emit_xor_local(isa, scratch))  # eax ^= acc
                    fragments.append(backend.emit_store_local(scratch))  # acc = eax
                # 2. XOR the STORED checksum in too -- result is 0 iff every field still matches
                #    what was checksummed at materialization time (double-entry's own
                #    cross-check principle, Pacioli 1494 -- a construction-time integrity
                #    signal, not a security boundary).
                fragments.append(emit_load_absolute(isa, base + n_fields * 4))  # eax = checksum
                fragments.append(emit_xor_local(isa, scratch))  # eax = checksum ^ acc
                fragments.append(emit_eax_is_zero(isa))  # eax = 1 if match else 0
                # 3. Pack (match << 32) | field_value into RAX -- the SAME T8.15/T8.16 `fallible`
                #    convention, read back via the EXISTING is_ok/payload, no new reader needed.
                fragments.append(emit_shl_rax_32(isa))  # rax = match << 32
                fragments.append(emit_store_local64(isa, scratch))  # acc(64) = match << 32
                target_addr = base + field_offsets[field_name] * 4
                fragments.append(emit_load_absolute(isa, target_addr))  # eax = target field
                fragments.append(emit_or_local64(isa, scratch))  # rax |= acc(64)
                fragments.append(emit_store_local64(isa, symbols[bind_target]))
            elif inner.kind != "capability-call":
                # `Bind.call` is `Call | Project`, and the ONLY legal Project there is the
                # `verified-field` selector handled by the branch above (it alone returns a
                # packed `(match, value)` needing a Bind target). Any other projection belongs
                # in a `Remember` value position -- rejected here explicitly rather than
                # crashing on a missing `.name`/`.args`, the same isinstance-first class of bug
                # G3 already found once.
                raise UnsupportedNode(
                    f"Bind({bind_target!r})'s call is a {inner_selector!r} projection -- only a "
                    "`verified-field` projection may be bound directly; read any other "
                    "projection through a `remember` instead"
                )
            elif bind_cached:
                # MIGRATED (addressing row): the cache key is the IR address, looked up by the
                # statement's identity. It CANNOT come from `irop.mugavari_id` directly, because
                # `_compile_ops` obtains its IR from `lower_each`, which lowers each statement
                # list independently and so has no whole-module context -- and per-statement-list
                # addressing would be WRONG, not merely absent: every recursive call restarts
                # depth and order at zero, so a top-level cached bind and one inside a branch
                # would collide. That is exactly the stale-result bug
                # `test_kollan_cache_loop_risk.py` pins. `compile_goal` therefore addresses the
                # module ONCE and hands the mapping down.
                mugavari_id = cached_keys.get(id(op))
                if mugavari_id is None:
                    raise UnsupportedNode(
                        f"Bind({bind_target!r}) is cached but has no resolved cache key -- "
                        "compile_goal/compile_fndef must build one via `cached_key_map()` "
                        "before calling _compile_ops"
                    )
                if mugavari_id not in cache_addresses:
                    raise UnsupportedNode(
                        f"Bind({bind_target!r}) is cached, but madras.dsl never resolved a real "
                        "cache slot for it (cache_addresses is missing this IR address) before "
                        "calling compile_goal -- nadi_cached_binds() + "
                        "kollan_cache.ResultCache.resolve() must run first"
                    )
                cache_addr = cache_addresses[mugavari_id]
                slot = symbols[bind_target]

                if inner.kind == "capability-call" and inner_args:
                    # N2 -- a cached call WITH arguments: T8.17's original call-SITE-addressed
                    # path above is confirmed live-wrong for this shape (a cached call inside a
                    # `Loop`, argument varying per iteration, silently returns stale data --
                    # `test_kollan_cache_loop_risk.py`). Content-addressed instead: the real
                    # cache key is `arg`'s VALUE, not just the call site.
                    if isa != "x86_64":
                        raise UnsupportedNode(
                            f"Bind({bind_target!r})'s cached call with arguments only supports "
                            f"isa='x86_64' so far, got {isa!r}"
                        )
                    cached_args = list(inner_args)
                    if len(cached_args) > 1:
                        raise UnsupportedNode(
                            f"Bind({bind_target!r})'s cached call has {len(cached_args)} "
                            "arguments -- content-addressed caching only supports exactly ONE "
                            "argument so far (v0 boundary, disclosed: the probe's key is a "
                            "single hashed value, not a tuple)"
                        )
                    (arg_name,) = cached_args
                    if not isinstance(arg_name, str) or arg_name not in symbols:
                        raise UnsupportedNode(
                            f"Bind({bind_target!r})'s cached call argument {arg_name!r} must be "
                            "a name bound earlier in this same goal/fn"
                        )
                    for scratch in _CACHE_PROBE_SCRATCH:
                        if scratch not in symbols:
                            raise UnsupportedNode(
                                f"Bind({bind_target!r}) is a cached call with arguments, but "
                                f"compile_goal/compile_fndef never reserved its scratch slot "
                                f"{scratch!r} -- this is an internal-contract error, not a "
                                "user one (the reservation must run on any cached-with-args USE)"
                            )
                    fragments.append(
                        _emit_cached_call_with_arg(
                            isa=isa,
                            abi=abi,
                            backend=backend,
                            op=op,
                            arg_slot=symbols[arg_name],
                            target_slot=slot,
                            table_addr=cache_addr,
                            symbols=symbols,
                            call_body=_call_body(inner_name, inner_args, keep_ret=False),
                        )
                    )
                    continue

                hit_body = emit_load_absolute64(isa, cache_addr) + backend.emit_store_local(slot)
                miss_body = (
                    _call_body(inner_name, inner_args, keep_ret=False)
                    + backend.emit_store_local(slot)
                    + emit_set_bit32(isa)
                    + emit_store_absolute64(isa, cache_addr)
                )
                jump_over_miss = backend.emit_jump(len(miss_body))
                skip_to_miss = len(hit_body) + len(jump_over_miss)
                test_and_branch = (
                    emit_load_absolute64(isa, cache_addr)
                    + emit_shift_right_32(isa)
                    + backend.emit_compare_and_jump_if_false("==", 1, skip_to_miss)
                )
                fragments.append(test_and_branch + hit_body + jump_over_miss + miss_body)
            elif inner.attrs.get("capability_kind") == "fallible":
                fragments.append(_call_body(inner_name, inner_args, keep_ret=False))
                fragments.append(emit_store_local64(isa, symbols[bind_target]))
            elif inner.attrs.get("capability_kind") == "ffi_bridge":
                # T9b -- lifts the last of T9a/G8's own restrictions: capturing an ffi_bridge
                # call's result into a named Bind, not just letting it run as a bare Call. The
                # EXECUTION side needed NO new work: `run_compiled_capability_call`'s own
                # `frame_bytes` param (G8) already picks `run_framed_call_with_unwind` -- the
                # correctly-registered-unwind-info runner for a real `push rbp` frame -- for any
                # goal whose reported frame size is nonzero, and a Bind ALWAYS reserves at least
                # one symbol slot, so this case was already routed correctly once codegen
                # stopped refusing it. Same "strip the ret, RAX still holds the raw pointer,
                # explicit 64-bit store next" shape every other Bind branch already uses (the
                # fallible branch immediately above stores its own 64-bit packed value the exact
                # same way) -- zero new store/runner machinery, only the refusal removed.
                if python_api_addr is None:
                    raise UnsupportedNode(
                        f"Bind({bind_target!r})'s call is ffi_bridge, but compile_goal was never "
                        "given a resolved python_api_addr"
                    )
                target_addr = _capability_addr(inner_name)
                py_call = emit_python_call(isa, target_addr, python_api_addr, abi)
                fragments.append(py_call[:-ret_len])
                fragments.append(emit_store_local64(isa, symbols[bind_target]))
            else:
                fragments.append(_call_body(inner_name, inner_args, keep_ret=False))
                fragments.append(backend.emit_store_local(symbols[bind_target]))
        elif irop.kind == "memory-ref" and irop.op == "write":
            # PARTIALLY MIGRATED (row 3c-ii, slice 3). The DESTINATION comes from the IR --
            # `attrs["key"]` -- which is also what exercises the s59 fix where a projection's
            # selector name was overwriting it (`remember dest = arr[idx]` lowered to a write of
            # `idx`). The VALUE details still read the AST, and the blocker is named rather than
            # worked around: a value that is itself an operation (a `Recall` read) lowers to a
            # PRELUDE op plus this write, and `lower_each` returns only the last op per statement,
            # so the read -- and its key -- is not reachable from here. Migrating the value side
            # needs the prelude threaded through too, which is its own slice.
            dest_key = str(irop.attrs["key"])
            # The value's shape, read from the IR. `value_form`/`type`/`selector` are exactly the
            # two-level naming discipline applied to the value space (s59): the FORM names what
            # kind of value it is, the TYPE/SELECTOR carries the variation. Dispatching on them
            # is the same decision the isinstance ladder made, minus the Python class.
            val_form = irop.attrs.get("value_form")
            val_type = irop.attrs.get("type")
            val_selector = irop.attrs.get("selector")
            val_source = irop.attrs.get("source")
            val_sel_key = irop.attrs.get("selector_key")
            if val_selector == "result-tag":
                bind_name = val_source
                if bind_name not in fallible_binds:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) reads is_ok({bind_name!r}), but {bind_name!r} "
                        "isn't bound by a fallible call in this same goal -- its high 32 bits "
                        "would be undefined stack garbage, not a real tag"
                    )
                fragments.append(emit_load_local64(isa, symbols[bind_name]))
                fragments.append(emit_shift_right_32(isa))
                fragments.append(backend.emit_store_local(symbols[dest_key]))
                continue
            if val_selector == "result-payload":
                bind_name = val_source
                if bind_name not in fallible_binds:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) reads payload({bind_name!r}), but {bind_name!r} "
                        "isn't bound by a fallible call in this same goal"
                    )
                fragments.append(emit_load_local64(isa, symbols[bind_name]))
                fragments.append(backend.emit_store_local(symbols[dest_key]))
                continue
            if val_form == "intrinsic":
                # N4 -- `cycles()`: a genuinely NATIVE clock read (`rdtsc`), no
                # capability_addresses resolution needed at all (unlike every other value kind
                # here, this reads real hardware state directly, not memory `.tamil` itself
                # materialized).
                if isa != "x86_64":
                    raise UnsupportedNode(
                        f"Remember({dest_key!r})'s cycles() read only supports isa='x86_64' so "
                        f"far, got {isa!r}"
                    )
                fragments.append(emit_read_cycle_counter(isa))
                fragments.append(backend.emit_store_local(symbols[dest_key]))
                continue
            # MIGRATED: a `recall` value is its own `memory-ref`/`read` op in the IR (row 1
            # emitted it as a real operation rather than folding it into an attribute, because a
            # read has real cost and a real address). The write consumes it by operand, so the
            # key comes from the DEFINITION of that operand -- ordinary SSA def-use, no
            # reconstruction from names.
            recall_def = ir_defs.get(irop.operands[0]) if irop.operands else None
            if recall_def is not None and recall_def.op == "read":
                key = str(recall_def.attrs["key"])
                if key not in recall_addresses:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) recalls {key!r}, but madras.dsl never resolved a "
                        "real value for it (recall_addresses is missing this key) before "
                        "calling compile_goal -- collect_recalls() + "
                        "kollan_recall.resolve_recalls() must run first"
                    )
                fragments.append(emit_load_absolute(isa, recall_addresses[key]))
                fragments.append(backend.emit_store_local(symbols[dest_key]))
                continue
            if val_type == "range":
                raise UnsupportedNode(
                    f"Remember({dest_key!r}) can't hold a range(...) -- a range isn't a scalar "
                    "value; it's only meaningful as a Loop's iterable"
                )
            if val_type == "array":
                if dest_key not in array_addresses:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) declares an array, but madras.dsl never "
                        "materialized it (array_addresses is missing this key) before calling "
                        "compile_goal -- nadi_arrays() + a real allocator must run first"
                    )
                continue  # a pure compile-time declaration -- no runtime slot, no emitted code,
                # same zero-runtime-cost shape RangeLiteral's own bounds already have.
            if val_selector == "index":
                array_name = val_source
                assert val_sel_key is not None  # validator: `index` always carries a key
                if array_name not in array_lengths and array_name in list_addresses:
                    # G5 -- `name[index]` REUSED for a list read: which structure `name` is is
                    # a semantic fact resolved here (list_addresses membership), not a syntax
                    # split. A list is a chunked chain (research candidate #1), so an indexed
                    # read WALKS `index` hops from the head -- runtime cost the array path's
                    # O(1) absolute-address read doesn't pay, an honest, disclosed tradeoff of
                    # the chunked layout, not a hidden regression.
                    if isa != "x86_64":
                        raise UnsupportedNode(
                            f"Remember({dest_key!r})'s list-index codegen only supports "
                            f"isa='x86_64' so far, got {isa!r}"
                        )
                    try:
                        index = int(val_sel_key)
                    except ValueError as exc:
                        raise UnsupportedNode(
                            f"Remember({dest_key!r})'s list index must be an integer literal, "
                            f"got {val_sel_key!r}"
                        ) from exc
                    if index < 0:
                        raise UnsupportedNode(
                            f"Remember({dest_key!r}) indexes {array_name}[{index}], a negative "
                            "list index isn't supported"
                        )
                    fragments.append(emit_load_local64(isa, symbols[array_name]))
                    for _ in range(index):
                        fragments.append(emit_load_indirect_offset(isa, 8))
                    fragments.append(emit_load_indirect_offset(isa, 0))
                    fragments.append(emit_store_local64(isa, symbols[dest_key]))
                    continue
                if array_name not in array_lengths:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) indexes {array_name!r}, which isn't an array "
                        "bound earlier in this same goal"
                    )
                if array_name not in array_addresses:
                    raise UnsupportedNode(
                        f"array {array_name!r} was declared but never materialized before "
                        "compile_goal ran"
                    )
                length = array_lengths[array_name]
                if val_sel_key in ranges:
                    # N1a -- a LOOP INDUCTION index. Its value is a runtime fact, but its RANGE
                    # is a compile-time one (`_compile_loop` forces integer-literal bounds), so
                    # the access is PROVEN in-bounds here and the emitted code carries no
                    # runtime bounds check at all. Bounds violations stay COMPILE-time errors,
                    # exactly as they already are for a literal index -- "governed by
                    # construction" with the guarantee in the compiler, not a per-access tax.
                    if isa != "x86_64":
                        raise UnsupportedNode(
                            f"Remember({dest_key!r})'s runtime-indexed array read only supports "
                            f"isa='x86_64' so far, got {isa!r}"
                        )
                    lo, hi = ranges[val_sel_key]
                    if lo < 0 or hi > length:
                        raise UnsupportedNode(
                            f"Remember({dest_key!r}) indexes {array_name}[{val_sel_key}] over "
                            f"range [{lo}, {hi}), which isn't provably within a {length}-"
                            f"element array -- narrow the loop's range(...) bounds"
                        )
                    fragments.append(emit_load_immediate64(isa, array_addresses[array_name]))
                    fragments.append(emit_load_local_into_ecx(isa, symbols[val_sel_key]))
                    fragments.append(emit_load_indexed_scaled(isa))
                    fragments.append(backend.emit_store_local(symbols[dest_key]))
                    continue
                try:
                    index = int(val_sel_key)
                except ValueError as exc:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r})'s array index must be an integer literal or a "
                        f"loop induction variable, got {val_sel_key!r} -- an arbitrary "
                        "computed index has no provable range, so it can't be bounds-checked "
                        "at compile time (rejected loudly rather than read unchecked)"
                    ) from exc
                if not (0 <= index < length):
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) indexes {array_name}[{index}], out of bounds "
                        f"for a {length}-element array"
                    )
                elem_addr = array_addresses[array_name] + index * 4
                fragments.append(emit_load_absolute(isa, elem_addr))
                fragments.append(backend.emit_store_local(symbols[dest_key]))
                continue
            if val_type == "record":
                if dest_key not in record_addresses:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) declares a record, but madras.dsl never "
                        "materialized it (record_addresses is missing this key) before calling "
                        "compile_goal -- nadi_records() + a real allocator must run first"
                    )
                continue  # a pure compile-time declaration -- no runtime slot, no emitted code,
                # same zero-runtime-cost shape ArrayLiteral's own declaration already has.
            if val_selector == "field":
                record_name = val_source
                assert val_sel_key is not None  # validator: `field` always carries a key
                if record_name not in record_field_offsets and record_name in map_field_positions:
                    # G5 -- `name.field` REUSED for a map read: same semantic-fact-not-syntax-
                    # split shape `ArrayIndex`-on-a-list just established. v0 boundary: only a
                    # key inserted by THIS map's own `MapLiteral` is resolvable (its hop-count
                    # comes from the literal's own field order) -- a key inserted later by
                    # `mapset` isn't readable back yet, an honest, disclosed gap (matches G1's
                    # 0-arg-only fn scope), not a hidden one.
                    if isa != "x86_64":
                        raise UnsupportedNode(
                            f"Remember({dest_key!r})'s map-get codegen only supports isa='x86_64' "
                            f"so far, got {isa!r}"
                        )
                    field_positions = map_field_positions[record_name]
                    if val_sel_key not in field_positions:
                        raise UnsupportedNode(
                            f"Remember({dest_key!r}) reads {record_name!r}.{val_sel_key}, "
                            f"which isn't a key {record_name!r}'s literal declares -- known "
                            f"keys: {sorted(field_positions)}"
                        )
                    hops = field_positions[val_sel_key]
                    fragments.append(emit_load_local64(isa, symbols[record_name]))
                    for _ in range(hops):
                        fragments.append(emit_load_indirect_offset(isa, 16))
                    fragments.append(emit_load_indirect_offset(isa, 8))
                    fragments.append(emit_store_local64(isa, symbols[dest_key]))
                    continue
                if record_name not in record_field_offsets:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) reads {record_name!r}.{val_sel_key}, but "
                        f"{record_name!r} isn't a record bound earlier in this same goal"
                    )
                if record_name not in record_addresses:
                    raise UnsupportedNode(
                        f"record {record_name!r} was declared but never materialized before "
                        "compile_goal ran"
                    )
                field_offsets = record_field_offsets[record_name]
                if val_sel_key not in field_offsets:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) reads {record_name!r}.{val_sel_key}, which "
                        f"isn't a field {record_name!r} declares -- known fields: "
                        f"{sorted(field_offsets)}"
                    )
                field_addr = record_addresses[record_name] + field_offsets[val_sel_key] * 4
                fragments.append(emit_load_absolute(isa, field_addr))
                fragments.append(backend.emit_store_local(symbols[dest_key]))
                continue
            if val_type == "string":
                if dest_key not in string_addresses:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) declares a string, but madras.dsl never "
                        "materialized it (string_addresses is missing this key) before calling "
                        "compile_goal -- nadi_strings() + a real allocator must run first"
                    )
                if isa != "x86_64":
                    raise UnsupportedNode(
                        f"Remember({dest_key!r})'s string codegen only supports isa='x86_64' so "
                        f"far, got {isa!r}"
                    )
                # A string is a (pointer, length) slice (Zig/Rust precedent) -- the length is
                # compile-time-known (never stored at runtime, same treatment array_lengths
                # already gets); only the real base ADDRESS needs holding, which fits the SAME
                # 8-byte-aligned slot + emit_load_immediate64/emit_store_local64 (T8.15/T8.16)
                # every other 64-bit value already uses. Zero new stencils needed for v0.
                fragments.append(emit_load_immediate64(isa, string_addresses[dest_key]))
                fragments.append(emit_store_local64(isa, symbols[dest_key]))
                continue
            if val_type in ("list", "map"):
                # G5 -- unlike ArrayLiteral/RecordLiteral's pure compile-time declaration (no
                # runtime slot at all), a list/map's bound name holds a REAL, MUTABLE local
                # slot (the current head chunk's address) -- `push`/`mapset` need somewhere to
                # read/write it. Same 2-fragment shape StringLiteral already established
                # (load the closed-tree-materialized address, store it locally); zero new
                # stencils for the declare step itself -- only `push`/`mapset` are new codegen.
                addresses = list_addresses if val_type == "list" else map_addresses
                kind_word = "list" if val_type == "list" else "map"
                if dest_key not in addresses:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) declares a {kind_word}, but madras.dsl never "
                        f"materialized it ({kind_word}_addresses is missing this key) before "
                        "calling compile_goal -- nadi_lists()/nadi_maps() + a real "
                        "allocator must run first"
                    )
                if isa != "x86_64":
                    raise UnsupportedNode(
                        f"Remember({dest_key!r})'s {kind_word} codegen only supports isa='x86_64' "
                        f"so far, got {isa!r}"
                    )
                fragments.append(emit_load_immediate64(isa, addresses[dest_key]))
                fragments.append(emit_store_local64(isa, symbols[dest_key]))
                continue
            if val_form == "compute":
                # G11 -- `derive`'s own real codegen: evaluate `left <op> right` into EAX and
                # store it. RIGHT is resolved to a SLOT FIRST (materializing a literal into
                # `__compute_scratch__` if needed) BEFORE loading LEFT into EAX -- reversing
                # this order would let a literal RIGHT's own load-immediate+store-local
                # sequence clobber EAX while it still held LEFT's value, a real ordering bug
                # caught by thinking through the instruction sequence, not live (the register-
                # memory op stencils only ever read the right operand from a slot, never an
                # immediate, exactly like `emit_xor_local`'s own established shape).
                if isa != "x86_64":
                    raise UnsupportedNode(
                        f"Remember({dest_key!r})'s computed value only supports isa='x86_64' so "
                        f"far, got {isa!r}"
                    )
                left = str(irop.attrs["left"])
                oper = str(irop.attrs["op"])
                right = str(irop.attrs["right"])
                try:
                    right_val = int(right)
                except ValueError:
                    if right not in symbols:
                        raise UnsupportedNode(
                            f"Remember({dest_key!r})'s computed value references {right!r}, "
                            "which isn't a name bound earlier in this same goal"
                        ) from None
                    right_slot = symbols[right]
                else:
                    if "__compute_scratch__" not in symbols:
                        raise UnsupportedNode(
                            "a computed value needs an internal scratch slot that wasn't "
                            "reserved -- compile_goal/compile_fndef must reserve it before "
                            "calling _compile_ops (this is an internal-contract error, not a "
                            "user one)"
                        )
                    right_slot = symbols["__compute_scratch__"]
                    fragments.append(backend.emit_load_immediate(right_val))
                    fragments.append(backend.emit_store_local(right_slot))
                try:
                    left_val = int(left)
                except ValueError:
                    if left not in symbols:
                        raise UnsupportedNode(
                            f"Remember({dest_key!r})'s computed value references {left!r}, "
                            "which isn't a name bound earlier in this same goal"
                        ) from None
                    fragments.append(backend.emit_load_local(symbols[left]))
                else:
                    fragments.append(backend.emit_load_immediate(left_val))
                op_emitter = {
                    "+": emit_add_local,
                    "-": emit_sub_local,
                    "*": emit_mul_local,
                    "/": emit_div_local,
                }[oper]
                fragments.append(op_emitter(isa, right_slot))
                fragments.append(backend.emit_store_local(symbols[dest_key]))
                continue
            if val_form == "project":
                raise UnsupportedNode(
                    f"Remember({dest_key!r}) reads a {val_selector!r} projection, which "
                    "compile_goal doesn't lower yet"
                )
            # A scalar value is either an integer literal -- which the lowering records as
            # `const` -- or a name bound earlier, which it records as an operand. The AST's
            # try/int/except shape was doing that classification at codegen time; the IR has
            # already done it, so this reads the answer instead of recomputing it.
            if "const" in irop.attrs:
                fragments.append(backend.emit_load_immediate(int(irop.attrs["const"])))
            else:
                ref = irop.operands[0] if irop.operands else ""
                if ref not in symbols:
                    raise UnsupportedNode(
                        f"Remember({dest_key!r}) references {ref!r}, which isn't a name "
                        "bound earlier in this same goal -- compile_goal only resolves local "
                        "Binds, not the real external memory graph"
                    ) from None
                fragments.append(backend.emit_load_local(symbols[ref]))
            fragments.append(backend.emit_store_local(symbols[dest_key]))
        elif irop.kind == "memory-ref" and irop.op in ("push", "map-set"):
            # MIGRATED (row 3c-ii, slice 6). Push and mapset are the SAME kernel primitive --
            # `memory-ref` -- separated by the `op` variation, which is the two-level naming
            # discipline showing through: one frozen kind, the variation in a field. The AST
            # spells them as two classes and then immediately re-joins them in a tuple isinstance;
            # the IR never split them in the first place.
            is_push = irop.op == "push"
            collection = str(irop.attrs["list"] if is_push else irop.attrs["map"])
            # G5 -- REAL runtime growth: calls the UNCHANGED `emit_alloc` stencil (T8.12) as a
            # real subroutine (registered under the reserved capability name
            # "__collection_alloc__", the SAME `capability_addresses` map every other call
            # already resolves through -- zero new plumbing for the call target itself), then
            # links the freshly allocated chunk onto the existing chain. A list chunk is
            # `[value:8][next:8]` (16 bytes); a map chunk is `[key:8][value:8][next:8]`
            # (24 bytes) -- `Push`'s sibling, one more literal write before the same link step.
            if isa != "x86_64":
                raise UnsupportedNode(
                    f"{'Push' if is_push else 'MapSet'}'s runtime-growth codegen "
                    f"only supports isa='x86_64' so far, got {isa!r}"
                )
            # One name, not a push/mapset pick: the AST needs two FIELDS (`list_name`,
            # `map_name`) for one concept, so this was a genuine branch. The IR carries one
            # collection either way, and the branch collapsed on its own.
            target_name = collection
            if target_name not in symbols:
                raise UnsupportedNode(
                    f"{'push' if is_push else 'mapset'} {target_name!r} references "
                    f"a name that isn't a list/map bound earlier in this same goal"
                )
            if "__push_scratch__" not in symbols:
                raise UnsupportedNode(
                    "push/mapset needs an internal scratch slot that wasn't reserved -- "
                    "compile_goal/compile_fndef must reserve it before calling _compile_ops "
                    "(this is an internal-contract error, not a user one)"
                )
            scratch = symbols["__push_scratch__"]
            chunk_size = 16 if is_push else 24
            try:
                if is_push:
                    literal_values = [int(irop.attrs["const"])]
                else:
                    literal_values = [int(str(irop.attrs["key"])), int(irop.attrs["const"])]
            except ValueError as exc:
                raise UnsupportedNode(
                    f"{'push' if is_push else 'mapset'} {target_name!r}'s "
                    "value/key must be an integer literal -- a computed value needs a real "
                    "register allocator, not built yet"
                ) from exc
            fragments.append(emit_load_local64(isa, symbols[target_name]))  # rax = old head
            fragments.append(emit_store_local64(isa, scratch))  # scratch = old head
            fragments.append(emit_load_immediate_arg1(isa, chunk_size, abi))  # arg1 = chunk size
            # `emit_capability_call` is itself a COMPLETE 0-arg callable stencil (its own
            # trailing `ret`) -- used here as a mid-function FRAGMENT (a real `call`, not the
            # whole compiled body), so its `ret` byte must be stripped, same as `_call_body`'s
            # own `keep_ret` shape every OTHER capability call already gets right.
            alloc_call = emit_capability_call(isa, _capability_addr("__collection_alloc__"), abi)
            fragments.append(alloc_call[:-ret_len])
            fragments.append(emit_store_local64(isa, symbols[target_name]))  # head = new chunk
            for i, value in enumerate(literal_values):
                fragments.append(emit_store_immediate_indirect(isa, value, i * 8))
            fragments.append(emit_link_local_into_indirect(isa, scratch, len(literal_values) * 8))
        # MIGRATED (row 3c-ii, slice 7): the four control-flow constructs are ONE kernel
        # primitive separated by the `op` variation, so the dispatch is uniform. Each still hands
        # the AST NODE to its sub-compiler -- `_compile_branch`/`_loop`/`_match`/`_parallel`
        # recurse into `_compile_ops` with statement lists, and re-pointing that recursion at IR
        # regions changes `_compile_ops`' own signature rather than reading a field, so it is a
        # separate slice. Disclosed rather than glossed: the DISPATCH is on the IR, the sub-
        # compilers' internals are not yet.
        elif irop.kind == "control-flow" and irop.op == "branch":
            fragments.append(
                _compile_branch(
                    op,
                    irop=irop,
                    isa=isa,
                    abi=abi,
                    backend=backend,
                    capability_addresses=capability_addresses,
                    array_addresses=array_addresses,
                    array_lengths=array_lengths,
                    record_addresses=record_addresses,
                    string_addresses=string_addresses,
                    list_addresses=list_addresses,
                    map_addresses=map_addresses,
                    map_field_positions=map_field_positions,
                    record_field_offsets=record_field_offsets,
                    recall_addresses=recall_addresses,
                    fallible_binds=fallible_binds,
                    verified_field_binds=verified_field_binds,
                    cache_addresses=cache_addresses,
                    cached_keys=cached_keys,
                    python_api_addr=python_api_addr,
                    symbols=symbols,
                    ret_len=ret_len,
                )
            )
        elif irop.kind == "control-flow" and irop.op == "loop":
            fragments.append(
                _compile_loop(
                    op,
                    irop=irop,
                    isa=isa,
                    abi=abi,
                    backend=backend,
                    capability_addresses=capability_addresses,
                    array_addresses=array_addresses,
                    array_lengths=array_lengths,
                    record_addresses=record_addresses,
                    string_addresses=string_addresses,
                    list_addresses=list_addresses,
                    map_addresses=map_addresses,
                    map_field_positions=map_field_positions,
                    record_field_offsets=record_field_offsets,
                    recall_addresses=recall_addresses,
                    fallible_binds=fallible_binds,
                    verified_field_binds=verified_field_binds,
                    cache_addresses=cache_addresses,
                    cached_keys=cached_keys,
                    python_api_addr=python_api_addr,
                    symbols=symbols,
                    ret_len=ret_len,
                    induction_ranges=ranges,
                )
            )
        elif irop.kind == "control-flow" and irop.op == "match":
            fragments.append(
                _compile_match(
                    op,
                    irop=irop,
                    isa=isa,
                    abi=abi,
                    backend=backend,
                    capability_addresses=capability_addresses,
                    array_addresses=array_addresses,
                    array_lengths=array_lengths,
                    record_addresses=record_addresses,
                    string_addresses=string_addresses,
                    list_addresses=list_addresses,
                    map_addresses=map_addresses,
                    map_field_positions=map_field_positions,
                    record_field_offsets=record_field_offsets,
                    recall_addresses=recall_addresses,
                    fallible_binds=fallible_binds,
                    verified_field_binds=verified_field_binds,
                    cache_addresses=cache_addresses,
                    cached_keys=cached_keys,
                    python_api_addr=python_api_addr,
                    symbols=symbols,
                    ret_len=ret_len,
                )
            )
        elif irop.kind == "control-flow" and irop.op == "parallel":
            fragments.append(
                _compile_parallel(
                    op,
                    isa=isa,
                    abi=abi,
                    capability_addresses=capability_addresses,
                    symbols=symbols,
                    ret_len=ret_len,
                )
            )
    return b"".join(fragments)


def compile_goal(
    goal: Goal,
    isa: Isa,
    capability_addresses: dict[str, int],
    array_addresses: dict[str, int] | None = None,
    record_addresses: dict[str, int] | None = None,
    string_addresses: dict[str, int] | None = None,
    list_addresses: dict[str, int] | None = None,
    map_addresses: dict[str, int] | None = None,
    recall_addresses: dict[str, int] | None = None,
    cache_addresses: dict[str, int] | None = None,
    python_api_addr: int | None = None,
    abi: Abi = "win64",
    _out_frame_size: list[int] | None = None,
) -> bytes:
    """**The actual `.tamil` -> native pipeline** (RFC-0002 §7.1/§7.5): a SEPARATE track from
    `interpret()`'s orchestration path, per the RFC's own explicit design -- §7.2/§7.3 state
    plainly that "machine-code compilation buys nothing" in orchestration; it matters only for
    the deterministic compute-substrate. This is deliberately not wired into
    `escalation.route()`/`interpret()` -- it's the (B) native track running in parallel with
    (A) the interpreted v0, decoupled by the AST itself serving as the stable `.tamil-IR` seam
    (no separate IR built yet).

    v0 scope: compiles a goal's `capability-call`/`Bind`/`Remember`/`Branch` sequence into ONE
    native function. `Govern` is validated as a static, spawn-time gate (same semantics as
    `interpreter.py`'s `_rank_from_govern`) but not itself compiled to a runtime check.
    `capability_addresses` maps a `.tamil` capability name to its already-resolved real function
    address -- resolving a capability NAME to an address is a separate, undesigned concern
    (today's capabilities run through Python tool-calling infra, not raw C-ABI functions); this
    only concatenates stencils once addresses are known.

    **Bind/Remember:** every `Bind.target`/`Remember.key` gets its own local stack slot (a real
    symbol table, first-appearance order, collected recursively through any `Branch` bodies too),
    generalizing `emit_symbol_roundtrip`'s one hardcoded slot to N named ones. A goal with any
    Bind/Remember gets a real persistent stack frame (`emit_frame_prologue`/`emit_frame_epilogue`)
    wrapping the whole compiled body instead of T8.5's plain call-stencil concatenation; a goal
    with only `capability-call`s (no locals needed) keeps that exact original path unchanged,
    byte-for-byte. `Remember`'s value can be an integer literal, a name already bound earlier in
    the SAME goal, or (T8.14) a `Recall` -- a real memory-graph read.

    **Branch (this increment):** recursively compiles arbitrary-length `then`/`otherwise`
    statement lists (including nested Branches), not `emit_branch`'s fixed two-value select.
    Only a `name <op> literal` condition is supported -- the compared name must already be
    bound by an earlier `Bind`/`Remember`, since a top-level goal has no incoming argument for
    its own condition variable to live in.

    **Loop:** supports `for i in range(start, stop) { body }` via loop rotation -- `iterable`
    must be a `RangeLiteral` with integer-literal bounds; any other iterable (a `Recall`-bound
    range, or a real array/collection) raises `UnsupportedNode` with a distinct, honest message.

    **Arrays (T8.13):** `Remember(key, ArrayLiteral(...))` declares an array exists -- pure
    compile-time bookkeeping, zero emitted code, same shape `RangeLiteral` already has.
    `Remember(key, ArrayIndex(array, index))` reads one element back (bounds-checked against the
    array's own declared length, `index` must be an integer literal for v0). Neither statement
    ever allocates: `array_addresses` maps every array name `collect_arrays(goal)` finds to its
    already-real, already-materialized base address -- resolving/allocating that memory is the
    CLOSED tree's job (D58), done BEFORE this function is ever called, exactly the same
    "resolved-address-in, bytes-out" shape `capability_addresses` already has.

    **Recall (T8.14):** `Remember(key, Recall(other_key))` reuses T8.13's `emit_load_absolute` to
    read a real int back from `recall_addresses[other_key]` -- zero new stencil bytes needed.
    There's no compute-substrate reason to route the memory-graph LOOKUP itself through compiled
    machine code (unlike `capability-call`, a lookup is I/O-bound, not something native speed
    helps); only the VALUE needs to be readable at native speed by whatever the goal does with it
    next (a Branch, a further Call). So the CLOSED tree (`madras.dsl.kollan_recall.
    resolve_recalls`) calls the real provider directly in Python for every key
    `collect_recalls(goal)` finds, writes each result into its own real memory cell, and hands
    `compile_goal` the resulting addresses -- the same "resolved-address-in, bytes-out" shape
    `array_addresses`/`capability_addresses` already have. `Recall` used anywhere other than a
    top-level `Remember` value (a `Call` argument, a `Loop`/`Range` bound) stays out of scope, a
    distinct, not-yet-tackled gap.

    **Result-as-value error handling (T8.16):** a `Bind` whose `call.capability_kind ==
    "fallible"` stores the FULL 64-bit packed `(tag << 32) | value` result (T8.15's convention)
    into its slot via `emit_store_local64`, not `emit_store_local`'s 32-bit-only store -- the
    target function is expected to return this packed shape instead of a plain scalar.
    `Remember(key, ResultOk(bind))` (`is_ok(bind)`) and `Remember(key, ResultValue(bind))`
    (`payload(bind)`) read the tag/value back out via `emit_load_local64` (+ `emit_shift_right_32`
    for the tag only -- the value needs no shift, already in the low 32 bits). Rust/Go's
    Result-as-value precedent, NOT real stack-unwinding -- avoids repeating T8.11's expensive
    unwind-table fight for every error path. `bind` must reference a name actually bound by a
    `fallible` call in the SAME goal; referencing a plain (non-fallible) Bind's name raises
    `UnsupportedNode` -- its high 32 bits are undefined stack garbage, not a real tag.

    **Capability-call result cache, v0 (T8.17):** a `Bind(cached=True)` checks a real,
    persistent cache entry FIRST (keyed by the Bind's own `mugavari_id`, a stable per-call-site
    address): `emit_load_absolute64` + `emit_shift_right_32` reads the entry's "populated" tag;
    if populated (a cache HIT), the real call is skipped entirely and the cached value (already
    in the low 32 bits) is used. If not (a cache MISS), the real call runs, its result is stored
    locally as normal, `emit_set_bit32` marks it populated with NO scratch register (every other
    bit, incl. the real result, is left untouched), and `emit_store_absolute64` writes the packed
    entry back -- the SAME `(tag << 32) | value` shape T8.15/T8.16 already established, reused
    here as `(populated << 32) | value`. The hit/miss dispatch reuses `emit_compare_and_jump_if_
    false`/`emit_jump` UNCHANGED, the exact same fragments `_compile_branch` already uses -- no
    new control-flow primitive needed. `cache_addresses` maps every Mugavari ID
    `collect_cached_binds(goal)` finds to its already-real, already-allocated (and, for a
    previously-unseen ID, zero-initialized -- "empty") arena slot; allocating that memory is the
    CLOSED tree's job (`madras.dsl.kollan_cache.ResultCache`), done BEFORE this function is ever
    called. v0 scope: fixed-capacity, no eviction, keyed purely by call SITE (not by argument
    values) -- a real, stated simplification, not a hidden one; only a plain `native` call may be
    cached (composing with `fallible`/`ffi_bridge` needs its own real storage-shape design).

    **A real `ffi_bridge` capability-call, from real `.tamil` source (T9.2):** a bare `Call`
    whose `capability_kind == "ffi_bridge"` (e.g. `ffi python append_and_verify_audit_entry()`)
    is compiled as `emit_python_call(isa, capability_addresses[name], python_api_addr)` -- T8.11's
    real CPython FFI bridge, driven for the first time by a genuine PARSED `.tamil` SOURCE
    program instead of a hand-passed Python reference (T9.1's own honest-scope gap, closed here).
    `capability_addresses[name]` holds the target Python callable's own `id(...)` (the same
    resolution `madras.dsl.kollan_bridge.call_python_object` already does); `python_api_addr` is
    `PyObject_CallObject`'s own resolved-at-runtime address, shared by every `ffi_bridge` call in
    the goal. **v0 scope, deliberately narrow:** only a BARE `Call` (never a `Bind` -- storing its
    result needs a real 64-bit local, which needs `emit_frame_prologue`'s frame-pointer prologue
    to have real unwind info registered for it, not built), and only when it's the ONE AND ONLY
    statement anywhere in the whole goal (no other Call/Bind/Remember, incl. inside Branch/Loop
    bodies, and `symbols` must be empty) -- this keeps the compiled shape byte-identical to
    `emit_python_call` alone, exactly what the EXISTING unwind-info builder
    (`madras.dsl.kollan._build_unwind_info`, sized for `emit_python_call`'s own `sub rsp,0x28`
    prologue as the function's very FIRST bytes) already handles correctly. A real correctness
    point caught before it shipped, not assumed: an ffi_bridge call merely being the goal's LAST
    statement (with unrelated calls preceding it) is NOT enough -- those preceding calls' own
    self-balancing `sub/add rsp` fragments would put `emit_python_call`'s prologue somewhere
    OTHER than byte 0, which the registered unwind info doesn't describe. Violating any of this
    raises `UnsupportedNode` rather than silently producing code that could crash or misbehave at
    execution time -- the exact same GIL/unwind-info mismatch T8.14's own first (corrected)
    attempt at embedding a Python call inside a locals-having frame already found and fixed by
    NOT doing that; this increment stays inside the shape that's actually proven safe.

    **G8 lifts this restriction for real**, the missing piece T8.14 didn't have: an `ffi_bridge`
    call may now appear anywhere alongside other statements, AS LONG AS `compile_goal` forces a
    real frame (`__ffi_frame__`) whenever it isn't the exact original bare-single-call shape --
    `_build_framed_unwind_info`/`run_framed_call_with_unwind` (isolated-and-verified before
    anything was built on top of them) correctly register unwind info for that real `push rbp`
    frame shape, closing the gap T8.14 walked back from. `_out_frame_size`, if given a list,
    gets the real frame size (0 = no frame, the original shape) appended -- callers like
    `madras.dsl.kollan_bridge.run_compiled_capability_call` need this to know which unwind-info
    shape to register when actually running the compiled bytes.
    """
    if isa not in _BACKENDS:
        raise UnsupportedIsa(f"no backend for isa {isa!r} -- supported: {sorted(_BACKENDS)}")
    backend = _BACKENDS[isa]

    # G11 -- lower every `derive` into `Remember(Compute(...))` + its re-emitted cascade
    # copies BEFORE anything else runs (`_lower_result_arms`'s own "reuse existing nodes"
    # shape, G9): rebinding the local `goal` name means every downstream reference (symbol
    # collection, move-checking, codegen) automatically sees the lowered body -- zero other
    # changes needed anywhere else in this function.
    goal = goal.model_copy(update={"body": _lower_derives(goal.body)})

    check_moves(goal)  # G6 -- raises UnsupportedNode on a real use-after-move, before anything
    # else runs; on by default (D70's own "moves are stage 1" mandate, not opt-in).

    ops = _validate_and_filter(goal.body)
    if not ops:
        raise UnsupportedNode("compile_goal needs at least one statement to compile")

    # Lowered ONCE and reused: the symbol table, the literal census, the field/element ordering
    # and the cache-key map are all questions about the same program, and asking them of one
    # module rather than of the AST four separate times is what keeps them from drifting apart.
    # Lowered AFTER `_lower_derives` above, so a `derive` is already a `remember` here.
    ir_module = lower_to_nadi(goal)

    # Slot numbers ARE insertion order, and they appear directly in the emitted bytes -- so this
    # swap is verified the only way it safely can be, byte-identically against every golden.
    symbols: dict[str, int] = dict(nadi_symbols(ir_module))
    array_lengths = {name: len(elements) for name, elements in nadi_arrays(ir_module).items()}
    resolved_array_addresses = array_addresses or {}
    resolved_record_addresses = record_addresses or {}
    resolved_string_addresses = string_addresses or {}
    resolved_list_addresses = list_addresses or {}
    resolved_map_addresses = map_addresses or {}
    # Field/element ORDER (name -> ordinal) is derived from the AST itself, same as
    # `array_lengths` -- only the real memory ADDRESS needs a caller (the closed tree, which
    # allocated it). G5's map hop-count is the SAME "position is the address" idea, just walked
    # at runtime (a chunk chain) instead of read at a fixed byte offset (a record).
    record_field_offsets = {
        name: {field: i for i, field in enumerate(fields)}
        for name, fields in nadi_records(ir_module).items()
    }
    map_field_positions = {
        name: {field: i for i, field in enumerate(fields)}
        for name, fields in nadi_maps(ir_module).items()
    }
    map_set_targets: set[str] = set()
    _collect_map_set_targets_into(goal.body, map_set_targets)
    for mutated_name in map_set_targets:
        map_field_positions.pop(mutated_name, None)
    # A verified-field read (G3) needs one internal scratch local slot -- reserved here,
    # BEFORE frame_size is computed from len(symbols), whenever the goal uses any record at all
    # (reserving unconditionally on record USE, not just verified reads, is a real but harmless
    # over-reservation: 8 bytes, never wrong).
    if record_field_offsets:
        symbols.setdefault("__verify_scratch__", len(symbols))
    # G5's `push`/`mapset` need one internal scratch slot too (the previous head, stashed while
    # `emit_alloc`'s call clobbers RAX) -- same unconditional-on-USE over-reservation as above.
    if resolved_list_addresses or resolved_map_addresses:
        symbols.setdefault("__push_scratch__", len(symbols))
    # G8 -- lifts T9a's one-bare-call-per-goal restriction: an `ffi_bridge` call needs a REAL
    # registered-unwind-info frame the moment it's anything OTHER than the goal's sole statement
    # with no locals at all (the original T8.11/T9.2 shape, left byte-identical and untouched --
    # see `_compile_ops`'s own ffi_bridge dispatch). Forcing a frame here (via a reserved dummy
    # slot, the SAME "reserve on USE" pattern `__verify_scratch__`/`__push_scratch__` already
    # use) makes `tail_keep_ret` False downstream, which is what actually tells `_compile_ops`
    # to embed `emit_python_call` as a stripped-`ret` FRAGMENT instead of the whole function.
    has_ffi_bridge = any(
        (isinstance(op, Call) and op.capability_kind == "ffi_bridge")
        # T9b: a Bind whose call is ffi_bridge needs the SAME forced-frame treatment -- it
        # always reserves a symbol slot anyway (its own target), so this branch is here for
        # documentation/correctness of intent, not because it changes `symbols`' emptiness.
        or (
            isinstance(op, Bind)
            and isinstance(op.call, Call)
            and op.call.capability_kind == "ffi_bridge"
        )
        for op in ops
    )
    if has_ffi_bridge and not (len(ops) == 1 and not symbols):
        symbols.setdefault("__ffi_frame__", len(symbols))
    # G9 -- Result matching needs one internal scratch slot too (`is_ok`'s own tag, held while
    # each arm's payload gets read) -- same unconditional-on-USE over-reservation as above.
    if _stmts_have_match(goal.body):
        symbols.setdefault("__match_tag__", len(symbols))
    # G11 -- a `derive`/Compute value needs one internal scratch slot too (a literal right
    # operand is materialized here before the register-memory arithmetic op runs against it)
    # -- same unconditional-on-USE over-reservation as above.
    if _stmts_have_compute(goal.body):
        symbols.setdefault("__compute_scratch__", len(symbols))
    # N2 -- a content-addressed cached-call-with-args probe needs its own scratch slots too --
    # same unconditional-on-USE over-reservation as above.
    if _stmts_have_cached_call_with_args(goal.body):
        for scratch in _CACHE_PROBE_SCRATCH:
            symbols.setdefault(scratch, len(symbols))
    # G10 -- a `parallel` block needs a few shared scratch slots (reused sequentially, never
    # simultaneously live -- see `_compile_parallel`) plus one PERSISTENT handle slot per
    # branch (must stay alive from spawn through its own join) -- reserved here, on USE,
    # same pattern `__match_tag__`/`__verify_scratch__` already established.
    goal_parallels = _walk_parallels(goal.body)
    if goal_parallels:
        symbols.setdefault(_PARALLEL_ZERO, len(symbols))
        symbols.setdefault(_PARALLEL_INFINITE, len(symbols))
        symbols.setdefault(_PARALLEL_SCRATCH_A, len(symbols))
        symbols.setdefault(_PARALLEL_SCRATCH_B, len(symbols))
        for p in goal_parallels:
            for i in range(len(p.body)):
                symbols.setdefault(f"__parallel_{id(p)}_handle_{i}__", len(symbols))
    resolved_recall_addresses = recall_addresses or {}
    fallible_binds: set[str] = set()
    _collect_fallible_binds(goal.body, fallible_binds)
    verified_field_binds: set[str] = set()
    _collect_verified_field_binds(goal.body, verified_field_binds)
    resolved_cache_addresses = cache_addresses or {}

    ret_len = _RET_LEN[isa]
    body = _compile_ops(
        ops,
        isa=isa,
        abi=abi,
        backend=backend,
        capability_addresses=capability_addresses,
        array_addresses=resolved_array_addresses,
        array_lengths=array_lengths,
        record_addresses=resolved_record_addresses,
        string_addresses=resolved_string_addresses,
        list_addresses=resolved_list_addresses,
        map_addresses=resolved_map_addresses,
        map_field_positions=map_field_positions,
        record_field_offsets=record_field_offsets,
        recall_addresses=resolved_recall_addresses,
        fallible_binds=fallible_binds,
        verified_field_binds=verified_field_binds,
        cache_addresses=resolved_cache_addresses,
        cached_keys=cached_key_map(goal),
        python_api_addr=python_api_addr,
        symbols=symbols,
        ret_len=ret_len,
        tail_keep_ret=not symbols,
    )

    if symbols:
        n_slots = len(symbols)
        prologue = backend.emit_frame_prologue(n_slots)
        epilogue = backend.emit_frame_epilogue(n_slots)
        if _out_frame_size is not None:
            _out_frame_size.append(frame_size(isa, n_slots))
        return prologue + body + epilogue
    if _out_frame_size is not None:
        _out_frame_size.append(0)
    return body


def _strip_returns_for_symbol_collection(stmts: list) -> list:
    """`_collect_symbols`/`_collect_fallible_binds` predate G1 and don't know about `Return`
    (their own `_validate_and_filter` call correctly rejects any node kind they don't recognize)
    -- build a stripped COPY (every `Return` removed, recursively through `Branch` bodies) purely
    for those two collectors to walk; `compile_fndef`'s own codegen logic uses the REAL body
    (with its `Return`s) and handles them itself, in tail position only (v0 scope)."""
    out: list = []
    for stmt in stmts:
        if isinstance(stmt, Return):
            continue
        if isinstance(stmt, Branch):
            out.append(
                stmt.model_copy(
                    update={
                        "then": _strip_returns_for_symbol_collection(stmt.then),
                        "otherwise": _strip_returns_for_symbol_collection(stmt.otherwise),
                    }
                )
            )
        else:
            out.append(stmt)
    return out


def _load_return_value(value, symbols: dict[str, int], backend) -> bytes:
    """Load a `Return`'s value into the return register (EAX on x86-64) -- v0 scope: only a
    bound local name or an integer literal (the `str`-only `Value` shape most of `compile_goal`'s
    own helpers already restrict to, e.g. `Remember`'s literal/name case above); anything else
    raises `UnsupportedNode`."""
    if not isinstance(value, str):
        raise UnsupportedNode(
            f"return only supports a bound name or an integer literal so far, got {value!r}"
        )
    if value in symbols:
        return backend.emit_load_local(symbols[value])
    try:
        n = int(value)
    except ValueError as exc:
        raise UnsupportedNode(
            f"return value {value!r} is neither a name bound earlier in this fn nor an "
            "integer literal"
        ) from exc
    return backend.emit_load_immediate(n)


def compile_fndef(
    fndef: FnDef,
    isa: Isa,
    capability_addresses: dict[str, int],
    abi: Abi = "win64",
    record_addresses: dict[str, int] | None = None,
    string_addresses: dict[str, int] | None = None,
    list_addresses: dict[str, int] | None = None,
    map_addresses: dict[str, int] | None = None,
) -> bytes:
    """Compile a user-defined function (G1, plan-local D69) into a real, independently-callable
    native routine -- reuses `compile_goal`'s own statement-compilation machinery
    (`_compile_ops`/`_collect_symbols`), always wrapped in a real frame prologue/epilogue (unlike
    `compile_goal`'s no-locals fast path -- a `fn` must always be safely callable/re-enterable
    from multiple, possibly-recursive call sites, so it always gets its own real stack frame,
    even with zero locals).

    **Calling another fn (including itself, recursively) is a PLAIN `Call` resolved through the
    SAME `capability_addresses` map `compile_goal` already uses** -- a user-defined fn IS a
    capability name resolving to a real function address; no special-casing needed here at all
    (founder's own s55 ruling: a call lowers to the existing `capability-call` kernel node, no
    7th node). The CLOSED tree is responsible for placing this function's compiled bytes in real
    executable memory and, for a fn that calls itself, registering its OWN address in
    `capability_addresses` BEFORE compiling it: this function's byte length never depends on any
    address VALUE (`emit_capability_call`'s target is a fixed-width 64-bit immediate,
    `struct.pack("<Q", ...)`), so a two-pass "measure with a placeholder, then compile for real
    once the real address is known" scheme works -- the closed tree's job, not this function's.

    **`return` (v0 scope, G1):** only supported as (a) the fn body's own last statement, or (b)
    the last statement of EACH arm of a `Branch` that is itself the fn body's last statement.
    Only a bound local name or an integer literal may be returned (`_load_return_value`).
    Anything else -- a `return` at any other position, a Branch not in tail position, inside a
    `Loop` -- raises `UnsupportedNode`: an honest v0 boundary, not a silent gap; arbitrary
    mid-block `return` needs a general tail-length-threading scheme through every nested
    compiler, not attempted here.

    **Recursion needs no special handling beyond this**: a real x86-64 `call`/`ret` (via
    `emit_capability_call`, NOT the tail-call-fallthrough path `compile_goal` uses for adjacent
    same-goal calls) gives every invocation its own fresh rbp-relative frame on the real call
    stack -- `emit_frame_prologue`/`emit_frame_epilogue` already establish a genuine
    `push rbp; mov rbp,rsp` frame, exactly like ordinary C recursion; no new stencil needed.
    """
    if isa not in _BACKENDS:
        raise UnsupportedIsa(f"no backend for isa {isa!r} -- supported: {sorted(_BACKENDS)}")
    backend = _BACKENDS[isa]

    if not fndef.body:
        raise UnsupportedNode(f"fn {fndef.name!r} needs at least one statement to compile")

    # G11 -- same lowering rebind compile_goal's own does, before anything else runs (a
    # trailing `Return` falls through `_lower_derives_stmts` unchanged -- it's neither a
    # `Derive` nor a name-writing statement `_written_name` would trigger a cascade from).
    fndef = fndef.model_copy(update={"body": _lower_derives(fndef.body)})

    # G6 -- runs on `fndef.body` UNSTRIPPED (unlike the collectors below): a fn's own `Return`
    # can move a value out, and check_moves needs to see that use, not have it stripped first.
    check_moves(fndef)

    collectible_body = _strip_returns_for_symbol_collection(fndef.body)
    symbols: dict[str, int] = {}
    # G8 -- params get the FIRST N slots, in declaration order, matching the fixed ABI register
    # order `emit_store_args_to_locals` spills them in (param i -> arg register i -> slot i).
    # `_collect_symbols`'s own `setdefault` below leaves these untouched and continues
    # numbering NEW body-declared locals from `len(symbols)` onward.
    for i, param in enumerate(fndef.params):
        symbols[param] = i
    _collect_symbols(collectible_body, symbols)
    fallible_binds: set[str] = set()
    _collect_fallible_binds(collectible_body, fallible_binds)
    verified_field_binds: set[str] = set()
    _collect_verified_field_binds(collectible_body, verified_field_binds)
    ret_len = _RET_LEN[isa]
    resolved_record_addresses = record_addresses or {}
    resolved_string_addresses = string_addresses or {}
    resolved_list_addresses = list_addresses or {}
    resolved_map_addresses = map_addresses or {}
    # Field ORDER is derived from the AST itself, same as compile_goal's own derivation --
    # only the real memory ADDRESS needs a caller.
    fn_records: dict[str, dict[str, int]] = {}
    _collect_records_into(collectible_body, fn_records)
    record_field_offsets = {
        name: {field: i for i, field in enumerate(fields)} for name, fields in fn_records.items()
    }
    fn_maps: dict[str, dict[str, int]] = {}
    _collect_maps_into(collectible_body, fn_maps)
    map_field_positions = {
        name: {field: i for i, field in enumerate(fields)} for name, fields in fn_maps.items()
    }
    fn_map_set_targets: set[str] = set()
    _collect_map_set_targets_into(collectible_body, fn_map_set_targets)
    for mutated_name in fn_map_set_targets:
        map_field_positions.pop(mutated_name, None)
    # A verified-field read (G3) needs one internal scratch local slot -- reserved BEFORE
    # compile_fndef always builds its frame (it always has one, per this function's own docstring).
    if record_field_offsets:
        symbols.setdefault("__verify_scratch__", len(symbols))
    # G5's `push`/`mapset` need one internal scratch slot too -- same over-reservation-on-USE.
    if resolved_list_addresses or resolved_map_addresses:
        symbols.setdefault("__push_scratch__", len(symbols))
    # G9 -- Result matching needs one internal scratch slot too -- same over-reservation-on-USE.
    if _stmts_have_match(collectible_body):
        symbols.setdefault("__match_tag__", len(symbols))
    # G11 -- same on-USE reservation as compile_goal's own (see its comment for the shape).
    if _stmts_have_compute(collectible_body):
        symbols.setdefault("__compute_scratch__", len(symbols))
    # N2 -- same on-USE reservation as compile_goal's own (see its comment for the shape).
    if _stmts_have_cached_call_with_args(collectible_body):
        for scratch in _CACHE_PROBE_SCRATCH:
            symbols.setdefault(scratch, len(symbols))
    # G10 -- same on-USE reservation as compile_goal's own (see its comment for the shape).
    fn_parallels = _walk_parallels(collectible_body)
    if fn_parallels:
        symbols.setdefault(_PARALLEL_ZERO, len(symbols))
        symbols.setdefault(_PARALLEL_INFINITE, len(symbols))
        symbols.setdefault(_PARALLEL_SCRATCH_A, len(symbols))
        symbols.setdefault(_PARALLEL_SCRATCH_B, len(symbols))
        for p in fn_parallels:
            for i in range(len(p.body)):
                symbols.setdefault(f"__parallel_{id(p)}_handle_{i}__", len(symbols))

    def _compile_prefix(stmts: list) -> bytes:
        ops = _validate_and_filter(stmts)
        return _compile_ops(
            ops,
            isa=isa,
            abi=abi,
            backend=backend,
            capability_addresses=capability_addresses,
            array_addresses={},
            array_lengths={},
            record_addresses=resolved_record_addresses,
            string_addresses=resolved_string_addresses,
            list_addresses=resolved_list_addresses,
            map_addresses=resolved_map_addresses,
            map_field_positions=map_field_positions,
            record_field_offsets=record_field_offsets,
            recall_addresses={},
            fallible_binds=fallible_binds,
            verified_field_binds=verified_field_binds,
            cache_addresses={},
            cached_keys=cached_key_map(fndef),
            python_api_addr=None,
            symbols=symbols,
            ret_len=ret_len,
            tail_keep_ret=False,
        )

    last = fndef.body[-1]
    if isinstance(last, Return):
        body_bytes = _compile_prefix(fndef.body[:-1]) + _load_return_value(
            last.value, symbols, backend
        )
    elif isinstance(last, Branch):
        then_last = last.then[-1] if last.then else None
        else_last = last.otherwise[-1] if last.otherwise else None
        if not (isinstance(then_last, Return) and isinstance(else_last, Return)):
            raise UnsupportedNode(
                f"fn {fndef.name!r}'s trailing Branch must end BOTH arms in `return` (v0 scope) "
                "-- a Branch without a terminal return in both arms needs a real "
                "tail-length-threading scheme, not built yet"
            )
        expr = parse_condition(last)
        if (
            not isinstance(expr, Compare)
            or not isinstance(expr.left, Name)
            or not isinstance(expr.right, Num)
        ):
            raise UnsupportedNode(
                f"fn {fndef.name!r}'s trailing Branch condition must be `name <op> literal` "
                f"(got {last.condition!r})"
            )
        if expr.left.name not in symbols:
            raise UnsupportedNode(
                f"fn {fndef.name!r}'s trailing Branch condition references {expr.left.name!r}, "
                "which isn't bound earlier in this same fn"
            )
        prefix_bytes = _compile_prefix(fndef.body[:-1])
        then_bytes = _compile_prefix(last.then[:-1]) + _load_return_value(
            then_last.value, symbols, backend
        )
        else_bytes = _compile_prefix(last.otherwise[:-1]) + _load_return_value(
            else_last.value, symbols, backend
        )
        load_cond = backend.emit_load_local(symbols[expr.left.name])
        jump_over_else = backend.emit_jump(len(else_bytes))
        skip_len = len(then_bytes) + len(jump_over_else)
        compare_and_branch = backend.emit_compare_and_jump_if_false(
            expr.op, expr.right.value, skip_len
        )
        body_bytes = (
            prefix_bytes + load_cond + compare_and_branch + then_bytes + jump_over_else + else_bytes
        )
    else:
        raise UnsupportedNode(
            f"fn {fndef.name!r} must end in a `return` statement or a Branch whose both arms "
            f"end in `return` (v0 scope), got {last.kind!r}"
        )

    n_slots = len(symbols)
    prologue = backend.emit_frame_prologue(n_slots)
    epilogue = backend.emit_frame_epilogue(n_slots)
    param_slots = [symbols[p] for p in fndef.params]
    store_args = emit_store_args_to_locals(isa, param_slots, abi) if param_slots else b""
    return prologue + store_args + body_bytes + epilogue


__all__ = [
    "Abi",
    "Isa",
    "Op",
    "UnsupportedIsa",
    "UnsupportedNode",
    "UnsupportedOp",
    "check_moves",
    "collect_arrays",
    "collect_cached_binds",
    "collect_lists",
    "collect_maps",
    "collect_recalls",
    "collect_records",
    "collect_strings",
    "compile_fndef",
    "compile_goal",
    "decode_riscv64",
    "emit_alloc",
    "emit_branch",
    "emit_branch_on_compare",
    "emit_call_with_args",
    "emit_capability_call",
    "emit_eax_is_zero",
    "emit_frame_epilogue",
    "emit_frame_prologue",
    "emit_govern_check",
    "emit_lea_local",
    "emit_link_local_into_indirect",
    "emit_load_absolute",
    "emit_load_absolute64",
    "emit_load_immediate64",
    "emit_load_immediate_arg1",
    "emit_load_indirect_offset",
    "emit_load_local64",
    "emit_or_local64",
    "emit_python_call",
    "emit_set_bit32",
    "emit_shift_right_32",
    "emit_shl_rax_32",
    "emit_store_absolute64",
    "emit_store_args_to_locals",
    "emit_store_immediate_indirect",
    "emit_store_local64",
    "emit_symbol_roundtrip",
    "emit_syscall",
    "emit_xor_local",
    "frame_size",
    "lower_branch",
]
