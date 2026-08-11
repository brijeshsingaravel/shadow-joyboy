"""Kollan's own error types -- shared across every ISA backend."""

from __future__ import annotations


class UnsupportedOp(ValueError):
    """A comparison operator with no stencil on the requested backend."""


class UnsupportedIsa(ValueError):
    """A target ISA with no backend registered."""


class UnsupportedNode(ValueError):
    """A kernel node `compile_goal` doesn't yet know how to compile -- a real, named scope
    boundary (e.g. Branch/Loop need condition strings parsed into an expression AST first),
    not a silent skip."""


__all__ = ["UnsupportedIsa", "UnsupportedNode", "UnsupportedOp"]
