"""Kollan (T8) -- the execution half. `tamil_lang.kollan` emits pure machine-code bytes (no
I/O, no allocation); running those bytes as real native code is exactly the "fuzzy/untrusted
work" the Sandboxed law (`dsl/sandboxed.py`) exists for, so it lives here in the closed tree,
same D58 split as `ffi_bridge`'s declare-vs-execute boundary.

**x86-64/Windows + x86-64/Linux (G2).** Windows: allocates a PAGE_EXECUTE_READWRITE region via
`VirtualAlloc` in one call (unchanged since before G2). Linux (G2, new): real POSIX `mmap`+
`mprotect` -- reserve RW memory, copy the stencil bytes in, THEN `mprotect` to RX (W^X: never
simultaneously writable and executable) -- verified live via WSL, not just encoded. Either way,
the caller is responsible for (a) only ever running bytes that came from a trusted emitter
(`tamil_lang.kollan`), never arbitrary/untrusted machine code, and (b) having compiled those
bytes with the matching ABI (`abi="win64"` on Windows, `abi="sysv"` on Linux -- G2).

**RISC-V has no execution path here on purpose.** This dev machine has no RV64 hardware and no
`ctypes`-reachable RISC-V execution surface; `tamil_lang.kollan.riscv64`'s stencils are verified
by encode/decode round-trip instead (an honest substitute -- see that module's own docstring).
The real RISC-V execution path is the Shakti QEMU/gem5 toolchain built for T6, wired up later
once Kollan actually needs it, not simulated here with an unproven claim.
"""

from __future__ import annotations

import ctypes
import platform
import struct

from tamil_lang import Abi, FnDef, Goal, compile_fndef, compile_goal, extract_parallel_branches

_MEM_COMMIT = 0x1000
_MEM_RESERVE = 0x2000
_PAGE_EXECUTE_READWRITE = 0x40
_MEM_RELEASE = 0x8000
_UWOP_PUSH_NONVOL = 0
_UWOP_ALLOC_LARGE = 1
_UWOP_ALLOC_SMALL = 2
_UWOP_SET_FPREG = 3
_REG_RBP = 5

# G2 -- Linux x86-64's mmap/mprotect flag values (from <sys/mman.h>; MAP_ANONYMOUS's value is
# Linux-specific -- macOS/BSD use 0x1000, a real difference if this ever targets those hosts).
_PROT_READ = 0x1
_PROT_WRITE = 0x2
_PROT_EXEC = 0x4
_MAP_PRIVATE = 0x02
_MAP_ANONYMOUS = 0x20


class _RUNTIME_FUNCTION(ctypes.Structure):
    """Windows x64's own `.pdata` entry shape -- 3 DWORDs, all RVAs relative to whatever
    `BaseAddress` is passed to `RtlAddFunctionTable`."""

    _fields_ = [
        ("BeginAddress", ctypes.c_uint32),
        ("EndAddress", ctypes.c_uint32),
        ("UnwindInfoAddress", ctypes.c_uint32),
    ]


class UnsupportedPlatform(RuntimeError):
    """Kollan's executor only runs on Windows/x86_64 or Linux/x86_64 so far (G2) -- fails loudly
    rather than silently miscompiling/crashing on an unproven host."""


def _prepare_kernel32() -> ctypes.WinDLL:  # type: ignore[name-defined]
    if platform.system() != "Windows" or platform.machine() not in ("AMD64", "x86_64"):
        raise UnsupportedPlatform(
            f"Kollan's executor only runs on Windows/AMD64 so far, not "
            f"{platform.system()}/{platform.machine()}"
        )
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    # ctypes defaults VirtualAlloc's return to a 32-bit-truncating c_int on 64-bit Windows --
    # a real bug this module's first test run caught (access-violation writes past a truncated
    # pointer). Must set restype/argtypes explicitly to get the real 64-bit address back.
    kernel32.VirtualAlloc.restype = ctypes.c_void_p
    kernel32.VirtualAlloc.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    kernel32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
    return kernel32


def _prepare_libc() -> ctypes.CDLL:
    """G2 -- the Linux sibling of `_prepare_kernel32`: resolve `libc.so.6`'s `mmap`/`mprotect`/
    `munmap` with explicit `restype`/`argtypes` (the same 64-bit-pointer-truncation bug class
    `_prepare_kernel32`'s own docstring already found for `VirtualAlloc` -- ctypes defaults a
    function's return to a 32-bit-truncating `c_int` unless told otherwise)."""
    if platform.system() != "Linux" or platform.machine() not in ("x86_64",):
        raise UnsupportedPlatform(
            f"Kollan's Linux executor only runs on Linux/x86_64 so far, not "
            f"{platform.system()}/{platform.machine()}"
        )
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.mmap.restype = ctypes.c_void_p
    libc.mmap.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_long,
    ]
    libc.mprotect.restype = ctypes.c_int
    libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    libc.munmap.restype = ctypes.c_int
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    return libc


def _alloc_exec_region(size: int) -> int:
    """Reserve `size` bytes of real memory for one-time code placement (G2), before any bytes
    are known -- Linux reserves RW only (a real `mprotect` to RX happens after writing, in
    `_finalize_exec_region` -- W^X, never simultaneously writable and executable); Windows
    allocates RWX up front (matching the existing, unchanged `_run_windows_x86_64` convention).
    Shared by `run_compiled_fndefs`, which must allocate N regions before any of their final
    bytes are known (the two-pass placeholder-then-patch scheme)."""
    if platform.system() == "Linux":
        libc = _prepare_libc()
        addr = libc.mmap(
            None,
            ctypes.c_size_t(size),
            _PROT_READ | _PROT_WRITE,
            _MAP_PRIVATE | _MAP_ANONYMOUS,
            -1,
            0,
        )
        if addr in (0, -1) or addr == ctypes.c_void_p(-1).value:
            raise MemoryError(f"mmap failed to reserve memory (errno={ctypes.get_errno()})")
        return addr
    kernel32 = _prepare_kernel32()
    addr = kernel32.VirtualAlloc(
        None, ctypes.c_size_t(size), _MEM_COMMIT | _MEM_RESERVE, _PAGE_EXECUTE_READWRITE
    )
    if not addr:
        raise MemoryError("VirtualAlloc failed to reserve executable memory")
    return addr


def _finalize_exec_region(addr: int, size: int) -> None:
    """After writing real code into a region from `_alloc_exec_region`, make it executable --
    Linux needs a real `mprotect` (RW->RX); Windows already allocated RWX up front, a no-op."""
    if platform.system() == "Linux":
        libc = _prepare_libc()
        if libc.mprotect(addr, ctypes.c_size_t(size), _PROT_READ | _PROT_EXEC) != 0:
            errno = ctypes.get_errno()
            raise MemoryError(f"mprotect failed to make memory executable (errno={errno})")


def _free_exec_region(addr: int, size: int) -> None:
    """Free a region from `_alloc_exec_region` -- Linux `munmap` (needs the real size); Windows
    `VirtualFree` (size=0 releases the whole reservation regardless, the existing convention)."""
    if platform.system() == "Linux":
        _prepare_libc().munmap(addr, ctypes.c_size_t(size))
    else:
        _prepare_kernel32().VirtualFree(addr, 0, _MEM_RELEASE)


def _run_x86_64(code: bytes, func_type: type, *args: int) -> int:
    """Allocate real executable memory, copy `code` in, call it via `func_type`, free it.
    Shared by every x86-64 stencil runner -- the allocate/execute/free mechanics are identical
    regardless of which stencil (or arity) produced `code`. Dispatches to the real host OS (G2):
    Windows -> `_run_windows_x86_64` (VirtualAlloc, UNCHANGED from before G2 -- this existing,
    tested path is never touched); Linux -> `_run_linux_x86_64` (mmap/mprotect, new). The caller
    is responsible for having compiled `code` with the matching `abi` (Win64 on Windows, SysV on
    Linux) -- this function only runs bytes, it doesn't know which ABI they were built for."""
    if platform.system() == "Linux":
        return _run_linux_x86_64(code, func_type, *args)
    return _run_windows_x86_64(code, func_type, *args)


def _run_windows_x86_64(code: bytes, func_type: type, *args: int) -> int:
    """The original Windows/VirtualAlloc executor (pre-G2), unchanged: allocate RWX memory in one
    call, copy `code` in, call it, free it."""
    kernel32 = _prepare_kernel32()
    size = len(code)
    addr = kernel32.VirtualAlloc(
        None, ctypes.c_size_t(size), _MEM_COMMIT | _MEM_RESERVE, _PAGE_EXECUTE_READWRITE
    )
    if not addr:
        raise MemoryError("VirtualAlloc failed to reserve executable memory")
    try:
        ctypes.memmove(addr, code, size)
        native_fn = func_type(addr)
        return native_fn(*args)
    finally:
        kernel32.VirtualFree(addr, 0, _MEM_RELEASE)


def _run_linux_x86_64(code: bytes, func_type: type, *args: int) -> int:
    """G2 -- the Linux/x86_64 executor: real POSIX `mmap`+`mprotect` (not a single RWX call like
    the Windows path) -- reserve RW memory, copy the (SysV-ABI) stencil bytes in, THEN switch the
    page to RX via `mprotect` (a page is never simultaneously writable and executable, the
    standard W^X discipline), call it, `munmap`. The caller must have compiled `code` with
    `abi="sysv"` (tamil_lang.kollan) -- this function has no way to check that itself, only to
    run whatever bytes it's given."""
    libc = _prepare_libc()
    size = len(code)
    addr = libc.mmap(
        None, ctypes.c_size_t(size), _PROT_READ | _PROT_WRITE, _MAP_PRIVATE | _MAP_ANONYMOUS, -1, 0
    )
    if addr in (0, -1) or addr == ctypes.c_void_p(-1).value:
        raise MemoryError(f"mmap failed to reserve memory (errno={ctypes.get_errno()})")
    try:
        ctypes.memmove(addr, code, size)
        if libc.mprotect(addr, ctypes.c_size_t(size), _PROT_READ | _PROT_EXEC) != 0:
            errno = ctypes.get_errno()
            raise MemoryError(f"mprotect failed to make memory executable (errno={errno})")
        native_fn = func_type(addr)
        return native_fn(*args)
    finally:
        libc.munmap(addr, ctypes.c_size_t(size))


def _build_unwind_info(alloc_bytes: int, prolog_len: int) -> bytes:
    """A minimal, real Windows x64 `UNWIND_INFO` describing a single `sub rsp, imm8` prolog --
    the shape every Kollan stencil that calls back OUT (`emit_capability_call`/
    `emit_python_call`) starts with. Windows x64 requires every JIT compiler to register real
    unwind metadata for any code that calls something non-leaf (LuaJIT/V8/.NET's JIT/wasmtime
    all do this) -- omitting it is silently fatal the moment anything beneath the call needs to
    walk back past our unregistered JIT frame. Leaf calls (`GetCurrentProcessId`) never trigger
    that walk, which is exactly why they worked without this and `PyObject_CallObject` (deep,
    non-leaf CPython eval-loop code) didn't."""
    alloc_slots = (alloc_bytes // 8) - 1  # UWOP_ALLOC_SMALL packs (size/8 - 1) into one nibble
    if not (0 <= alloc_slots <= 15):
        raise ValueError(
            f"_build_unwind_info only supports a small alloc (8-128 bytes), got {alloc_bytes}"
        )
    version_and_flags = 1  # version=1, flags=0 (no exception handler / chained unwind info)
    count_of_codes = 1
    frame_register_and_offset = 0  # no frame register (RBP) used by this prolog shape
    unwind_op_and_info = (alloc_slots << 4) | _UWOP_ALLOC_SMALL
    header = struct.pack(
        "<BBBB", version_and_flags, prolog_len, count_of_codes, frame_register_and_offset
    )
    # CodeOffset is the byte offset from function entry to the END of the described instruction.
    unwind_code = struct.pack("<BB", prolog_len, unwind_op_and_info)
    padding = b"\x00\x00"  # UNWIND_CODE array is padded to an even count (DWORD alignment)
    return header + unwind_code + padding


def _build_framed_unwind_info(frame_size: int, prolog_len: int) -> bytes:
    """G8 -- a real Windows x64 `UNWIND_INFO` for `emit_frame_prologue`'s OTHER prolog shape:
    `push rbp; mov rbp,rsp; sub rsp,imm32` -- a genuine frame-pointer frame, unlike
    `_build_unwind_info`'s single `sub rsp` (no `push rbp`, no frame register). Needed the
    moment a REAL multi-local frame (G8's Python-args-with-a-tuple calls; a future embedded-
    multi-call goal) calls non-leaf code -- same "silently fatal without it" stakes
    `_build_unwind_info`'s own docstring already documents, now for a different, more complex
    prolog shape that single-code-slot builder can't describe.

    UNWIND_CODE entries are stored in REVERSE program order (the array describes how to undo
    the prolog, walked from the LAST instruction executed back to the first) -- three logical
    ops here: `sub rsp` (ALLOC_LARGE, `OpInfo=0` -- size stored as `frame_size/8` in the
    following 16-bit slot, since `frame_size` is always a multiple of 8 by construction), `mov
    rbp,rsp` (SET_FPREG, paired with the header's own `FrameRegister=RBP, FrameOffset=0`), then
    `push rbp` (PUSH_NONVOL, `OpInfo=RBP`)."""
    if frame_size % 8 != 0:
        raise ValueError(
            f"_build_framed_unwind_info needs a multiple-of-8 frame_size, got {frame_size}"
        )
    alloc_slots = frame_size // 8
    if not (0 <= alloc_slots <= 0xFFFF):
        raise ValueError(f"_build_framed_unwind_info's frame_size is too large: {frame_size}")

    push_rbp_len = 1  # `push rbp`
    mov_rbp_rsp_len = push_rbp_len + 3  # `mov rbp, rsp`
    sub_rsp_len = mov_rbp_rsp_len + 7  # `sub rsp, imm32` -- must equal prolog_len

    version_and_flags = 1
    count_of_codes = 4  # PUSH_NONVOL(1) + SET_FPREG(1) + ALLOC_LARGE(1) + its size slot(1)
    frame_register_and_offset = _REG_RBP  # FrameOffset=0, packed into the low nibble already
    header = struct.pack(
        "<BBBB", version_and_flags, prolog_len, count_of_codes, frame_register_and_offset
    )
    alloc_code = struct.pack("<BB", sub_rsp_len, (0 << 4) | _UWOP_ALLOC_LARGE)
    alloc_size_slot = struct.pack("<H", alloc_slots)
    fpreg_code = struct.pack("<BB", mov_rbp_rsp_len, (0 << 4) | _UWOP_SET_FPREG)
    push_code = struct.pack("<BB", push_rbp_len, (_REG_RBP << 4) | _UWOP_PUSH_NONVOL)
    return header + alloc_code + alloc_size_slot + fpreg_code + push_code


def _run_x86_64_with_unwind(code: bytes, func_type: type, unwind_info: bytes, *args: int) -> int:
    """Like `_run_x86_64`, but also registers real `RtlAddFunctionTable` unwind info for the
    JIT'd region before calling it, and unregisters it (`RtlDeleteFunctionTable`) afterward --
    required for any stencil that calls into non-leaf code (see `_build_unwind_info`'s own
    docstring for why). Unwind info is laid out right after the code in the SAME executable
    page, DWORD-aligned, so one `VirtualAlloc`/`VirtualFree` pair covers both. `unwind_info` is
    pre-built by the CALLER (`_build_unwind_info` for the single-`sub rsp`-no-frame-pointer
    shape `emit_capability_call`/`emit_python_call` use, `_build_framed_unwind_info` (G8) for
    `emit_frame_prologue`'s real `push rbp` frame shape) -- this runner doesn't care which,
    only that it correctly describes `code`'s own real prologue bytes."""
    kernel32 = _prepare_kernel32()
    kernel32.RtlAddFunctionTable.restype = ctypes.c_int
    kernel32.RtlAddFunctionTable.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint64]
    kernel32.RtlDeleteFunctionTable.restype = ctypes.c_int
    kernel32.RtlDeleteFunctionTable.argtypes = [ctypes.c_void_p]

    code_len = len(code)
    unwind_offset = code_len + ((-code_len) % 4)  # DWORD-align the unwind info's own start
    total_size = unwind_offset + len(unwind_info)

    addr = kernel32.VirtualAlloc(
        None, ctypes.c_size_t(total_size), _MEM_COMMIT | _MEM_RESERVE, _PAGE_EXECUTE_READWRITE
    )
    if not addr:
        raise MemoryError("VirtualAlloc failed to reserve executable memory")
    try:
        ctypes.memmove(addr, code, code_len)
        ctypes.memmove(addr + unwind_offset, unwind_info, len(unwind_info))

        rt_func = _RUNTIME_FUNCTION(
            BeginAddress=0, EndAddress=code_len, UnwindInfoAddress=unwind_offset
        )
        if not kernel32.RtlAddFunctionTable(ctypes.byref(rt_func), 1, ctypes.c_uint64(addr)):
            raise RuntimeError("RtlAddFunctionTable failed to register the JIT'd region")
        try:
            native_fn = func_type(addr)
            return native_fn(*args)
        finally:
            kernel32.RtlDeleteFunctionTable(ctypes.byref(rt_func))
    finally:
        kernel32.VirtualFree(addr, 0, _MEM_RELEASE)


_INT_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int)
_INT3_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int)
_INT0_FN = ctypes.CFUNCTYPE(ctypes.c_int)
# PYFUNCTYPE, not CFUNCTYPE -- a real, distinct finding: CFUNCTYPE releases the GIL for the
# duration of the call (it assumes long-running, GIL-agnostic C code); PyObject_CallObject
# *requires* the GIL held, since it calls back into the interpreter. PYFUNCTYPE keeps the GIL
# held throughout, exactly matching this "native code that calls back into Python" shape.
_PTR0_FN = ctypes.PYFUNCTYPE(ctypes.c_void_p)
# Plain CFUNCTYPE (not PYFUNCTYPE) is correct here -- emit_alloc is a leaf stencil, calls nothing
# back into Python, so there's no GIL/pending-exception concern PyObject_CallObject has.
_PTR1_FN = ctypes.CFUNCTYPE(ctypes.c_uint64, ctypes.c_int)
# T8.15 -- same leaf-stencil reasoning as _PTR1_FN: emit_load_immediate64/emit_shift_right_32
# call nothing out, so plain CFUNCTYPE (no GIL release concern) is correct.
_UINT64_FN = ctypes.CFUNCTYPE(ctypes.c_uint64)


def run_govern_check(code: bytes, rank: int) -> int:
    """Execute an `emit_govern_check()` (x86_64 backend) stencil against a real `rank` value,
    returning the native function's actual int result (0 or 1) -- not a simulation."""
    return _run_x86_64(code, _INT_FN, rank)


def run_symbol_roundtrip(code: bytes, value: int) -> int:
    """Execute an `emit_symbol_roundtrip()` (x86_64 backend) stencil -- stores `value` into a
    real stack-local slot inside the running native code and loads it back, returning whatever
    the native code actually produced."""
    return _run_x86_64(code, _INT_FN, value)


def run_branch(code: bytes, cond: int, a: int, b: int) -> int:
    """Execute an `emit_branch()` (x86_64 backend) stencil -- a real conditional branch across
    two real basic blocks, returning `a` if `cond != 0` else `b`, computed by the actual
    executed machine code."""
    return _run_x86_64(code, _INT3_FN, cond, a, b)


def run_branch_on_compare(code: bytes, x: int, a: int, b: int) -> int:
    """Execute an `emit_branch_on_compare()` (x86_64 backend) stencil -- the comparison fused
    directly into the conditional jump (`cmp`+`Jcc`), executed live, not computed as a separate
    boolean first."""
    return _run_x86_64(code, _INT3_FN, x, a, b)


def run_capability_call(code: bytes) -> int:
    """Execute an `emit_capability_call()` (x86_64 backend) stencil -- the stencil itself
    already has its target address patched in, so this is a genuine 0-arg call: real machine
    code, generated by our own emitter, calling a real function by address and returning
    whatever that function actually returned."""
    return _run_x86_64(code, _INT0_FN)


def run_compiled_goal(code: bytes) -> int:
    """Execute a `tamil_lang.kollan.compile_goal()` output -- the same 0-arg calling shape as a
    single `run_capability_call`, since `compile_goal` concatenates call stencils into one
    function with exactly one trailing `ret`. Named separately for API symmetry with
    `compile_goal` (the actual `.tamil` -> native pipeline entry point), not because the
    execution mechanics differ."""
    return _run_x86_64(code, _INT0_FN)


def run_python_call_raw(code: bytes) -> int | None:
    """Execute an `emit_python_call()` stencil, returning the raw `PyObject*` result pointer
    (or `None` for a NULL return -- ctypes' own `c_void_p` restype conversion) exactly as
    CPython's C API handed it back. Deliberately the *raw* pointer, not a Python object --
    converting it back (and releasing CPython's extra reference) is `kollan_bridge`'s job,
    since getting reference-counting/exception-checking right belongs next to the resolution
    logic that made the call in the first place, not buried in the generic executor.

    Uses `_run_x86_64_with_unwind`, not the plain runner: `PyObject_CallObject` is deep,
    non-leaf CPython code (unlike `GetCurrentProcessId`), and silently crashes the whole
    process without registered unwind info -- a real, diagnosed finding (see
    `_build_unwind_info`'s docstring), not a defensive guess."""
    return _run_x86_64_with_unwind(
        code, _PTR0_FN, _build_unwind_info(alloc_bytes=0x28, prolog_len=4)
    )


_FRAME_PROLOG_LEN = 11  # emit_frame_prologue's fixed byte length: push rbp(1) + mov rbp,rsp(3)
# + sub rsp,imm32(7) -- fixed regardless of frame_size, since the sub always uses the imm32 form.


def run_framed_call_with_unwind(code: bytes, frame_size: int) -> int | None:
    """G8 -- like `run_python_call_raw`, but for code built on `emit_frame_prologue`'s REAL
    `push rbp` frame shape (multiple locals) instead of `emit_python_call`'s single bare
    `sub rsp` -- needed the moment a composed call sequence (e.g. building a real Python tuple
    from N args, each step its own CPython C-API call) needs more than one scratch value alive
    at once. Registers `_build_framed_unwind_info` for the JIT'd region -- same "silently fatal
    without it" stakes as `run_python_call_raw`, now for a frame that both HAS locals and calls
    non-leaf code, which `_build_unwind_info`'s single-code-slot shape can't describe."""
    return _run_x86_64_with_unwind(
        code, _PTR0_FN, _build_framed_unwind_info(frame_size, _FRAME_PROLOG_LEN)
    )


def run_alloc(code: bytes, size: int) -> int:
    """Execute an `emit_alloc()` (x86_64 backend) stencil -- a real bump allocation: the compiled
    code itself loads the arena's live offset cell, computes `base + offset`, advances the
    offset by `size`, and returns the real address, not simulated in Python. A leaf stencil (no
    unwind-table registration needed, unlike `run_python_call_raw`)."""
    return _run_x86_64(code, _PTR1_FN, size)


def run_compiled_fndefs(
    fndefs: dict[str, FnDef],
    entry_name: str,
    capability_addresses: dict[str, int] | None = None,
    abi: Abi | None = None,
) -> int:
    """Execute a set of user-defined `.tamil` functions (G1) — including calling each other and
    calling themselves recursively — by real, resolved address, same D58 execute-in-the-closed-
    tree split every other Kollan runner uses. `tamil_lang.kollan.compile_fndef` only ever takes
    resolved-address-in, bytes-out (same contract `compile_goal` already has); THIS function is
    where those addresses get resolved for real, for a whole program of functions at once.

    `abi` (G2): `None` (the default) auto-selects the correct ABI for the HOST running this —
    `"sysv"` on Linux, `"win64"` elsewhere — since the compiled bytes must match the real
    calling convention the CPU/OS actually enforces; pass it explicitly only to force a
    mismatch for testing.

    **The two-pass placement scheme** (general enough for arbitrary call graphs, including direct
    and mutual recursion — no dependency-order sorting needed): `compile_fndef`'s output length
    never depends on any address VALUE (`emit_capability_call`'s target is a fixed-width 64-bit
    immediate), so (1) compile every fn ONCE with placeholder (zero) addresses for every fn name,
    just to measure each one's real byte length; (2) reserve one real executable region per fn
    (`_alloc_exec_region`, G2: `VirtualAlloc` on Windows, `mmap` on Linux), learning its real
    address; (3) recompile every fn again, now with the REAL address map (every fn name → its
    real address, merged with any external `capability_addresses`) — the length MUST be
    identical to the placeholder pass (asserted, not assumed: a real invariant this whole scheme
    depends on, same discipline `_compile_loop`'s own back-edge-offset assertion already uses);
    (4) copy each fn's final real bytes into its already-reserved region and make it executable
    (`_finalize_exec_region`, G2: a no-op on Windows, `mprotect` on Linux); (5) call
    `entry_name`'s real address as a 0-arg function and return its result; (6) free every region.
    """
    abi = abi or ("sysv" if platform.system() == "Linux" else "win64")
    base_addresses = dict(capability_addresses or {})
    placeholder_addresses = dict(base_addresses) | dict.fromkeys(fndefs, 0)
    lengths = {
        name: len(compile_fndef(fndef, "x86_64", placeholder_addresses, abi))
        for name, fndef in fndefs.items()
    }

    real_addresses: dict[str, int] = dict(base_addresses)
    allocated: list[tuple[int, int]] = []  # (addr, size) — freed in the finally block
    try:
        for name, size in lengths.items():
            addr = _alloc_exec_region(size)
            allocated.append((addr, size))
            real_addresses[name] = addr

        for name, fndef in fndefs.items():
            final_code = compile_fndef(fndef, "x86_64", real_addresses, abi)
            if len(final_code) != lengths[name]:
                raise RuntimeError(
                    f"fn {name!r} compiled to a different length with real addresses "
                    f"({len(final_code)}) than with placeholders ({lengths[name]}) -- the "
                    "fixed-width-immediate invariant run_compiled_fndefs depends on doesn't hold"
                )
            addr = real_addresses[name]
            ctypes.memmove(addr, final_code, len(final_code))
            _finalize_exec_region(addr, lengths[name])

        entry_fn = _INT0_FN(real_addresses[entry_name])
        return entry_fn()
    finally:
        for addr, size in allocated:
            _free_exec_region(addr, size)


def _resolve_thread_primitives() -> dict[str, int]:
    """G10 -- resolve the real OS thread-create/join primitives by address, the SAME
    `ctypes.cast(fn, c_void_p).value` pattern `GetCurrentProcessId`'s own Iter4 proof already
    established (G2's `_win_api_addr` precedent, generalized). Windows: kernel32
    `CreateThread`/`WaitForSingleObject`. Linux: `pthread_create`/`pthread_join` -- merged into
    glibc's own `libc.so.6` since glibc 2.34, no separate `libpthread.so.6` load needed on a
    modern distro (this dev machine's WSL2 Ubuntu 26.04 included)."""
    if platform.system() == "Linux":
        libc = _prepare_libc()
        libc.pthread_create.restype = ctypes.c_int
        libc.pthread_create.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        libc.pthread_join.restype = ctypes.c_int
        libc.pthread_join.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        spawn_addr = ctypes.cast(libc.pthread_create, ctypes.c_void_p).value
        join_addr = ctypes.cast(libc.pthread_join, ctypes.c_void_p).value
    else:
        kernel32 = _prepare_kernel32()
        kernel32.CreateThread.restype = ctypes.c_void_p
        kernel32.CreateThread.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        spawn_addr = ctypes.cast(kernel32.CreateThread, ctypes.c_void_p).value
        join_addr = ctypes.cast(kernel32.WaitForSingleObject, ctypes.c_void_p).value
    # ctypes' own `c_void_p.value` is typed `int | None` (NULL is representable) -- a resolved
    # OS/libc symbol is never genuinely NULL in practice (the same real-vs-theoretical gap
    # `test_kollan.py`'s own `GetCurrentProcessId` proof already asserts past); fail loudly
    # rather than silently propagate a `None` address into `capability_addresses`.
    if spawn_addr is None or join_addr is None:
        raise UnsupportedPlatform("failed to resolve a real OS thread-create/join address")
    return {"__thread_spawn__": spawn_addr, "__thread_join__": join_addr}


def run_compiled_goal_with_parallel(
    goal: Goal,
    capability_addresses: dict[str, int] | None = None,
    abi: Abi | None = None,
) -> int:
    """G10 -- execute a `Goal` whose body may contain `parallel {}` blocks: real OS-thread
    fork-join, not simulated. Generalizes `run_compiled_fndefs`'s own two-pass placeholder-
    then-patch placement scheme (G1): every `parallel` branch (`extract_parallel_branches`,
    tamil_lang -- pure AST analysis, D58) is placed as its own real, independently-callable
    native routine BEFORE the main goal is compiled, so their real addresses can be handed to
    `compile_goal` via `capability_addresses` -- the SAME "resolved-address-in, bytes-out"
    shape every other capability already has. `__thread_spawn__`/`__thread_join__`
    (`_resolve_thread_primitives`) are added the same way."""
    abi = abi or ("sysv" if platform.system() == "Linux" else "win64")
    base_addresses = dict(capability_addresses or {})
    base_addresses.update(_resolve_thread_primitives())

    branches = extract_parallel_branches(goal.body)
    placeholder_addresses = dict(base_addresses) | dict.fromkeys(branches, 0)
    branch_lengths = {
        name: len(compile_fndef(fndef, "x86_64", placeholder_addresses, abi))
        for name, fndef in branches.items()
    }

    real_addresses: dict[str, int] = dict(base_addresses)
    allocated: list[tuple[int, int]] = []
    try:
        for name, size in branch_lengths.items():
            addr = _alloc_exec_region(size)
            allocated.append((addr, size))
            real_addresses[name] = addr

        for name, fndef in branches.items():
            final_code = compile_fndef(fndef, "x86_64", real_addresses, abi)
            if len(final_code) != branch_lengths[name]:
                raise RuntimeError(
                    f"parallel branch {name!r} compiled to a different length with real "
                    f"addresses ({len(final_code)}) than with placeholders "
                    f"({branch_lengths[name]}) -- the fixed-width-immediate invariant this "
                    "scheme depends on doesn't hold"
                )
            addr = real_addresses[name]
            ctypes.memmove(addr, final_code, len(final_code))
            _finalize_exec_region(addr, branch_lengths[name])

        main_code = compile_goal(goal, "x86_64", real_addresses, abi=abi)
        return run_compiled_goal(main_code)
    finally:
        for addr, size in allocated:
            _free_exec_region(addr, size)


def run_uint64_result(code: bytes) -> int:
    """Execute a 0-arg, leaf x86-64 stencil that returns a full 64-bit value in RAX -- T8.15's
    `emit_load_immediate64`/`emit_shift_right_32` round-trip proof, generically reusable for any
    future leaf stencil with this same shape."""
    return _run_x86_64(code, _UINT64_FN)


def place_executable(code: bytes) -> int:
    """G5 -- place `code` into a real, permanent executable region and return its address,
    WITHOUT executing or freeing it (unlike every other `run_*` helper here, which allocate,
    run, and free in one shot) -- the shared piece `run_compiled_fndefs`'s own two-pass scheme
    doesn't need: a real function that OTHER compiled code will `call` by address LATER (G5's
    `push`/`mapset` calling the unchanged `emit_alloc` stencil as a real subroutine from within
    a bigger compiled goal). The caller owns the returned address's lifetime -- same "lives as
    long as the allocator/process needs it" contract `BumpAllocator`'s own `ctypes` buffers
    already have; nothing here ever calls `_free_exec_region`."""
    addr = _alloc_exec_region(len(code))
    ctypes.memmove(addr, code, len(code))
    _finalize_exec_region(addr, len(code))
    return addr


__all__ = [
    "UnsupportedPlatform",
    "place_executable",
    "run_alloc",
    "run_branch",
    "run_branch_on_compare",
    "run_capability_call",
    "run_compiled_fndefs",
    "run_compiled_goal",
    "run_compiled_goal_with_parallel",
    "run_framed_call_with_unwind",
    "run_govern_check",
    "run_python_call_raw",
    "run_symbol_roundtrip",
    "run_uint64_result",
]
