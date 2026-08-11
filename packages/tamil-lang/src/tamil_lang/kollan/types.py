"""Shared, ISA-independent types for the Kollan stencil layer."""

from __future__ import annotations

from typing import Literal

Op = Literal[">=", ">", "<=", "<", "==", "!="]
Isa = Literal["x86_64", "riscv64"]
# G2 (D72/D73): x86-64's calling convention differs by OS, not by CPU -- Win64 (Windows) vs
# System V (Linux/macOS). RISC-V has no such split in this codebase (one calling convention).
# Only x86_64.py's stencils take an `abi` param; riscv64.py's never do.
Abi = Literal["win64", "sysv"]

__all__ = ["Abi", "Isa", "Op"]
