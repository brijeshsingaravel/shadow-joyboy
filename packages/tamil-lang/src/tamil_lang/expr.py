"""A small, standalone expression parser for `Branch.condition` / `Loop.iterable` strings.

Kural's own grammar already parses a branch condition *structurally* at parse time
(`kural.lark`'s `branch: "if" value OP value block ...`) -- but `kural.py`'s transformer
immediately renders that structure back into a flat string (`f"{v1} {op} {v2}"`) for
`Branch.condition`, a deliberate human-readable round-trip representation (D50/D60's kernel
freeze covers the 6 statement KINDS, not what a string field can be re-parsed into -- changing
that representation is not a kernel change). This module is the missing other half: turning the
string back into a structured expression a consumer that actually needs to *lower* a condition
(Kollan's `compile_goal`, eventually) can use, without touching `ast.py`'s frozen field types or
`kural.py`'s existing round-trip behavior at all.

Grammar: comparisons over arithmetic (`+`/`-`/`*`/`/`) expressions of names and integer
literals -- covers every real condition/iterable shape seen in this codebase's own `.tamil`
programs and tests (`"count > 0"`, `"items == empty"`, `"a > b"`, a bare name like `"x"`) plus
genuine arithmetic for forward compatibility, without inventing syntax `.tamil` doesn't have.
"""

from __future__ import annotations

from functools import cache
from typing import Literal

from lark import Lark, Token, Transformer
from pydantic import BaseModel, ConfigDict

from tamil_lang.ast import (
    Branch,
    Loop,
    Value,
)

_GRAMMAR = r"""
?start: comparison

?comparison: arith
           | arith COMPARE_OP arith   -> compare

?arith: term (ADD_OP term)*           -> arith
?term: factor (MUL_OP factor)*        -> arith
?factor: NUMBER                       -> number
       | NAME                        -> name
       | "(" comparison ")"

COMPARE_OP: ">=" | "<=" | "==" | "!=" | ">" | "<"
ADD_OP: "+" | "-"
MUL_OP: "*" | "/"
NAME: /[a-zA-Z_][a-zA-Z0-9_]*/
NUMBER: /[0-9]+/

%import common.WS
%ignore WS
"""


class Num(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["num"] = "num"
    value: int


class Name(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["name"] = "name"
    name: str


class BinOp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["binop"] = "binop"
    op: Literal["+", "-", "*", "/"]
    left: Expr
    right: Expr


class Compare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["compare"] = "compare"
    op: Literal[">=", "<=", "==", "!=", ">", "<"]
    left: Expr
    right: Expr


Expr = Num | Name | BinOp | Compare
BinOp.model_rebuild()
Compare.model_rebuild()


class ExprParseError(ValueError):
    """`condition`/`iterable` text that doesn't match the supported expression grammar."""


class _ExprTransformer(Transformer):
    def number(self, items: list[Token]) -> Num:
        (tok,) = items
        return Num(value=int(tok))

    def name(self, items: list[Token]) -> Name:
        (tok,) = items
        return Name(name=str(tok))

    def arith(self, items: list) -> Expr:
        # Lark hands back a flat [operand, OP, operand, OP, operand, ...] for the `*`
        # repetition -- fold left-associatively into a real binary tree.
        node = items[0]
        i = 1
        while i < len(items):
            op, rhs = str(items[i]), items[i + 1]
            node = BinOp(op=op, left=node, right=rhs)  # type: ignore[arg-type]
            i += 2
        return node

    def compare(self, items: list) -> Compare:
        left, op_tok, right = items
        return Compare(op=str(op_tok), left=left, right=right)  # type: ignore[arg-type]


@cache
def _parser() -> Lark:
    return Lark(_GRAMMAR, start="start", parser="lalr")


def parse_expr(text: str) -> Expr:
    """Parse a `Branch.condition`/`Loop.iterable` string into a structured `Expr`. Raises
    `ExprParseError` on anything outside the supported grammar -- fails closed, never silently
    returns a partial/best-guess tree."""
    try:
        tree = _parser().parse(text)
    except Exception as exc:  # Lark's own exception hierarchy varies by failure kind
        raise ExprParseError(f"could not parse expression {text!r}: {exc}") from exc
    return _ExprTransformer().transform(tree)


def parse_condition(branch: Branch) -> Expr:
    """Parse a `Branch`'s own `condition` string. Ergonomic wrapper -- identical to
    `parse_expr(branch.condition)`, just named for the call site's actual intent."""
    return parse_expr(branch.condition)


def parse_iterable(loop: Loop) -> Expr | Value:
    """Parse a `Loop`'s own `iterable`. Anything ALREADY structured (any non-`str` `Value` --
    a `Recall` memory-ref read, a `RangeLiteral`, a literal, a `Project`) is returned as-is,
    nothing to parse; only a plain string is parsed like any other expression. Whether a given
    shape is actually a SUPPORTED loop iterable (today, only `RangeLiteral`) is `compile_goal`'s
    own concern, not this function's -- this only avoids re-parsing what's already a real node.

    Tested against `str` rather than listing the structured kinds: an allow-list here silently
    fell out of date every time the Value space grew (it was still missing `RecordLiteral`/
    `StringLiteral`/`ListLiteral`/`MapLiteral`/`Compute`/`CyclesRead`), so the test is inverted
    to the one thing that genuinely needs parsing."""
    if isinstance(loop.iterable, str):
        return parse_expr(loop.iterable)
    return loop.iterable


__all__ = [
    "BinOp",
    "Compare",
    "Expr",
    "ExprParseError",
    "Name",
    "Num",
    "parse_condition",
    "parse_expr",
    "parse_iterable",
]
