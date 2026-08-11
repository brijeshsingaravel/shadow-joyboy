"""kollan_standalone.py -- Phase K1: Core made real, standalone.

Builds a Linux ELF whose compiled `.tamil` code allocates its OWN memory, with no host process
underneath it. Hosted execution (`kollan_collections.materialize_collections`) hands the compiled
code an arena that Python owns (`BumpAllocator`, a ctypes buffer) and places the allocator stencil
with `place_executable`. Neither exists in a standalone binary, so this module supplies both from
inside the program itself:

  * the ARENA comes from a raw `mmap` syscall (G2/D72's `emit_syscall`) in the program's own
    prologue -- `MAP_FIXED` at a build-time-chosen address, because `emit_alloc` bakes its arena
    base in as an IMMEDIATE and a runtime-chosen address could not be baked. This is why the
    T8.12 stencil needs ZERO changes to work standalone.
  * the BUMP CELL lives in the arena's first page. `MAP_ANONYMOUS` memory is kernel-zeroed, so
    the offset cell self-initialises to 0 -- no explicit zeroing code needed.
  * the ALLOCATOR is placed in the binary itself (after the goal body) and resolved through the
    SAME `capability_addresses["__collection_alloc__"]` entry hosted code already uses, via the
    two-pass measure-then-patch scheme G1's `run_compiled_fndefs` established.

Deliberate: the arena is a LARGE, LAZY VIRTUAL reservation, not one sized to `V_max`. The K1
dimensional-tiering experiment (s56) showed a *virtual* ceiling breaks demand-paging (it refuses
the big-virtual/small-resident mapping the compute-frugal thesis depends on), while a *physical*
ceiling does not. So the resident set -- not the reservation -- is what `V_max` bounds, enforced
by the VM's physical RAM (QEMU `-m`), exactly as the experiment prescribed.
"""

from __future__ import annotations

import struct

from tamil_lang.ast import Goal
from tamil_lang.kollan import compile_goal, emit_alloc
from tamil_lang.kollan.x86_64 import emit_syscall

from madras.dsl.kollan_elf import build_elf64_executable

_SYS_MMAP = 9
_SYS_EXIT = 60
_SYS_MADVISE = 28
_MADV_DONTNEED = 4
# Contraction only needs to span what was actually touched; a bounded range also keeps the
# kernel's page-table walk cheap (madvise over the whole 64 GiB reservation would be busywork).
DEFAULT_CONTRACTION_BYTES = 2 * 1024**3
_PROT_READ_WRITE = 0x1 | 0x2
# MAP_NORESERVE is load-bearing, not decoration: without it Linux's heuristic overcommit REFUSES
# a reservation this much larger than physical RAM (live-caught -- the 64 GiB arena was granted on
# an 8.7 GB host but refused inside a 2 GB VM, and the unchecked failure became a SIGSEGV).
_MAP_PRIVATE_ANON_FIXED = 0x02 | 0x20 | 0x10 | 0x4000
# Exit code used when the arena reservation itself fails -- a clean, diagnosable signal instead of
# a wild pointer.
ARENA_MMAP_FAILED_EXIT = 9

# 4 GiB -- clear of the ELF image (0x400000) and well below the usual stack region.
ARENA_ADDR = 0x1_0000_0000
# 64 GiB of *virtual* reservation; costs no physical RAM until pages are touched.
ARENA_VIRTUAL_BYTES = 64 * 1024**3
_OFFSET_CELL_ADDR = ARENA_ADDR
_ARENA_BASE_ADDR = ARENA_ADDR + 4096

# Must match kollan_elf.build_elf64_executable's own layout (ehdr 64 + phdr 56 at 0x400000).
_ENTRY = 0x400000 + 64 + 56


class StandaloneBuildError(RuntimeError):
    pass


def _exit_with_result() -> bytes:
    """`mov edi, eax` (the goal's result becomes the exit code) then `exit(edi)`."""
    return b"\x89\xc7" + b"\xb8" + struct.pack("<I", _SYS_EXIT) + b"\x0f\x05"


def _mmap_arena() -> bytes:
    """Reserve the arena, then VERIFY it. `MAP_FIXED` means success returns exactly `ARENA_ADDR`,
    so a single compare catches every failure mode. Without this check a refused reservation
    returns `-errno` in RAX and the first bump-allocated write dereferences it -- which is
    precisely the SIGSEGV this row hit live before the check existed."""
    reserve = emit_syscall(
        _SYS_MMAP,
        ARENA_ADDR,
        ARENA_VIRTUAL_BYTES,
        _PROT_READ_WRITE,
        _MAP_PRIVATE_ANON_FIXED,
        -1,
        0,
    )[:-1]  # strip the stencil's trailing `ret` -- used here as an inline fragment
    on_failure = (
        b"\xbf"
        + struct.pack("<I", ARENA_MMAP_FAILED_EXIT)  # mov edi, 9
        + b"\xb8"
        + struct.pack("<I", _SYS_EXIT)  # mov eax, 60
        + b"\x0f\x05"  # syscall
    )
    check = (
        b"\x48\xba"
        + struct.pack("<Q", ARENA_ADDR)  # mov rdx, ARENA_ADDR
        + b"\x48\x39\xd0"  # cmp rax, rdx
        + b"\x74"
        + struct.pack("<B", len(on_failure))  # je +len(on_failure)
    )
    return reserve + check + on_failure


def emit_arena_contraction(release_bytes: int = DEFAULT_CONTRACTION_BYTES) -> bytes:
    """The elastic box's CONTRACTION phase (RFC-0002 §5.2), made physically real: a
    `madvise(MADV_DONTNEED)` over the arena that genuinely returns pages to the OS.

    This exists because rewinding a bump pointer is NOT a physical free. Petti's
    `BumpAllocator.close_tier` (G6) rewinds the offset cell, which makes the space reusable —
    honest for the hosted ctypes arena, where the pages were always committed anyway. But the
    standalone arena is a large LAZY mmap: rewinding it returns nothing, and the touched pages
    stay resident forever. Only `madvise` drops them, live-measured at 457.6 MB → 0.0 MB
    (100% of peak) via `/proc/<pid>/statm` (s57, minimal A).

    Preserves RAX itself (`push`/`pop`) so the caller's result survives — a syscall clobbers
    RAX, and making that the caller's problem is a footgun this function refuses to hand out."""
    if release_bytes <= 0:
        raise ValueError(f"release_bytes must be positive, got {release_bytes}")
    if release_bytes > ARENA_VIRTUAL_BYTES:
        raise ValueError(
            f"release_bytes {release_bytes} exceeds the arena's own reservation "
            f"{ARENA_VIRTUAL_BYTES}"
        )
    madvise = emit_syscall(_SYS_MADVISE, ARENA_ADDR, release_bytes, _MADV_DONTNEED)[:-1]
    return b"\x50" + madvise + b"\x58"  # push rax / madvise / pop rax


def build_standalone_elf(
    goal: Goal,
    capability_addresses: dict[str, int] | None = None,
    list_addresses: dict[str, int] | None = None,
    map_addresses: dict[str, int] | None = None,
    pre_exit: bytes = b"",
) -> bytes:
    """Compile `goal` into a standalone Linux ELF that mmaps its own arena and bump-allocates
    inside it. The goal's result lands in EAX and becomes the process exit code.

    `pre_exit` injects raw fragments between the goal body and the exit sequence -- used by the
    elastic-box probe to emit a real `madvise(MADV_DONTNEED)` contraction after dilation. The
    caller owns preserving EAX across those fragments (a syscall clobbers RAX), e.g. by wrapping
    them in `push rax` / `pop rax`."""
    prologue = _mmap_arena()
    allocator = emit_alloc("x86_64", _ARENA_BASE_ADDR, _OFFSET_CELL_ADDR, "sysv")
    tail = _exit_with_result()

    def _compile(alloc_addr: int) -> bytes:
        caps = dict(capability_addresses or {})
        caps["__collection_alloc__"] = alloc_addr
        body = compile_goal(
            goal,
            "x86_64",
            caps,
            list_addresses=list_addresses,
            map_addresses=map_addresses,
            abi="sysv",
        )
        if not body.endswith(b"\xc3"):
            raise StandaloneBuildError("compiled goal did not end in `ret` as expected")
        return body[:-1]  # inline fragment: the exit sequence replaces the return

    # Two-pass placement (G1's `run_compiled_fndefs` scheme): measure with a placeholder, then
    # rebuild at the real address. The address is baked as a 64-bit immediate, so the body's
    # LENGTH is invariant between passes -- asserted, never assumed.
    probe = _compile(0)
    alloc_addr = _ENTRY + len(prologue) + len(probe) + len(pre_exit) + len(tail)
    body = _compile(alloc_addr)
    if len(body) != len(probe):
        raise StandaloneBuildError(
            f"two-pass placement is unstable: probe {len(probe)}B vs real {len(body)}B"
        )

    return build_elf64_executable(prologue + body + pre_exit + tail + allocator)


__all__ = [
    "ARENA_ADDR",
    "ARENA_MMAP_FAILED_EXIT",
    "ARENA_VIRTUAL_BYTES",
    "DEFAULT_CONTRACTION_BYTES",
    "StandaloneBuildError",
    "build_standalone_elf",
    "emit_arena_contraction",
]
