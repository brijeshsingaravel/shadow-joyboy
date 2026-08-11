"""Kural — the .tamil kernel parser (RFC-0002 §3, D11).

`parse(source) -> list[Goal]` turns `.tamil` source into the Kural AST (base-6). The grammar
is loaded once (one Lark LALR parser — which also gives Ezhuthu its lexer). The "one grammar,
three uses" invariant: `kural.lark` is the parser here, the lexer in `ezhuthu.py`, and (later) the
GBNF/XGrammar constraint over the model.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from lark import Lark, Token, Transformer

from tamil_lang.ast import (
    ArrayLiteral,
    Bind,
    Branch,
    Call,
    Compute,
    CyclesRead,
    Derive,
    FnDef,
    Goal,
    Govern,
    Import,
    ListLiteral,
    Loop,
    MapLiteral,
    MapSet,
    Match,
    MatchArm,
    Parallel,
    Project,
    Push,
    RangeLiteral,
    Recall,
    RecordLiteral,
    Remember,
    Return,
    StringLiteral,
    Value,
)

_GRAMMAR = Path(__file__).with_name("kural.lark").read_text(encoding="utf-8")
GBNF_PATH = Path(__file__).with_name("kural.gbnf")
# The genome-generated faculty-name fragment (T3.3/RFC-0001 §4) -- standalone, not spliced into
# GBNF_PATH's general-purpose `name` rule; see `scripts/gen_genome_grammar.py`.
GENOME_GBNF_PATH = Path(__file__).with_name("genome.gbnf")


@cache
def _parser() -> Lark:
    return Lark(_GRAMMAR, start="start", parser="lalr", maybe_placeholders=False)


def _render(v: Value) -> str:
    """Render a value back to source-ish text (for the flat `check`/`condition` strings)."""
    if isinstance(v, Recall):
        return f"recall({v.key})"
    if isinstance(v, RangeLiteral):
        return f"range({_render(v.start)}, {_render(v.stop)})"
    if isinstance(v, ArrayLiteral):
        return f"[{', '.join(_render(e) for e in v.elements)}]"
    if isinstance(v, Project):
        if v.selector == "index":
            return f"{v.source}[{v.key}]"
        if v.selector == "field":
            return f"{v.source}.{v.key}"
        if v.selector == "verified-field":
            return f"verified {v.source}.{v.key}"
        if v.selector == "result-tag":
            return f"is_ok({v.source})"
        return f"payload({v.source})"
    if isinstance(v, RecordLiteral):
        return f"{{{', '.join(f'{k} = {_render(val)}' for k, val in v.fields.items())}}}"
    if isinstance(v, StringLiteral):
        return f'"{v.text}"'
    if isinstance(v, ListLiteral):
        return f"list[{', '.join(_render(e) for e in v.elements)}]"
    if isinstance(v, MapLiteral):
        return f"map{{{', '.join(f'{k} = {_render(val)}' for k, val in v.fields.items())}}}"
    if isinstance(v, CyclesRead):
        return "cycles()"
    if isinstance(v, Compute):
        # G11 -- `Compute` is a COMPILER-only Value kind (`kollan/__init__.py`'s own
        # `_lower_derives`, never the parser -- `derive_stmt`'s own transform builds a
        # `Derive(expr=str)`, not a `Compute` node) -- unreachable in practice, kept only to
        # narrow `Value`'s type for the fallback `return v` below.
        raise AssertionError("Compute is compiler-only; the parser never produces one")
    return v


class _ToAst(Transformer):
    """Lower the Lark parse tree into the Kural AST (the six kernel node forms)."""

    # --- values ---------------------------------------------------------
    def atom(self, items: list[Token]) -> str | StringLiteral:
        """G4: a quoted STRING literal now becomes a real `StringLiteral` node (a genuinely
        arena-backed value), not a bare `str` indistinguishable from a name reference -- a
        deliberate, small breaking change (nothing working is lost; a quoted-string `Remember`
        value was already non-functional at codegen time before this)."""
        (tok,) = items
        return StringLiteral(text=tok.value[1:-1]) if tok.type == "STRING" else tok.value

    def recall(self, items: list[Token]) -> Recall:
        (name,) = items
        return Recall(key=name.value)

    def range_expr(self, items: list) -> RangeLiteral:
        start, stop = items
        return RangeLiteral(start=start, stop=stop)

    def array_literal(self, items: list) -> ArrayLiteral:
        return ArrayLiteral(elements=list(items))

    def index_expr(self, items: list) -> Project:
        name, index = items
        return Project(source=name.value, selector="index", key=index)

    def record_literal(self, items: list) -> RecordLiteral:
        names, values = items[0::2], items[1::2]
        return RecordLiteral(fields={n.value: v for n, v in zip(names, values, strict=True)})

    def field_expr(self, items: list[Token]) -> Project:
        record, field = items
        return Project(source=record.value, selector="field", key=field.value)

    def list_literal(self, items: list) -> ListLiteral:
        return ListLiteral(elements=list(items))

    def map_literal(self, items: list) -> MapLiteral:
        names, values = items[0::2], items[1::2]
        return MapLiteral(fields={n.value: v for n, v in zip(names, values, strict=True)})

    def verified_field(self, items: list[Token]) -> Project:
        record, field = items
        return Project(source=record.value, selector="verified-field", key=field.value)

    def is_ok_expr(self, items: list[Token]) -> Project:
        (name,) = items
        return Project(source=name.value, selector="result-tag")

    def payload_expr(self, items: list[Token]) -> Project:
        (name,) = items
        return Project(source=name.value, selector="result-payload")

    def cycles_expr(self, items: list) -> CyclesRead:
        return CyclesRead()

    # --- blocks ---------------------------------------------------------
    def block(self, items: list) -> list:
        return list(items)

    # --- the six kernel statement forms ---------------------------------
    def call(self, items: list) -> Call:
        name = items[0].value
        args: list[Value] = list(items[1:])
        return Call(name=name, args=args)

    def qualified_call(self, items: list) -> Call:
        """A call into an imported file's fn (G7) -- lowers to the SAME `Call` node any other
        capability-call uses, `name` set to the dotted `"alias.fn"` string (`Call.name` already
        accepts any `str`); `madras.dsl.kollan_modules` resolves the dot at merge time, not the
        parser."""
        alias, fn_name = items[0], items[1]
        args: list[Value] = list(items[2:])
        return Call(name=f"{alias.value}.{fn_name.value}", args=args)

    def ffi_call(self, items: list) -> Call:
        """capability-call, capability_kind=ffi_bridge (D10, T3.1) — Python only this pass."""
        name = items[0].value
        args: list[Value] = list(items[1:])
        return Call(name=name, args=args, capability_kind="ffi_bridge", lang="python")

    def fallible_call(self, items: list) -> Call:
        """capability-call, capability_kind=fallible (T8.16) — the target returns a packed
        (tag, value) result instead of a plain scalar."""
        name = items[0].value
        args: list[Value] = list(items[1:])
        return Call(name=name, args=args, capability_kind="fallible")

    def govern(self, items: list) -> Govern:
        name, op, value = items
        return Govern(check=f"{name.value} {op.value} {_render(value)}")

    def bind(self, items: list) -> Bind:
        name, call = items
        return Bind(target=name.value, call=call)

    def cached_bind(self, items: list) -> Bind:
        """A memoized Bind (T8.17) -- `cached bind r = call f()`. v0 scope: only a plain `call`
        RHS (the grammar itself only accepts `call`, not `ffi_call`/`fallible_call`)."""
        name, call = items
        return Bind(target=name.value, call=call, cached=True)

    def remember(self, items: list) -> Remember:
        name, value = items
        return Remember(key=name.value, value=value)

    def branch(self, items: list) -> Branch:
        v1, op, v2, then_block = items[0], items[1], items[2], items[3]
        else_block = items[4] if len(items) > 4 else []
        return Branch(
            condition=f"{_render(v1)} {op.value} {_render(v2)}",
            then=then_block,
            otherwise=else_block,
        )

    def loop(self, items: list) -> Loop:
        name, iterable, body = items
        return Loop(var=name.value, iterable=iterable, body=body)

    def return_stmt(self, items: list) -> Return:
        (value,) = items
        return Return(value=value)

    def push_stmt(self, items: list) -> Push:
        """v0 scope: `value` must be a literal (same "literal, not computed" boundary
        `ArrayIndex.index` already draws) -- `Push.value: str` fails Pydantic validation right
        at parse time if given anything else, the same fail-fast contract `ArrayIndex`/
        `FieldAccess`'s own `str`-typed fields already have."""
        name, value = items
        return Push(list_name=name.value, value=value)

    def map_set_stmt(self, items: list) -> MapSet:
        name, key, value = items
        return MapSet(map_name=name.value, key=key, value=value)

    # --- match (G9) -------------------------------------------------------
    def ok_pattern(self, items: list[Token]) -> tuple[str, str]:
        (name,) = items
        return ("ok", name.value)

    def err_pattern(self, items: list[Token]) -> tuple[str, str]:
        (name,) = items
        return ("err", name.value)

    def literal_pattern(self, items: list[Token]) -> tuple[str, None]:
        (tok,) = items
        return (tok.value, None)

    def wildcard_pattern(self, items: list) -> tuple[str, None]:
        return ("_", None)

    def match_arm(self, items: list) -> MatchArm:
        pattern, bind = items[0]
        if len(items) == 2:
            (block,) = items[1:]
            guard = None
        else:
            v1, op, v2, block = items[1:]
            guard = f"{_render(v1)} {op.value} {_render(v2)}"
        return MatchArm(pattern=pattern, bind=bind, guard=guard, body=block)

    def match_stmt(self, items: list) -> Match:
        name_tok, *arms = items
        return Match(scrutinee=name_tok.value, arms=arms)

    def parallel_stmt(self, items: list) -> Parallel:
        return Parallel(body=items)

    def derive_stmt(self, items: list) -> Derive:
        name_tok, v1, op_tok, v2 = items
        return Derive(key=name_tok.value, expr=f"{_render(v1)} {op_tok.value} {_render(v2)}")

    # --- top level ------------------------------------------------------
    def goal(self, items: list) -> Goal:
        intent_tok, body = items
        return Goal(intent=intent_tok.value[1:-1], body=body)

    def fndef(self, items: list) -> FnDef:
        name_tok, *param_toks, body = items
        return FnDef(name=name_tok.value, params=[p.value for p in param_toks], body=body)

    def import_decl(self, items: list) -> Import:
        alias_tok, path_tok = items
        return Import(alias=alias_tok.value, path=path_tok.value[1:-1])

    def start(self, items: list[Goal | FnDef | Import]) -> list[Goal | FnDef | Import]:
        return list(items)


def parse_program(source: str) -> list[Goal | FnDef | Import]:
    """Parse `.tamil` source into the Kural AST — top-level `goal`s, `fn` defs (G1), AND
    `import` decls (G7), in source order. The general entry point; use this for any program that
    may declare functions or import other files."""
    tree = _parser().parse(source)
    return _ToAst().transform(tree)  # type: ignore[return-value]


def parse(source: str) -> list[Goal]:
    """Parse `.tamil` source into the Kural AST, **goals only** — the pre-G1 entry point, kept
    additive/backward-compatible for existing callers (10 call sites predate `fn` defs). A
    program with `fn` defs/`import` decls alongside goals returns only the goals here; use
    `parse_program()` for the full mixed list."""
    return [n for n in parse_program(source) if isinstance(n, Goal)]


__all__ = [
    "ArrayLiteral",
    "Bind",
    "Branch",
    "Call",
    "Derive",
    "FnDef",
    "Goal",
    "Govern",
    "Import",
    "Loop",
    "Match",
    "MatchArm",
    "Parallel",
    "RangeLiteral",
    "Recall",
    "RecordLiteral",
    "Remember",
    "Return",
    "StringLiteral",
    "parse",
    "parse_program",
]
