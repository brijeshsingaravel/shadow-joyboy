"""Nadi (நாடி) -- the `.tamil-IR` seam (RFC-0002 §7.1).

WHY THIS EXISTS
---------------
Both back-ends consume the AST *directly*: `kollan` imports 25 AST node types, `interpreter.py`
walks the same tree. Surface syntax is therefore welded to the compiler -- change a delimiter and
the back-ends break. Every syntax question becomes a compiler risk.

That is precisely the failure mode the Thirukkural documents. Its *meaning* survived two
millennia and 40+ translations; its *form* -- seven words, 4+3, a rhythm -- did not, and every
translator concedes a translation is "at most a faint replica" of the original. What carries its
own structure travels; what depends on the surface is lost. A language meant to be adopted by
people who did not write it (English: ~75% non-native; Linux: won wherever the user is a builder)
must be able to lose its surface without losing itself.

Nadi is that decoupling point: the front-end targets Nadi, Nadi targets *either* back-end. This
is the MLIR/LLVM discipline -- a stable meta-IR with pluggable lowerings -- and it is what makes
`[ ]` vs `{ }` a free choice instead of a migration.

**Named நாடி** -- pulse / nerve / *tube*, the channel a thing flows through. Attested in the Kural
itself (504: `குணம்நாடிக் குற்றமும் நாடி...`, "having examined"). Distinct from நதி (river), a
Sanskrit loan and a common confusion.

THE DESIGN
----------
MLIR-shaped, deliberately: `op + operands + attributes + regions`. One `NadiOp` type, and the
variation lives in fields -- the SAME two-level discipline that has held the kernel at six
primitives across ~15 build phases, and that s59 restored to the value space. An IR with a node
class per construct would be a third place for the kernel to silently grow.

`kind` is constrained to the six frozen primitives (`KERNEL_KINDS`) and is validated, so a
lowering **cannot** invent a seventh. `op` is the free variation beneath it.

SSA-ish: every op that produces a value carries a unique `result`, so a back-end can trust a name
identifies exactly one definition. That is the property that makes the IR safe to re-target.

STATE (s59): `interpreter.py` now reads Nadi -- rank floor, capability set, working-set size and
intent all come from the IR, so the interpreted path no longer knows any AST node type.
Re-pointing `kollan` (3,354 lines, 25 AST node types) is the deliberately separate next row --
one new risk at a time, the same discipline every K-phase row used.
"""

from __future__ import annotations

import hashlib
from itertools import count
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tamil_lang.ast import (
    Bind,
    Branch,
    Call,
    Derive,
    FnDef,
    Goal,
    Govern,
    Loop,
    MapSet,
    Match,
    Parallel,
    Push,
    Remember,
    Return,
)

# Reused, never re-derived: the Morton arithmetic and the frozen 6-entry category dict have
# exactly one home (`mugavari.py`), the same discipline `decode_morton3` was kept single for.
from tamil_lang.mugavari import _CATEGORY, _morton3

# The six frozen kernel primitives (RFC-0002 §3.3/§3.6). Nadi's op set IS the kernel -- an IR
# that allowed a seventh kind would become a back door around the frozen base.
KERNEL_KINDS = frozenset(
    {
        "goal",
        "governance-check",
        "memory-ref",
        "control-flow",
        "compose-bind",
        "capability-call",
    }
)

Kind = Literal[
    "goal", "governance-check", "memory-ref", "control-flow", "compose-bind", "capability-call"
]


class UnsupportedForNadi(TypeError):
    """A node the lowering does not understand. Raised rather than skipped: a silently lossy IR
    is worse than no IR -- the back-end would compile a program the author did not write."""


class NadiOp(BaseModel):
    """One IR operation. MLIR-shaped: a kernel `kind`, a free `op` variation, SSA `result`,
    `operands` (names of values defined earlier), `attrs` (literal payload), and `regions`
    (nested op lists -- branch arms, loop bodies)."""

    model_config = ConfigDict(extra="forbid")

    kind: Kind
    op: str
    result: str | None = None
    operands: list[str] = Field(default_factory=list[str])
    attrs: dict[str, Any] = Field(default_factory=dict)
    regions: list[list[NadiOp]] = Field(default_factory=list["list[NadiOp]"])
    # The Morton-coded materialized-path address (RFC-0002 §4.2), `None` until
    # `assign_mugavari_ids()` runs -- depth and order are only knowable once the whole module
    # exists, the same reason `mugavari.assign_ids` is a separate post-parse pass on the AST.
    mugavari_id: str | None = None

    @model_validator(mode="after")
    def _kind_is_a_kernel_primitive(self) -> NadiOp:
        if self.kind not in KERNEL_KINDS:
            raise ValueError(f"{self.kind!r} is not one of the six kernel primitives")
        return self

    def walk(self) -> list[NadiOp]:
        """This op and every op nested in its regions, depth-first."""
        # annotated, not inferred: `[self]` would infer `list[Self]`, which is invariant and
        # then rejects the `list[NadiOp]` extension below.
        out: list[NadiOp] = [self]
        for region in self.regions:
            for child in region:
                out.extend(child.walk())
        return out


class NadiModule(BaseModel):
    """A lowered program: one root op (a `goal` or `fn`) plus its regions."""

    model_config = ConfigDict(extra="forbid")

    root: NadiOp

    def walk(self) -> list[NadiOp]:
        return self.root.walk()

    def addressed(self) -> dict[str, NadiOp]:
        """Every op by its Mugavari address -- the lookup `kollan_cache` needs to key a slot to a
        call site. Raises if `assign_mugavari_ids()` hasn't run, rather than returning a quietly
        incomplete map (the failure mode that let unaddressed AST nodes go unnoticed)."""
        out: dict[str, NadiOp] = {}
        for op in self.walk():
            if op.mugavari_id is None:
                raise ValueError(f"op {op.op!r} has no address -- call assign_mugavari_ids() first")
            out[op.mugavari_id] = op
        return out

    def find_one(self, op: str) -> NadiOp:
        """The single op with this name -- raises if absent or ambiguous, so a test that thinks
        it found something cannot be quietly wrong."""
        hits = [o for o in self.walk() if o.op == op]
        if len(hits) != 1:
            raise LookupError(f"expected exactly one {op!r} op, found {len(hits)}")
        return hits[0]


def _assign_one(op: NadiOp, parent_prefix: str, depth: int, counter: list[int]) -> None:
    """Address one op, then its regions one level deeper -- pre-order, so `order` (the z axis)
    counts in the same sequence `mugavari.assign_ids` uses on the AST.

    **No special cases, and that is the whole argument for computing Mugavari here.** The AST
    version needs two: `FnDef` carries kind `"fn-def"`, which is not one of the frozen six, so it
    borrows `goal`'s category; and grouping helpers (`MatchArm`) carry no `kind` at all, so
    recursion passes through them unaddressed. `NadiOp`'s validator guarantees every op is one of
    the six, so `_CATEGORY[op.kind]` can never miss and nothing is ever skipped."""
    category = _CATEGORY[op.kind]
    order = counter[0]
    counter[0] += 1

    # `regions` excluded so an op's hash reflects its OWN content, not its descendants' -- the
    # same property `mugavari._content_hash` gets by computing before it recurses. Positional
    # identity is already carried by the Morton code (which includes `order`), so two ops with
    # identical content at different sites still get different addresses.
    payload = op.model_dump_json(exclude={"mugavari_id", "regions"})
    content_hash = hashlib.sha256(payload.encode()).hexdigest()[:12]
    op.mugavari_id = f"{parent_prefix}/{_morton3(depth, category, order):x}-{content_hash}"

    for region in op.regions:
        for child in region:
            _assign_one(child, op.mugavari_id, depth + 1, counter)


def lower_each(stmts: list[Any]) -> list[NadiOp]:
    """One IR op per statement, same order, same length -- the pairing a back-end needs to
    migrate incrementally (s59, row 3c-ii).

    `kollan`'s codegen is ~1,234 lines over ~151 dispatch sites, and it emits machine code, so
    moving it to the IR in one sweep would be a large unverifiable jump. This lets it move a
    statement kind at a time: the back-end keeps its existing statement list, pairs each entry
    with its lowered op, and reads whichever kinds have already migrated from the IR while the
    rest still read the AST. Every step is checkable against the byte-identical golden oracle.

    A statement whose VALUE is itself an operation (a `Recall` read) lowers to a prelude op plus
    a main op; the main op is the last, and the prelude is reachable through its operands, so
    nothing is hidden by taking `[-1]`."""
    namer = _Namer()
    return [_lower_stmt(stmt, namer)[-1] for stmt in stmts]


def lower_each_with_defs(stmts: list[Any]) -> tuple[list[NadiOp], dict[str, NadiOp]]:
    """`lower_each`, plus the SSA definition map -- result name -> the op that produced it.

    `lower_each` alone returns the LAST op per statement, which is the statement's own operation.
    That is enough while a back-end only needs the statement itself, but not when it needs the
    value flowing INTO it: `remember x = recall(k)` lowers to a prelude `memory-ref`/`read` plus
    the write, and the write records only `operands=['%recall.0']` -- that a value arrived, not
    what produced it or which key it read.

    The definition map closes that without reconstructing anything: `defs[op.operands[0]]` is the
    producing op, with its own attrs intact. This is ordinary SSA def-use, and it is available
    precisely because every value-producing op carries a unique `result`.

    Preferred over widening `lower_each` to return every op per statement: a back-end wants "the
    statement's op" and "where this value came from" as two separate questions, and answering
    them with two structures keeps the common iteration a flat 1:1 walk."""
    namer = _Namer()
    mains: list[NadiOp] = []
    defs: dict[str, NadiOp] = {}
    for stmt in stmts:
        lowered = _lower_stmt(stmt, namer)
        for produced in lowered:
            for op in produced.walk():
                if op.result is not None:
                    defs[op.result] = op
        mains.append(lowered[-1])
    return mains, defs


def assign_mugavari_ids(module: NadiModule) -> NadiModule:
    """Assign every op's Mugavari address in place (RFC-0002 §4.2); returns the same module.

    The IR counterpart of `mugavari.assign_ids`, and the piece that closes row 2's disclosed
    boundary: `kollan_cache` keys its result cache by Mugavari ID, so the compiled path could not
    read the IR until the IR could carry an address.

    Deliberately NOT string-identical to the AST scheme -- an op's content hash is taken over the
    IR op rather than the AST node, so the strings differ by construction. What is preserved is
    the meaning of the three axes (depth, category, order), which is what §4.2 actually specifies
    and what `decode_morton3` reads back. `kollan_cache`'s slot map is an in-process dict, so no
    stored state depends on the old strings."""
    _assign_one(module.root, "", 0, [0])
    return module


class _Namer:
    """SSA result names. A single counter per lowering makes results unique module-wide, so
    re-binding a `.tamil` name (legal in source) can never collide in the IR."""

    def __init__(self) -> None:
        self._n = count()

    def next(self, hint: str) -> str:
        return f"%{hint}.{next(self._n)}"


def _value_attrs(
    value: Any, namer: _Namer | None = None, prelude: list[NadiOp] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Split a Value into (attrs, operands). A bare `str` is either a bound name (an operand) or
    an integer literal (an attribute); a structured Value node carries its own payload.

    Deliberately records the value's KIND rather than its source spelling -- the IR must not
    remember how the literal was written, only what it is."""
    if isinstance(value, str):
        try:
            return {"const": int(value)}, []
        except ValueError:
            return {}, [value]
    # A `Recall` is a genuine kernel node -- `memory-ref`, op `read`. Folding it into an
    # attribute would ERASE a real operation that has a real cost and a real Mugavari address,
    # and the AST/Nadi differential test caught exactly that (a recall-valued `remember` counted
    # 5 nodes in the AST and 4 ops in Nadi). Emitted as its own op, consumed by operand -- which
    # is also what proper SSA requires: a read produces a value.
    if getattr(value, "kind", None) == "memory-ref" and namer is not None and prelude is not None:
        read = NadiOp(
            kind="memory-ref", op="read", result=namer.next("recall"), attrs={"key": value.key}
        )
        prelude.append(read)
        return {}, [read.result or ""]

    kind = getattr(value, "kind", None)
    form = getattr(value, "form", None)
    attrs: dict[str, Any] = {"value_kind": kind}
    if form:
        attrs["value_form"] = form
    # `key` is deliberately re-homed to `selector_key`. A `Project` (`a[i]`, `m.field`) carries the
    # SELECTOR's name in its own `key`, and a statement's `key` is its DESTINATION -- two different
    # things under one name. Emitting both flat let the value's key overwrite the statement's, so
    # `remember dest = arr[idx]` lowered to a write of `idx` and the destination vanished. Silent,
    # and a guaranteed miscompile the moment codegen reads the IR.
    #
    # **Every field the value declares, not a hand-written list of them.** The list this replaced
    # ("type", "selector", "key", "op", "text", "elements", "fields", "start", "stop") omitted
    # `Project.source`, so `remember d = a[i]` lowered to a write that knew its destination, that
    # it was an index projection, and the index name -- but not WHICH ARRAY. Codegen cannot work
    # from that, and nothing reported it.
    #
    # That is the same defect shape as the three tree-walkers (a stale isinstance ladder) and the
    # five collectors (four hand-written recursion cases apiece): an enumeration maintained by
    # hand, drifting as the value space grew. Driving off the model's own fields means a value
    # form added later cannot silently lose one.
    for field in getattr(type(value), "model_fields", {}):
        if field in ("kind", "form", "mugavari_id"):
            continue  # already recorded above as value_kind/value_form, or not semantic
        got = getattr(value, field, None)
        if got is not None and not isinstance(got, BaseModel):
            attrs["selector_key" if field == "key" else field] = got
    return attrs, []


def _lower_stmt(stmt: Any, namer: _Namer) -> list[NadiOp]:
    """One AST statement -> one NadiOp. Every branch names its kernel primitive explicitly, so
    the mapping from AST to kernel is auditable in one place rather than inferred.

    Returns a LIST: a value that is itself a kernel operation (a `Recall` read) is emitted
    as its own preceding op and consumed by operand, so no real operation is erased into an
    attribute. Most statements return a single-element list."""
    prelude: list[NadiOp] = []
    if isinstance(stmt, Govern):
        return [*prelude, NadiOp(kind="governance-check", op="govern", attrs={"check": stmt.check})]

    if isinstance(stmt, Call):
        attrs, operands = {"name": stmt.name, "capability_kind": stmt.capability_kind}, []
        if stmt.lang:
            attrs["lang"] = stmt.lang
        # `args` records EVERY argument in source order; `operands` records only those that are
        # SSA values (a bound name, or a `Recall` read's result). The two differ exactly when an
        # argument is a LITERAL, and dropping those was real data loss: a call's literal argument
        # is illegal under G8's v0 boundary, but the IR silently forgetting it meant the check
        # that rejects it never fired -- `test_a_literal_call_argument_raises` caught precisely
        # that when the codegen started reading arguments from here.
        #
        # Kept as two fields rather than one because they mean different things: `operands` must
        # stay honestly SSA (names defined earlier), while `args` is the faithful argument list a
        # back-end needs -- including the illegal entries it is responsible for rejecting.
        arg_list: list[str] = []
        for arg in stmt.args:
            _, o = _value_attrs(arg, namer, prelude)
            operands.extend(o)
            arg_list.append(o[0] if o else str(arg))
        if arg_list:
            attrs["args"] = arg_list
        return [
            *prelude,
            NadiOp(
                kind="capability-call",
                op="call",
                result=namer.next("call"),
                operands=operands,
                attrs=attrs,
            ),
        ]

    if isinstance(stmt, Bind):
        inner = _lower_stmt(stmt.call, namer)[-1] if isinstance(stmt.call, Call) else None
        if inner is None:  # a Project (verified-field) bound directly
            attrs, operands = _value_attrs(stmt.call, namer, prelude)
            inner = NadiOp(
                kind="memory-ref",
                op="read",
                result=namer.next("proj"),
                operands=operands,
                attrs=attrs,
            )
        return [
            *prelude,
            NadiOp(
                kind="compose-bind",
                op="bind",
                result=namer.next(stmt.target),
                operands=[inner.result] if inner.result else [],
                attrs={"target": stmt.target, "cached": stmt.cached},
                regions=[[inner]],
            ),
        ]

    if isinstance(stmt, Remember):
        attrs, operands = _value_attrs(stmt.value, namer, prelude)
        return [
            *prelude,
            NadiOp(
                kind="memory-ref",
                op="write",
                result=namer.next(stmt.key),
                operands=operands,
                attrs={**attrs, "key": stmt.key},
            ),
        ]

    if isinstance(stmt, Push):
        attrs, operands = _value_attrs(stmt.value, namer, prelude)
        return [
            *prelude,
            NadiOp(
                kind="memory-ref",
                op="push",
                result=namer.next("push"),
                operands=operands,
                attrs={**attrs, "list": stmt.list_name},
            ),
        ]

    if isinstance(stmt, MapSet):
        attrs, operands = _value_attrs(stmt.value, namer, prelude)
        return [
            *prelude,
            NadiOp(
                kind="memory-ref",
                op="map-set",
                result=namer.next("mapset"),
                operands=operands,
                attrs={**attrs, "map": stmt.map_name, "key": stmt.key},
            ),
        ]

    if isinstance(stmt, Derive):
        attrs, operands = _value_attrs(stmt.expr, namer, prelude)
        return [
            *prelude,
            NadiOp(
                kind="memory-ref",
                op="derive",
                result=namer.next(stmt.key),
                operands=operands,
                attrs={**attrs, "key": stmt.key},
            ),
        ]

    if isinstance(stmt, Branch):
        return [
            *prelude,
            NadiOp(
                kind="control-flow",
                op="branch",
                attrs={"condition": stmt.condition},
                regions=[
                    [o for s in stmt.then for o in _lower_stmt(s, namer)],
                    [o for s in stmt.otherwise for o in _lower_stmt(s, namer)],
                ],
            ),
        ]

    if isinstance(stmt, Loop):
        attrs, operands = _value_attrs(stmt.iterable, namer, prelude)
        return [
            *prelude,
            NadiOp(
                kind="control-flow",
                op="loop",
                operands=operands,
                attrs={**attrs, "var": stmt.var},
                regions=[[o for s in stmt.body for o in _lower_stmt(s, namer)]],
            ),
        ]

    if isinstance(stmt, Match):
        # Arm metadata rides as an `arms` ATTRIBUTE, parallel to `regions` by index -- the three
        # fields that decide what an arm MEANS (`pattern`, `bind`, `guard`) as opposed to what it
        # DOES (its body, which is the region). Lowering only the bodies dropped all three, and a
        # match cannot be compiled without its patterns; `bind` additionally gets a real slot from
        # `_collect_symbols`, so losing it shifts every later slot and changes the emitted code.
        #
        # NOT a wrapper op per arm, which would be more MLIR-idiomatic: an extra op per arm would
        # inflate the IR's op count, and `MatchArm` carries no kernel `kind` so the AST never
        # counts it -- that would break the `working_set_size` differential against `elastic_box`
        # which every row so far rests on. Metadata parallel to regions keeps both properties.
        return [
            *prelude,
            NadiOp(
                kind="control-flow",
                op="match",
                operands=[stmt.scrutinee],
                attrs={
                    "arms": [
                        {"pattern": arm.pattern, "bind": arm.bind, "guard": arm.guard}
                        for arm in stmt.arms
                    ]
                },
                regions=[[o for s in arm.body for o in _lower_stmt(s, namer)] for arm in stmt.arms],
            ),
        ]

    if isinstance(stmt, Parallel):
        return [
            *prelude,
            NadiOp(
                kind="control-flow",
                op="parallel",
                regions=[[o for s in stmt.body for o in _lower_stmt(s, namer)]],
            ),
        ]

    if isinstance(stmt, Return):
        attrs, operands = _value_attrs(stmt.value, namer, prelude)
        return [*prelude, NadiOp(kind="control-flow", op="return", operands=operands, attrs=attrs)]

    raise UnsupportedForNadi(
        f"{type(stmt).__name__} has no Nadi lowering -- every statement must map to one of the "
        f"six kernel primitives, or the IR would be silently lossy"
    )


def lower_to_nadi(program: Goal | FnDef) -> NadiModule:
    """`.tamil` AST -> Nadi IR. The front-end's only output contract.

    Everything the surface syntax decided -- delimiters, keyword spellings, punctuation -- is
    gone by the time this returns. What remains is structure: which kernel primitive, which
    variation, which operands, which nested regions."""
    namer = _Namer()
    body = [op for s in program.body for op in _lower_stmt(s, namer)]
    if isinstance(program, FnDef):
        root = NadiOp(
            kind="capability-call",
            op="fn",
            result=namer.next(program.name),
            attrs={"name": program.name, "params": list(program.params)},
            regions=[body],
        )
    else:
        root = NadiOp(kind="goal", op="goal", attrs={"intent": program.intent}, regions=[body])
    return NadiModule(root=root)


# ---------------------------------------------------------------------------------------------
# IR readers -- the queries a back-end asks of a lowered program.
#
# These exist so a back-end never has to know an AST node type. Each is a `walk()` over the IR,
# which recurses into regions BY CONSTRUCTION -- the property that makes it structurally
# impossible to repeat the s59 class of bug, where three hand-written AST walkers each silently
# skipped whatever node kinds were added after they were written.
# ---------------------------------------------------------------------------------------------


def capability_names(module: NadiModule) -> list[str]:
    """Every capability this program invokes, at any depth, in source order.

    Security-relevant: a consumer feeds this to capability validation and sandbox gating, so a
    name missing here is a capability that runs unvalidated and unisolated. Region recursion is
    what guarantees `if x > 0 { call untrusted() }` cannot escape."""
    return [
        op.attrs["name"]
        for op in module.walk()
        if op.kind == "capability-call" and "name" in op.attrs
    ]


def governance_checks(module: NadiModule) -> list[str]:
    """Every `governance-check` this program declares, in source order. The check text itself is
    deliberately opaque to the IR -- interpreting `rank >= 2` against a real rank ladder is the
    back-end's concern, not the language's (RFC-0002: the kernel has no rank vocabulary)."""
    return [op.attrs["check"] for op in module.walk() if op.kind == "governance-check"]


def intent_of(module: NadiModule) -> str:
    """The root goal's intent. Empty for an `fn` root, which has a name rather than an intent."""
    return str(module.root.attrs.get("intent", ""))


def working_set_size(module: NadiModule) -> int:
    """The program's whole-tree op count -- the elastic box's occupancy (RFC-0002 §5.1's
    "Bounded" law, §5.2).

    Verified equal to `elastic_box.working_set_size` over the AST on every real stdlib program
    and every construct (branch, match, parallel, recall-in-value, recall-in-arg) -- see
    `tests/test_tamil_lang/test_walkers_reach_every_node.py`. Two independent implementations
    agreeing is what makes this safe to depend on."""
    return len(module.walk())


def fits_in_box(module: NadiModule, v_max: int) -> bool:
    """Does the whole working set fit under the `V_max` ceiling?"""
    return working_set_size(module) <= v_max


# ---------------------------------------------------------------------------------------------
# The data collectors kollan needs before it can allocate (D58: `compile_goal` never allocates;
# the closed tree resolves real addresses first and hands them in).
#
# kollan carries five near-identical `_collect_X_into` walkers for these, and each hand-recurses
# into `Branch.then`/`Branch.otherwise`/`Loop.body`/`Match.arms` -- five collectors times four
# recursion cases, twenty places for a later node kind to be forgotten. Here they are one filter
# over one walk that already descends into regions by construction, so those twenty cases stop
# existing rather than being reimplemented correctly.
#
# Literals arrive as `memory-ref`/`write` ops carrying `type` plus a payload, and the payload is
# stored as STRINGS in attrs (the IR keeps the literal, not the host type) -- so each reader
# converts to the Python type kollan actually allocates from.
# ---------------------------------------------------------------------------------------------


def _literal_writes(module: NadiModule, type_name: str) -> list[NadiOp]:
    """Every `remember` of a literal of one type, in program order (Nadi's walk is pre-order, the
    same sequence the AST collectors visit statements in). Order is preserved deliberately: a
    record's field ordinal IS its byte offset and an array's element order IS its layout."""
    return [
        op
        for op in module.walk()
        if op.kind == "memory-ref" and op.op == "write" and op.attrs.get("type") == type_name
    ]


def nadi_symbols(module: NadiModule) -> dict[str, int]:
    """Name -> local slot, in first-appearance order -- the IR counterpart of
    `kollan._collect_symbols`.

    **Order is the whole contract.** A slot number is its insertion index, and slot numbers appear
    DIRECTLY in the emitted machine code, so a single transposition is a miscompile rather than a
    cosmetic difference. Two orderings have to be got exactly right:

    - a `loop` publishes its induction variable BEFORE its body, so the var is visited before the
      region -- which pre-order `walk()` gives for free;
    - a `match` interleaves: arm 0's payload bind, then arm 0's body, then arm 1's bind, then arm
      1's body. A plain `walk()` would emit ALL arm binds first (they live in the op's own attrs)
      and only then the bodies -- a different order the moment any arm body declares a name. That
      is why this walks arms explicitly instead of reusing `walk()`.

    An array literal is deliberately excluded, matching the AST version: it is a compile-time
    declaration whose real address is resolved entirely outside the symbol table, so it takes no
    runtime slot.
    """
    symbols: dict[str, int] = {}

    def visit(ops: list[NadiOp]) -> None:
        for op in ops:
            if op.kind == "compose-bind":
                symbols.setdefault(str(op.attrs["target"]), len(symbols))
            elif op.kind == "memory-ref" and op.op == "write":
                if op.attrs.get("type") != "array":
                    symbols.setdefault(str(op.attrs["key"]), len(symbols))
            elif op.kind == "control-flow" and op.op == "loop":
                symbols.setdefault(str(op.attrs["var"]), len(symbols))
                for region in op.regions:
                    visit(region)
            elif op.kind == "control-flow" and op.op == "match":
                arms = list(op.attrs.get("arms", []))
                for i, region in enumerate(op.regions):
                    bind = arms[i].get("bind") if i < len(arms) else None
                    if bind is not None:
                        symbols.setdefault(str(bind), len(symbols))
                    visit(region)
            elif op.kind == "control-flow":
                for region in op.regions:
                    visit(region)

    for region in module.root.regions:
        visit(region)
    return symbols


def nadi_cached_binds(module: NadiModule) -> set[str]:
    """Every Mugavari address a cached `compose-bind` needs a real result-cache slot for -- the
    IR counterpart of `kollan.collect_cached_binds` (T8.17).

    `kollan_cache` keys its slots by address, and its two requirements are correctness
    properties, not tidiness: an address must be DETERMINISTIC (one cache instance is built once
    and reused across every goal an agent compiles, so the same call site must key to the same
    slot every time) and DISTINCT PER CALL SITE (two sites sharing a slot is exactly the
    stale-result bug `test_kollan_cache_loop_risk.py` pins -- a cached call inside a loop,
    argument varying per iteration, silently returning the first iteration's answer).

    Requires `assign_mugavari_ids(module)` to have run. Raises rather than skipping: caching
    without a stable per-call-site key is not a degraded cache, it is a wrong one."""
    ids: set[str] = set()
    for op in module.walk():
        if op.kind == "compose-bind" and op.attrs.get("cached"):
            if op.mugavari_id is None:
                raise ValueError(
                    f"cached bind {op.attrs.get('target')!r} has no address -- run "
                    "assign_mugavari_ids(module) before collecting cache keys"
                )
            ids.add(op.mugavari_id)
    return ids


def nadi_cached_binds_with_args(module: NadiModule) -> set[str]:
    """The SUBSET of `nadi_cached_binds` whose call carries arguments (N2).

    Those need a different cache shape -- a small content-addressed hash table keyed by the
    argument VALUE rather than a single call-site slot -- because a cached call inside a loop with
    a per-iteration argument returns stale data under call-site-only keying
    (`test_kollan_cache_loop_risk.py`, a live-confirmed bug, not a hypothetical)."""
    ids: set[str] = set()
    for op in module.walk():
        if op.kind != "compose-bind" or not op.attrs.get("cached") or op.mugavari_id is None:
            continue
        producer = op.regions[0][0] if op.regions and op.regions[0] else None
        if producer is None or producer.kind != "capability-call":
            continue
        if producer.attrs.get("args"):
            ids.add(op.mugavari_id)
    return ids


def nadi_arrays(module: NadiModule) -> dict[str, list[int]]:
    """Name -> literal integer elements, first-appearance order (`kollan.collect_arrays`)."""
    return {
        op.attrs["key"]: [int(e) for e in op.attrs.get("elements", [])]
        for op in _literal_writes(module, "array")
    }


def nadi_lists(module: NadiModule) -> dict[str, list[int]]:
    """Name -> literal integer elements (`kollan.collect_lists`)."""
    return {
        op.attrs["key"]: [int(e) for e in op.attrs.get("elements", [])]
        for op in _literal_writes(module, "list")
    }


def nadi_records(module: NadiModule) -> dict[str, dict[str, int]]:
    """Name -> fields IN DECLARATION ORDER (`kollan.collect_records`). The order is the contract:
    a field's ordinal is its byte offset, so a transposition here is a miscompile."""
    return {
        op.attrs["key"]: {k: int(v) for k, v in op.attrs.get("fields", {}).items()}
        for op in _literal_writes(module, "record")
    }


def nadi_maps(module: NadiModule) -> dict[str, dict[str, int]]:
    """Name -> literal integer entries (`kollan.collect_maps`)."""
    return {
        op.attrs["key"]: {k: int(v) for k, v in op.attrs.get("fields", {}).items()}
        for op in _literal_writes(module, "map")
    }


def nadi_strings(module: NadiModule) -> dict[str, str]:
    """Name -> literal UTF-8 text (`kollan.collect_strings`)."""
    return {
        op.attrs["key"]: str(op.attrs.get("text", "")) for op in _literal_writes(module, "string")
    }


__all__ = [
    "KERNEL_KINDS",
    "NadiModule",
    "NadiOp",
    "UnsupportedForNadi",
    "assign_mugavari_ids",
    "capability_names",
    "fits_in_box",
    "governance_checks",
    "intent_of",
    "lower_each",
    "lower_each_with_defs",
    "lower_to_nadi",
    "nadi_arrays",
    "nadi_cached_binds",
    "nadi_cached_binds_with_args",
    "nadi_lists",
    "nadi_maps",
    "nadi_records",
    "nadi_strings",
    "nadi_symbols",
    "working_set_size",
]
