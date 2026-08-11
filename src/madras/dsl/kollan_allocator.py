"""kollan_allocator.py -- T8.12: a bump/arena allocator as the first real capability behind
Kollan's `memory-ref` kernel node.

Kollan's local variables today are only fixed-size stack slots (`emit_store_local`/
`emit_load_local`) -- there is no way for compiled `.tamil` code to acquire a runtime-sized block
of memory at all, which is exactly what blocks real arrays (`_compile_loop`, T8.10, explicitly
flags this as undesigned). This is the first allocator: a bump/arena strategy -- Zig's explicit,
caller-visible `Allocator` model, not Rust's compile-time ownership or Swift's ARC. It matches
Kollan's own "no hidden runtime machinery" shape (no GC pause, no borrow-checker, no refcounting)
and is the simplest real strategy that unblocks arrays.

`memory-ref` (D50/D60) stays the frozen kernel node -- allocation itself is not a new kernel
node. `BumpAllocator` is one swappable *implementation* behind it; a developer could later swap
in a different allocator capability (fixed-buffer, pooled) without any kernel change.
"""

from __future__ import annotations

import ctypes

from tamil_lang.kollan import emit_alloc

from madras.dsl.kollan import run_alloc


class ArenaExhausted(RuntimeError):
    """Raised when an allocation would run past the arena's own fixed capacity. A bump allocator
    never grows or reclaims mid-arena by design -- the caller resets or replaces it instead."""


class BumpAllocator:
    """A real, fixed-capacity bump arena backing Kollan's `memory-ref` allocations. The compiled
    native stencil (`emit_alloc`) reads/writes the offset cell directly by its real address, so
    both the arena buffer and the offset cell are held as `ctypes` objects (stable, non-GC-moved
    addresses) for this allocator's whole lifetime, not plain Python values."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError(f"BumpAllocator capacity must be positive, got {capacity}")
        self._capacity = capacity
        self._buffer = ctypes.create_string_buffer(capacity)
        self._offset_cell = ctypes.c_uint64(0)
        self._base_addr = ctypes.addressof(self._buffer)
        self._offset_addr = ctypes.addressof(self._offset_cell)

    def alloc(self, size: int) -> int:
        """Bump-allocate `size` bytes, returning the real address of the block -- computed by
        actual executed machine code, not Python arithmetic. Raises `ArenaExhausted` if this
        would exceed the arena's fixed capacity, checked in Python BEFORE running the stencil
        (the native code itself has no bounds check -- skipping that per-allocation cost is a
        bump allocator's whole point)."""
        if size <= 0:
            raise ValueError(f"alloc size must be positive, got {size}")
        current_offset = self._offset_cell.value
        if current_offset + size > self._capacity:
            raise ArenaExhausted(
                f"allocating {size} bytes at offset {current_offset} would exceed this "
                f"arena's {self._capacity}-byte capacity"
            )
        code = emit_alloc("x86_64", self._base_addr, self._offset_addr)
        return run_alloc(code, size)

    def reset(self) -> None:
        """Rewind the bump pointer to zero -- the arena-reset semantics that make a bump
        allocator usable at all without ever freeing individual blocks."""
        self._offset_cell.value = 0

    def open_tier(self) -> int:
        """G6 (plan-local D70) -- Petti's own v0 tier/region boundary: a checkpoint of the
        arena's current bump offset. Region-based reclaim (the ML-family/Cyclone region-
        inference precedent, generalized -- not full PagedAttention paging yet, that's later
        real future work): a scope's own allocations all land after this checkpoint; closing
        the tier later (`close_tier`), if nothing allocated since is still reachable from
        outside the scope, rewinds back here -- a REAL bulk free, the same live offset cell
        every `alloc()` call already bumps, not simulated."""
        return self._offset_cell.value

    def close_tier(self, checkpoint: int) -> None:
        """G6 -- reclaim a tier opened by `open_tier()`: rewind the bump pointer back to
        `checkpoint`, making everything allocated since reusable in one step.

        **Scope, corrected s57:** this is an ARENA-level free, not a PHYSICAL one -- it rewinds
        the offset cell so the space can be re-bumped; it does not return pages to the OS. That
        distinction is invisible here by construction (this arena is a fixed ctypes buffer whose
        pages were committed the moment it was created, so there is nothing to hand back), but it
        is NOT invisible in the standalone runtime, whose arena is a large lazy `mmap`: there,
        rewinding alone leaves every touched page resident forever. Physical reclaim is
        `madras.dsl.kollan_standalone.emit_arena_contraction` (a real `madvise(MADV_DONTNEED)`,
        live-measured returning 100% of peak). An earlier version of this docstring called the
        rewind "a real bulk deallocation", which overstated it -- found by measuring, not reading.

        The CALLER owns
        proving nothing allocated since escaped this scope -- Petti itself does no move-
        tracking (`tamil_lang.kollan.check_moves` + a conservative escape check decide this,
        kept as a SEPARATE concern, the same "bounds-check-in-Python-not-the-stencil" split
        `alloc()`'s own `ArenaExhausted` check already draws)."""
        if checkpoint > self._offset_cell.value:
            raise ValueError(
                f"close_tier checkpoint {checkpoint} is ahead of the current offset "
                f"{self._offset_cell.value} -- tiers must close in the same LIFO order they "
                "opened in, matching lexical scope nesting"
            )
        self._offset_cell.value = checkpoint

    @property
    def bytes_used(self) -> int:
        return self._offset_cell.value

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def base_addr(self) -> int:
        """G5 -- the arena's own real base address, exposed so a caller can emit ANOTHER
        `emit_alloc` stencil sharing this SAME arena (`kollan_collections.py`'s shared
        `__collection_alloc__`, called from WITHIN compiled `.tamil` code, not from Python) --
        both allocation paths bump the SAME live offset cell, so they never double-book memory."""
        return self._base_addr

    @property
    def offset_addr(self) -> int:
        """G5 -- the arena's own live offset cell's real address, the other half `base_addr`'s
        docstring explains."""
        return self._offset_addr


__all__ = ["ArenaExhausted", "BumpAllocator"]
