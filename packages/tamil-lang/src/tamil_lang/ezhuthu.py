"""Ezhuthu — the .tamil grammar-as-tokenizer (RFC-0002 §4.5).

There is no BPE. For `.tamil` source, the *grammar is the tokenizer*: the deterministic naming step
(source symbols → kernel tokens) is exactly Lark's contextual lexer over `kural.lark` — the L0
encoding for source — exact, reversible, row-C structural (the fuzzy embedding step enters
at the model leaf, never here). Learned/dynamic-chunking tokenization is reserved for the HOPE model
(§4.5), not for source.
"""

from __future__ import annotations

from lark import Token

from tamil_lang.kural import _parser


def tokenize(source: str) -> list[Token]:
    """Ezhuthu: lex `.tamil` source into its deterministic kernel-token stream (no BPE)."""
    return list(_parser().lex(source))


def token_kinds(source: str) -> list[str]:
    """The token *types* only (e.g. NAME/STRING/OP) — handy for round-trip/conformance checks."""
    return [t.type for t in tokenize(source)]


__all__ = ["token_kinds", "tokenize"]
