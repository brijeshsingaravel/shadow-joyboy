"""kollan_collections.py -- G5: materializes a goal's/fn's list/map literals into real, chunked
arena memory, the closed-tree half of collections (mirrors `kollan_records.py`/`kollan_strings.py`
in shape, but list/map are the first MUTABLE values -- `push`/`mapset` grow them with a REAL
runtime allocation, not just a compile-time declaration).

A list chunk is `[value:8][next:8]` (16 bytes); a map chunk is `[key:8][value:8][next:8]`
(24 bytes) -- both a real, arena-backed, singly-linked chain (research: outlier candidate #1,
Jai `Bucket_Array`/Odin arena-mode dynamic arrays -- growth never copies or moves an existing
chunk, only links a new one on). Each literal's elements/fields are inserted in REVERSE
declaration order, so after materialization the FIRST-declared element/key ends up as the chain's
HEAD (hop 0) -- `Push`/`MapSet` (real runtime growth, always prepending the newest onto the
existing head) then naturally continue the SAME "most-recently-added is hop 0" contract, so a
`ListLiteral`+`push` mix and a `MapLiteral`+later `mapset` behave consistently, not just the
literal-only case.

`tamil_lang.kollan.compile_goal`/`compile_fndef` never allocate (D58); this module allocates the
initial chain AND places the one shared `__collection_alloc__` stencil (the UNCHANGED `emit_alloc`
from T8.12, reused wholesale) into real executable memory -- `push`/`mapset`'s compiled code calls
it BY ADDRESS, through the SAME `capability_addresses` map every other capability call resolves
through, zero new plumbing for the call target itself.
"""

from __future__ import annotations

import ctypes

from tamil_lang.ast import FnDef, Goal
from tamil_lang.kollan import Abi, emit_alloc
from tamil_lang.nadi import lower_to_nadi, nadi_lists, nadi_maps

from madras.dsl.kollan import place_executable
from madras.dsl.kollan_allocator import BumpAllocator

_LIST_CHUNK_SIZE = 16  # [value:8][next:8]
_MAP_CHUNK_SIZE = 24  # [key:8][value:8][next:8]
_EMPTY = 0  # the sentinel "no chunks yet" head/tail address


def _write_chunk(base: int, words: list[int]) -> None:
    for i, word in enumerate(words):
        ctypes.cast(base + i * 8, ctypes.POINTER(ctypes.c_int64))[0] = word


def materialize_collections(
    program: Goal | FnDef,
    allocator: BumpAllocator,
    abi: Abi = "win64",
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """Allocate + populate every list/map `program` (a `Goal` or `FnDef`) declares, all chunks
    living in `allocator`'s SAME arena real `push`/`mapset` code will also grow into at runtime.
    Returns `(list_addresses, map_addresses, capability_addresses)` -- the first two feed
    `compile_goal`/`compile_fndef`'s `list_addresses`/`map_addresses` parameters directly; the
    third holds `{"__collection_alloc__": <real address>}`, merged into whatever
    `capability_addresses` the caller already has (a plain dict update -- this reserved name is
    never a real user-facing capability name, so there's no real collision risk)."""
    list_addresses: dict[str, int] = {}
    for name, elements in nadi_lists(lower_to_nadi(program)).items():
        head = _EMPTY
        for value in reversed(elements):
            chunk = allocator.alloc(_LIST_CHUNK_SIZE)
            _write_chunk(chunk, [value, head])
            head = chunk
        list_addresses[name] = head

    map_addresses: dict[str, int] = {}
    for name, fields in nadi_maps(lower_to_nadi(program)).items():
        head = _EMPTY
        # A `MapLiteral`'s keys are compile-time NAME identifiers (same "position is the
        # address" shape `RecordLiteral`'s field names already have -- `FieldAccess`-on-a-map
        # resolves a key to a hop count entirely at compile time, never comparing a runtime key
        # value). The chunk's key WORD is written as a placeholder (0) -- there's nothing
        # meaningful to store there for a compile-time-resolved read; a real runtime key value
        # only exists for a later `mapset` (an honest, disclosed asymmetry: `MapSet`'s key is a
        # genuine runtime int literal, part of what makes it real runtime growth, not a
        # compile-time declaration).
        for value in reversed(list(fields.values())):
            chunk = allocator.alloc(_MAP_CHUNK_SIZE)
            _write_chunk(chunk, [0, value, head])
            head = chunk
        map_addresses[name] = head

    alloc_code = emit_alloc("x86_64", allocator.base_addr, allocator.offset_addr, abi)
    capability_addresses = {"__collection_alloc__": place_executable(alloc_code)}

    return list_addresses, map_addresses, capability_addresses


__all__ = ["materialize_collections"]
