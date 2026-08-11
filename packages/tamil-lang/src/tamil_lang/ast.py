"""The Kural AST — the .tamil kernel node types (RFC-0002 §3, D11).

The full base-6 kernel: the base-4 (universal, Turing-complete) `capability-call`, `control-flow`,
`memory-ref`, `compose/bind` + the governed-AI-2 (moat) `goal`, `governance-check`. New AI-native
primitives enter later via the open "X" slot (the Capability Catalog), never by growing this file.

Every node is a Pydantic model with `extra="forbid"` — a malformed program fails at parse time,
not at runtime: "governed by construction", applied to the language itself.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Mugavari (RFC-0002 §4.2) -- the node's address: parent-prefix · Morton(x,y,z) ·
    # content-hash. None until `mugavari.assign_ids()` walks the tree post-parse (depth/order
    # are only knowable once the full tree exists, not during bottom-up parsing).
    mugavari_id: str | None = Field(default=None)


class Recall(_Node):
    """memory-ref (read) — read a key from the memory graph (Ninaivu). A value-position node."""

    kind: Literal["memory-ref"] = "memory-ref"
    op: Literal["read"] = "read"
    key: str


class RangeLiteral(_Node):
    """`range(start, stop)` — a value-position node, additive to `Loop.iterable`'s value space
    (not a new kernel node kind — `control-flow (loop)` already exists; this only grows what an
    existing field can hold, the same "additive, not a kernel change" shape T3.1's `ffi_bridge`
    and the Mugavari `mugavari_id` field both already established). No materialized array is
    implied: `start`/`stop` are evaluated, never allocated — the honest minimum a counting loop
    needs, matching how real compilers (LLVM/GCC/rustc) lower a simple `for i in a..b` to a
    scalar stack counter, not a heap collection."""

    kind: Literal["value"] = "value"
    form: Literal["literal"] = "literal"
    type: Literal["range"] = "range"
    start: str | Recall
    stop: str | Recall


class ArrayLiteral(_Node):
    """`[e1, e2, ...]` — a fixed-length, integer-literal array. A value-position node, additive
    to the Value space (not a new kernel node kind — same "additive, not a kernel change" shape
    as `RangeLiteral`). Declares that an array exists and what its literal elements are; the real
    memory backing it is allocated+populated by the CLOSED tree (`madras.dsl`) BEFORE
    `compile_goal` runs (D58: `compile_goal` itself never allocates) — this node carries zero
    runtime cost on its own, matching how `RangeLiteral`'s bounds are evaluated, never allocated."""

    kind: Literal["value"] = "value"
    form: Literal["literal"] = "literal"
    type: Literal["array"] = "array"
    elements: list[str]


class Project(_Node):
    """value/project — read a part out of a compound value bound earlier in the same goal/fn.

    **ONE node for what used to be five.** `ArrayIndex`, `FieldAccess`, `VerifiedFieldAccess`,
    `ResultOk` and `ResultValue` each took their own top-level `kind` despite carrying
    *structurally identical* fields (a source name + an optional selector) and expressing one
    operation: project a part out of a compound. Merging them applies the SAME two-level naming
    discipline the kernel statements have always used — `kind` frozen, the variation carried in
    a field (five `memory-ref` statements share one `kind` with five `op`s; five `control-flow`
    statements share one `kind` with five `form`s) — to the value space, which was the only part
    of the AST that had skipped it. Precedent (RFC-0002 §8, borrow the mainstream's strongest
    patterns): **Rust MIR**'s `Place` + `ProjectionElem` enum (Field/Index/Deref/Downcast) and
    **LLVM**'s single `getelementptr` for all compound addressing — both unify exactly this way.

    `selector` decides what is read and how `key` is interpreted:

    - `index` -- `name[i]`; `key` is the integer index (a literal, or (N1a) a bound loop
      induction variable, whose compile-time-known range lets the compiler prove the access
      in-bounds and emit a check-free load).
    - `field` -- `name.f`; `key` is the field name (the plain, zero-overhead read).
    - `verified-field` -- `verified name.f` (G3); `key` is the field name. **Bind-only**: it
      returns a PACKED `(match << 32) | value`, which needs a Bind target to hold -- the same
      Bind-only convention `fallible` calls already use.
    - `result-tag` -- `is_ok(b)`; reads the tag out of a packed result (T8.15/T8.16). No `key`.
    - `result-payload` -- `payload(b)`; reads the payload out of a packed result. No `key`.

    v0 boundaries carried over unchanged from the merged nodes: an arbitrary computed/`Recall`-
    bound index still needs a real register allocator (not built), and a `field` read on a map
    resolves by compile-time key→hop-count, not a fixed byte offset."""

    kind: Literal["value"] = "value"
    form: Literal["project"] = "project"
    source: str
    selector: Literal["index", "field", "verified-field", "result-tag", "result-payload"]
    key: str | None = None

    @model_validator(mode="after")
    def _key_matches_selector(self) -> Project:
        """A selector that names a part REQUIRES a key; one that reads a fixed half of a packed
        result takes none. Enforced here so a malformed projection fails at parse time, not at
        codegen — the same "governed by construction, applied to the language itself" contract
        every other node's `extra="forbid"` already gives."""
        needs_key = self.selector in ("index", "field", "verified-field")
        if needs_key and self.key is None:
            raise ValueError(f"selector={self.selector!r} requires a key")
        if not needs_key and self.key is not None:
            raise ValueError(f"selector={self.selector!r} takes no key, got {self.key!r}")
        return self


class StringLiteral(_Node):
    """`"..."` (G4) — a quoted string literal, arena-backed. A value-position node, additive to
    the Value space (not a new kernel node kind — same shape as `ArrayLiteral`/`RecordLiteral`).
    A string is fundamentally a `(pointer, length)` slice (Zig `[]const u8` / Rust `&str`
    precedent, matching Petti's own arena philosophy, D70) -- the byte LENGTH is compile-time-
    known from the literal itself (same "never stored at runtime" treatment `array_lengths`
    already gets); only the real base ADDRESS needs holding, which fits the SAME 8-byte-aligned
    local slot + `emit_load_immediate64`/`emit_store_local64` (T8.15/T8.16) every other 64-bit
    value already uses -- zero new stencils needed for v0's declare-and-hold scope. UTF-8
    encoded (not ASCII-only): a real correctness choice, not decoration, given `.tamil` itself.

    **Breaking change, made deliberately (G4):** before this, a quoted string literal parsed via
    `atom` into a bare `str` -- INDISTINGUISHABLE from a name reference, and already
    non-functional at codegen time (compile_goal tried `int(value)`, failed, then a symbol-table
    lookup, failed, raised `UnsupportedNode`) for any real `.tamil` program. Nothing working is
    lost; the parser output SHAPE changes for what was already a dead end."""

    kind: Literal["value"] = "value"
    form: Literal["literal"] = "literal"
    type: Literal["string"] = "string"
    text: str


class RecordLiteral(_Node):
    """`{ f1 = v1, f2 = v2, ... }` (G3, plan-local D69) — a fixed-shape, integer-literal record.
    A value-position node, additive to the Value space (not a new kernel node kind — same
    "additive, not a kernel change" shape as `ArrayLiteral`, which this deliberately mirrors:
    declares that a record exists and what its literal field values are; the real memory backing
    it is allocated+populated by the CLOSED tree BEFORE `compile_goal` runs (D58), plus a real
    XOR checksum over the fields (`verified field` access recomputes+compares it, G3's
    self-verifying variant — historical precedent: double-entry bookkeeping's built-in
    cross-check, Pacioli 1494 — NOT cryptographic, an honest construction-time integrity check).
    `fields` is an ordered name->literal map: insertion order IS the field's ordinal (its byte
    offset), the same "position is the address" idea `mugavari.py`'s own Morton coding uses."""

    kind: Literal["value"] = "value"
    form: Literal["literal"] = "literal"
    type: Literal["record"] = "record"
    fields: dict[str, str]


class ListLiteral(_Node):
    """`list[e1, e2, ...]` (G5, plan-local D69) -- a literal-initial, arena-backed, CHUNKED list
    (research: outlier candidate #1 -- Jai `Bucket_Array`/Odin arena-mode dynamic arrays, not a
    plain contiguous array). A value-position node, additive to the Value space (same "additive,
    not a kernel change" shape as `ArrayLiteral`/`RecordLiteral`/`StringLiteral`). Unlike those,
    a list is MUTABLE at runtime (`Push` grows it with real allocation, not just a compile-time
    declaration) -- its bound name holds a real, live local slot (the current head chunk's
    address), the same "declared value lives in a local slot" shape `StringLiteral` already
    established, not the "pure compile-time declaration, no runtime slot" shape `ArrayLiteral`/
    `RecordLiteral` use."""

    kind: Literal["value"] = "value"
    form: Literal["literal"] = "literal"
    type: Literal["list"] = "list"
    elements: list[str]


class MapLiteral(_Node):
    """`map{ k1 = v1, k2 = v2, ... }` (G5, plan-local D69) -- a literal-initial, arena-backed,
    chunked key/value map (research: a bump-and-rehash-friendly chained design, the honest v0
    slice of the "Swiss-table-lite" recommendation -- real per-chunk chaining, not yet a real
    hash/probe; see `MapSet`/`FieldAccess`-on-a-map for the v0 boundary this draws). Same
    "additive Value kind, mutable via a real local slot" shape `ListLiteral` establishes; field
    order in the literal is this v0's own compile-time key->hop-count resolution (mirrors
    `RecordLiteral`'s "insertion order is the field's ordinal" contract)."""

    kind: Literal["value"] = "value"
    form: Literal["literal"] = "literal"
    type: Literal["map"] = "map"
    fields: dict[str, str]


class CyclesRead(_Node):
    """`cycles()` (N4) — a NATIVE (not FFI) read of the CPU's own cycle counter (x86-64
    `rdtsc`), the real-work half of N4's eval/observability primitive (Erlang/BEAM research:
    genuine native instrumentation is compile-time-emitted directly at the measurement site,
    not a bolted-on runtime library). A value-position node, additive to the Value space (not a
    new kernel node kind — same shape as `ResultOk`/`RangeLiteral`). Zero fields: `rdtsc` takes
    no operand, needs no `capability_addresses` resolution at all (a single instruction, the
    same "raw, no libc shim" philosophy D72's syscall-FFI already established for G2).

    v0 scope, disclosed: returns only the LOW 32 bits of the 64-bit counter (`rdtsc` places
    them in EAX directly, needing no combine step) — a real, monotonic cycle count for any
    interval under ~1-2 seconds at multi-GHz clock rates (wraps around after that), the honest
    minimum for measuring a single capability call's duration, reusing G11's EXISTING 32-bit
    `derive`/`Compute` subtraction machinery unchanged (zero new arithmetic stencils needed) --
    matching this codebase's own established "honest minimum boundary" pattern (G1's 0-arg-only
    fns, G8's register-only params). A full 64-bit wall-clock timestamp (needing frequency
    calibration via `CPUID` to convert cycles to real time) is real, separate future work."""

    kind: Literal["value"] = "value"
    form: Literal["intrinsic"] = "intrinsic"


class Compute(_Node):
    """`left <op> right` (G11, plan-local D69) — a single arithmetic binary operation over two
    already-bound names or integer literals. A value-position node, additive to the Value space
    (not a new kernel node kind — same shape as `ResultOk`/`FieldAccess`). `left`/`right` reuse
    `expr.py`'s existing `Name`/`Num` parsing (never wired into codegen before this row); v0
    scope draws the SAME narrow boundary `lower_branch`'s own `Compare(Name, Num)`-only shape
    already established, just widened one notch further (research/founder sign-off, s56):
    exactly one operator, both operands each a bound name OR a literal (never a nested
    sub-expression) — `x + 1`, `x + y`, not `x + y * 2`. This is `Derive`'s own real value: a
    `derive` statement LOWERS to `Remember(key, Compute(...))` at compile time, giving it
    genuinely new codegen (nothing before this row ever evaluated an arithmetic expression into
    a stored value) while staying inside the Value space every other statement already reads
    through."""

    kind: Literal["value"] = "value"
    form: Literal["compute"] = "compute"
    left: str
    op: Literal["+", "-", "*", "/"]
    right: str


# A value is a literal/name atom (str), a memory read (Recall), a range literal, an array
# literal, an array index read, a record literal, a field read, a Result-as-value tag/payload
# read, or a computed arithmetic value. `VerifiedFieldAccess` is deliberately NOT here -- like
# `fallible`/`ffi_call`, it's a Bind-RHS-only shape, not a plain value.
Value = (
    str
    | Recall
    | Project
    | RangeLiteral
    | ArrayLiteral
    | RecordLiteral
    | StringLiteral
    | ListLiteral
    | MapLiteral
    | Compute
    | CyclesRead
)


class Call(_Node):
    """capability-call — invoke a capability/faculty/tool (the universal 'do').

    `ffi_bridge` (D10, T3.1) and `fallible` (T8.16) are capability *kinds* flowing through this
    node, not new kernel primitives: `capability_kind="ffi_bridge"` + `lang` name the foreign host
    (Python only this pass); `capability_kind="fallible"` means the target returns a packed
    `(tag, value)` result (T8.15's convention) instead of a plain scalar — Result-as-value error
    handling (Rust/Go precedent), not real stack-unwinding. Declaring/parsing a call is open (this
    package); its governed *execution* (rank/scope check, sandboxing) is a Compiler/Agent-OS
    concern in the closed tree.
    """

    kind: Literal["capability-call"] = "capability-call"
    capability_kind: Literal["native", "ffi_bridge", "fallible"] = "native"
    lang: str | None = None
    name: str
    args: list[Value] = []


class Govern(_Node):
    """governance-check — **Aram** (அறம், "right action toward others") on a node.

    Deny-by-default is the MECHANISM, not the meaning. An agent does not act on another's behalf
    without explicit warrant, so a node with no governance is refused rather than assumed safe —
    the refusal follows from the principle, it is not the principle. Describing this only as a
    "gate" says what it blocks and never what it is for, which matters because the word we use
    here is the word a contributor builds toward.
    """

    kind: Literal["governance-check"] = "governance-check"
    check: str  # e.g. "rank >= 2"


class Bind(_Node):
    """compose/bind — bind a capability-call's output to a name (dataflow between nodes).

    `cached` (T8.16... T8.17) is an additive field, not a new kernel node — same "grow the
    existing node's own fields" shape `Call.capability_kind` already established. A cached Bind
    is memoized by its own `mugavari_id` (a stable, per-call-site address, T3's own Morton-coded
    node addressing): the real call only runs once per distinct call site; every later
    compile/execution of the SAME site reuses the cached result. v0 scope: only a plain (`native`)
    call may be cached — composing with `fallible`/`ffi_bridge` needs its own real storage-shape
    design, not attempted here.
    """

    kind: Literal["compose-bind"] = "compose-bind"
    target: str
    call: Call | Project
    cached: bool = False


class Remember(_Node):
    """memory-ref (write) — write a value into the memory graph (Ninaivu)."""

    kind: Literal["memory-ref"] = "memory-ref"
    op: Literal["write"] = "write"
    key: str
    value: Value


class Branch(_Node):
    """control-flow (branch) — conditional execution."""

    kind: Literal["control-flow"] = "control-flow"
    form: Literal["branch"] = "branch"
    condition: str  # e.g. "count > 0"
    then: list[Statement] = []
    otherwise: list[Statement] = []


class Loop(_Node):
    """control-flow (loop) — iterate a body over an iterable."""

    kind: Literal["control-flow"] = "control-flow"
    form: Literal["loop"] = "loop"
    var: str
    iterable: Value
    body: list[Statement] = []


class Return(_Node):
    """control-flow (return) — additive to `control-flow`'s existing `form` field (D50/D60: the
    same "grow an existing field, not a new kernel node" shape `Branch`/`Loop` already
    established, plan-local D69/G1). Exits the enclosing `fn` with a value. v0 scope (G1):
    permitted only as the LAST statement of whatever block it's in (the fn's own top-level
    body, or a `Branch`'s `then`/`otherwise`) — an honest, stated boundary matching this
    codebase's own "name <op> literal only" / "0-arg calls only" scope-honesty convention;
    arbitrary mid-block position needs a general tail-length-threading scheme through every
    nested compiler, not attempted here."""

    kind: Literal["control-flow"] = "control-flow"
    form: Literal["return"] = "return"
    value: Value


class MatchArm(_Node):
    """One arm of a `match` (G9, plan-local D69) — not itself a top-level `Statement`, a
    sub-structure of `Match`. `pattern` is one of `"ok"`/`"err"` (Result matching, over a
    `fallible`/`verified` Bind's packed tag — research: Erlang/Elixir's tagged-tuple matching
    with zero formal enum declaration maps directly onto `.tamil`'s own packed-tag Result
    convention, GPL-LLM-OSS Radar s56), an integer literal string (literal matching, over a
    bound int name), or `"_"` (the wildcard/else arm literal matching REQUIRES for totality — an
    honest substitute for real exhaustiveness checking, since there's no closed sum-type domain
    to check against). `bind` (Result matching only) names the local the arm's body can read the
    payload through, auto-bound the SAME way `payload(name)` already reads it — `None` for
    literal matching (no payload to bind). `guard` (optional) is a flat `name <op> literal`
    condition string, the SAME shape `Branch.condition` already has (reused verbatim, not a new
    expression grammar) — a matched arm whose guard is false falls through to the NEXT arm,
    not to `_`/the whole match's failure."""

    pattern: str
    bind: str | None = None
    guard: str | None = None
    body: list[Statement] = []


class Match(_Node):
    """`match name { ... }` (G9, plan-local D69) — additive to `control-flow`'s existing `form`
    field (D50/D60: the SAME "grow an existing field, not a new kernel node" shape `Branch`/
    `Loop`/`Return` already established). Lowers to a chain of nested `Branch` nodes at COMPILE
    time (`_compile_match`, not parse time — `Match` stays a real, inspectable AST node, useful
    for future tooling/self-hosting/exhaustiveness checking), reusing `_compile_branch`'s
    existing recursive compare-and-jump codegen wholesale — zero new stencils. `scrutinee` is a
    name already bound earlier in this same goal/fn (the SAME "must be bound earlier" rule every
    other control-flow condition already has); whether it's Result matching or literal matching
    is resolved at compile time by checking `fallible_binds` membership (the SAME set `is_ok`/
    `payload` already validate against), not a separate flag on this node.

    v0 scope: `match` is a MID-BODY construct only, the SAME boundary `Loop` already has — an
    arm ending in `return` (as the fn's own trailing statement) isn't supported yet (extending
    `compile_fndef`'s tail-position Return logic, currently Branch-only, to a match's N arms is
    real future work, not attempted here). The working v0 pattern: an arm writes its result via
    `remember`, and a real top-level `return` follows the whole `match` — proven live."""

    kind: Literal["control-flow"] = "control-flow"
    form: Literal["match"] = "match"
    scrutinee: str
    arms: list[MatchArm] = []


class Push(_Node):
    """`push name value` (G5, plan-local D69) -- additive `op` value on the frozen `memory-ref`
    kernel node (D50/D60: same "grow the existing field, not a new kernel node" shape `Return`
    already established for `control-flow`'s `form`). A REAL runtime allocation + chunk-link
    (`emit_alloc` called from within the compiled goal itself, T8.12 reused wholesale) -- unlike
    `Remember`'s `op="write"`, which only ever stores into an already-sized local slot, `push`
    grows a `ListLiteral`-bound name by one chunk each call. v0 scope: the pushed `value` must be
    an integer literal (same "literal, not computed" boundary `ArrayIndex`'s own index already
    draws)."""

    kind: Literal["memory-ref"] = "memory-ref"
    op: Literal["push"] = "push"
    list_name: str
    value: str


class MapSet(_Node):
    """`mapset name key value` (G5, plan-local D69) -- `memory-ref`'s `push` sibling for a
    `MapLiteral`-bound name: same real runtime allocation + chunk-link, a `[key, value, next]`
    chunk instead of `push`'s `[value, next]`. v0 scope: both `key` and `value` must be integer
    literals (same boundary `Push.value` draws) -- `FieldAccess`-on-a-map (this v0's `MapGet`)
    only resolves keys inserted by a `MapLiteral` at construction time, not by a later `mapset`;
    an honest, disclosed gap (matches G1's "0-arg only"/G3's "plain vs verified" narrower-read
    boundaries), not a hidden one -- `mapset` still proves the real runtime-growth machinery."""

    kind: Literal["memory-ref"] = "memory-ref"
    op: Literal["map-set"] = "map-set"
    map_name: str
    key: str
    value: str


class Parallel(_Node):
    """`parallel { stmt1 stmt2 ... }` (G10, plan-local D69) -- additive to `control-flow`'s
    existing `form` field (D50/D60: the SAME "grow an existing field, not a new kernel node"
    shape `Branch`/`Loop`/`Return`/`Match` already established). Research (GPL-LLM-OSS Radar,
    s56): mainstream `async`/`await` (Rust/Zig/Python) is exactly the "function coloring"
    RFC-0002 §5.7 rejects; Go's goroutines+channels and Erlang/BEAM actors both need a
    persistent scheduler/runtime this project doesn't have. The outlier synthesis this lowers
    to: Cilk's fork-join (spawn/sync over real OS threads, no persistent work-stealing pool
    needed -- fits Kollan's own "compile one goal, run it" shape) as the EXECUTION mechanism +
    Bevy/Unity DOTS's ECS scheduler (systems declare read/write access, the scheduler
    auto-derives what's independent and parallelizes it -- NO explicit spawn call from the
    programmer) as the SCHEDULING philosophy + Pony's reference-capability compile-time
    race-freedom (data-race freedom proven by the type/scope system, not a runtime lock) as the
    SAFETY guarantee. Concretely: each top-level statement in `body` is its own unit of
    concurrency -- no `spawn` keyword, the compiler decides these N statements run
    concurrently, not the programmer.

    v0 scope, disclosed: EVERY branch (each top-level statement) must be fully SELF-CONTAINED
    -- it may reference only names it binds itself; touching any name bound outside the
    `parallel` block (by an enclosing `remember`/`bind`, or by a sibling branch) raises
    `UnsupportedNode` at compile time. This is proven for free, not by a bespoke free-variable
    walker: each branch is compiled as its own independent, closed-tree 0-arg `FnDef` (a FRESH
    symbol table with no outer context at all) -- a name that isn't bound within the branch
    simply fails to resolve, the exact same "must be bound earlier" enforcement every other
    control-flow form already has, now doubling as the race-freedom proof (Pony's own move,
    gotten here from machinery that already existed). A branch's own `remember`s are legitimate
    scratch space for a multi-step computation; they just can't escape the block (no results
    flow back out to the surrounding code in v0 -- real future work, needs a captured-frame-
    pointer addressing mode `compile_fndef`'s locals don't have yet). `parallel` is mid-body
    only (the same boundary `Loop`/`Match` already have) and may NOT nest (a `parallel` inside
    another `parallel`'s branch is out of v0 scope). Lowers to real OS-thread fork-join at
    EXECUTION time (`madras.dsl.kollan`, closed tree, D58): Windows `CreateThread`/
    `WaitForSingleObject`, Linux `pthread_create`/`pthread_join` -- both resolved-by-address the
    SAME way any other OS/libc capability already is (G2/G8's own precedent), zero new
    x86-64 stencils (reuses `emit_call_with_args`/`emit_lea_local` wholesale)."""

    kind: Literal["control-flow"] = "control-flow"
    form: Literal["parallel"] = "parallel"
    body: list[Statement] = []


class Derive(_Node):
    """`derive y = x + 1` (G11, plan-local D69) -- additive `op` on the frozen `memory-ref`
    kernel node (D50/D60: same "grow the existing field, not a new kernel node" shape `Push`/
    `MapSet` already established for `memory-ref`'s own `op` field). Research (deep dive,
    founder-directed "what outlier methods people use today"): the formal taxonomy from *Build
    Systems à la carte* splits every incremental/reactive system into two independent axes --
    SCHEDULER (topological/static [Make] vs restarting/dynamic [Excel] vs suspending/dynamic
    [Bazel/Buck2]) and REBUILDER (dirty-bit [Excel/Svelte] vs verifying-trace-with-early-cutoff
    [Salsa, rust-analyzer's own incremental-computation framework; Buck2's single incremental
    graph]). Kollan compiles ONCE into a straight-line instruction stream with no persistent
    process/scheduler/lazy-eval runtime, which forces a topological/STATIC scheduler -- the only
    one of the three buildable without inventing a runtime this row. Founder-chosen rebuilder:
    dirty-bit (Svelte's own actual production model), not Salsa/Bazel's early-cutoff (real added
    complexity -- a memoization slot + revalidation logic -- not needed for a v0).

    Concretely, Svelte's OWN move: `derive` is not a runtime signal/observer at all -- it lowers
    ENTIRELY at COMPILE time (`_lower_derives`, mirroring `_lower_result_arms`/`_lower_literal_
    arms`'s "reuse existing nodes, zero new stencils" pattern G9 already established) into a
    `Remember(key, Compute(left, op, right))` at its own declaration point, PLUS a literal COPY
    of that same statement re-emitted immediately after every later statement (anywhere in the
    same straight-line scope -- reuses `remember`'s own existing scoping rule verbatim, no
    special case for `parallel`/`match`) that writes to `left`/`right`. Multi-level chains
    (`derive z = y + 1` where `y` is itself derived) are supported: the dependency graph is
    closed transitively and re-emissions are topologically ordered, so writing to the ROOT
    dependency correctly cascades through every derived value downstream of it, in order.

    v0 scope, disclosed: `expr` (raw `name <op> name` / `name <op> literal` text, the SAME flat
    round-trip representation `Branch.condition`/`MatchArm.guard` already use, parsed via
    `expr.py`'s EXISTING `parse_expr()`/`BinOp` machinery) draws the SAME narrow boundary
    `lower_branch` already established for conditions: exactly one operator, no nested
    sub-expressions. `derive` is mid-body only (the same boundary `Loop`/`Match`/`Parallel`
    already have)."""

    kind: Literal["memory-ref"] = "memory-ref"
    op: Literal["derive"] = "derive"
    key: str
    expr: str


# Any statement in a goal/block body is one of the six kernel node forms.
Statement = (
    Call
    | Govern
    | Bind
    | Remember
    | Branch
    | Loop
    | Return
    | Push
    | MapSet
    | Match
    | Parallel
    | Derive
)


class Goal(_Node):
    """goal — the intent a subtree serves (the telos the runtime energizes)."""

    kind: Literal["goal"] = "goal"
    intent: str
    body: list[Statement] = []


class FnDef(_Node):
    """A user-defined function (G1, plan-local D69) — NOT a Statement, NOT a 7th kernel node:
    a top-level declaration (sibling to `Goal`) whose BODY is ordinary `Statement`s, and whose
    CALL SITE (a plain `Call` naming this fn) lowers to the existing `capability-call` kernel
    node exactly like any other capability (founder's own s55 ruling — no new kernel node for
    user-defined functions). `params` (G8, plan-local D69) widens G1's 0-arg-only scope: each
    name gets its own local slot, populated from the caller's real ABI argument registers
    (`emit_call_with_args`'s callee-side counterpart) at fn entry — the SAME `capability_
    addresses`-resolved call site any capability uses, now genuinely carrying values. v0 scope:
    `params` are REGISTER-only (up to 4 on Win64 / 6 on SysV) — reading a stack-spilled INCOMING
    parameter needs a positive-RBP-offset addressing mode `compile_fndef`'s local-slot frame
    doesn't have yet (its slots are all negative-offset locals); the CALL SITE itself already
    supports unlimited args via stack-spilling (`emit_call_with_args`, symmetric for calling
    OUT to any external/native capability) — only a `fn`'s own incoming params draw this
    narrower, disclosed boundary."""

    kind: Literal["fn-def"] = "fn-def"
    name: str
    params: list[str] = []
    body: list[Statement] = []


class Import(_Node):
    """`import alias = "path"` (G7, plan-local D69) — a top-level declaration (sibling to
    `Goal`/`FnDef`), NOT a Statement, NOT a new kernel node: multi-file resolution is entirely a
    CLOSED-tree, pre-codegen concern (`madras.dsl.kollan_modules`), same "declaring is open,
    resolving/executing is closed" split D58 already draws for allocation. A call into an
    imported file is a PLAIN `Call` whose `name` is the qualified `"alias.fn"` string (research:
    Zig's `@import` for the "no separate module-namespace type" simplicity + Go's whole-program
    import-DAG discipline for cycle-safe multi-file resolution) -- `Call.name` already accepts
    any `str`, so a dotted name needs zero AST change; only the grammar gains a qualified-call
    form. `path` is resolved relative to the IMPORTING file's own directory (Zig's own
    resolution rule); re-using the same `alias` twice in one file shadows the earlier binding
    (last-imported-wins, a deliberate, disclosed simplification over a collision error)."""

    kind: Literal["import"] = "import"
    alias: str
    path: str


# Resolve forward references (Branch/Loop/MatchArm reference Statement, which references them).
Branch.model_rebuild()
Loop.model_rebuild()
MatchArm.model_rebuild()
Match.model_rebuild()
Parallel.model_rebuild()
Goal.model_rebuild()
FnDef.model_rebuild()
