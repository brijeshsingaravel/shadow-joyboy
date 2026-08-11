"""kollan_elf.py -- N6: writes a genuinely standalone Linux ELF64 executable from compiled
`.tamil` machine code -- the real "single binary" (RFC-0002 SS5.8) `run_compiled_goal`/
`run_compiled_fndefs` never produced: those execute raw bytes in-memory via `VirtualAlloc`/
`mmap`, hosted inside THIS Python process. This writes a real file the OS itself loads and runs
as its own process, no host process involved at all.

Minimal by design (research: real-world minimal ELF64 executables run ~120-130 bytes total) --
ONE PT_LOAD segment, no section headers (the kernel only needs program headers to execute a
binary; section headers are for linking/debugging, genuinely optional), no dynamic linking, no
libc. The caller's own compiled bytes must be a real, self-contained `_start` -- typically
ending in a raw `exit`/`exit_group` syscall (G2's own `emit_syscall`, already built, D72) since
there is no caller process to `ret` into.
"""

from __future__ import annotations

import struct

_BASE_VADDR = 0x400000
_EHDR_SIZE = 64
_PHDR_SIZE = 56


def build_elf64_executable(code: bytes) -> bytes:
    """Wrap `code` (already-compiled x86-64 machine code, ending in a real exit syscall, NOT a
    `ret`) in a minimal, valid ELF64 header + single PT_LOAD program header. Returns the
    complete file bytes -- writing to disk + `chmod +x` is the caller's own job (this function
    is pure, matching `compile_goal`'s own "bytes in, bytes out" contract)."""
    entry = _BASE_VADDR + _EHDR_SIZE + _PHDR_SIZE
    total_size = _EHDR_SIZE + _PHDR_SIZE + len(code)

    # magic + class/data/ver/OSABI
    e_ident = bytes([0x7F]) + b"ELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8
    ehdr = e_ident + struct.pack(
        "<HHIQQQIHHHHHH",
        2,  # e_type = ET_EXEC
        0x3E,  # e_machine = EM_X86_64
        1,  # e_version
        entry,  # e_entry
        _EHDR_SIZE,  # e_phoff -- program header sits right after this header
        0,  # e_shoff -- no section headers
        0,  # e_flags
        _EHDR_SIZE,  # e_ehsize
        _PHDR_SIZE,  # e_phentsize
        1,  # e_phnum -- exactly one PT_LOAD segment
        0,  # e_shentsize
        0,  # e_shnum
        0,  # e_shstrndx
    )
    assert len(ehdr) == _EHDR_SIZE

    phdr = struct.pack(
        "<IIQQQQQQ",
        1,  # p_type = PT_LOAD
        5,  # p_flags = PF_X | PF_R (read + execute, no write -- real W^X, code is immutable)
        0,  # p_offset -- the WHOLE file (headers included) is the loaded segment
        _BASE_VADDR,  # p_vaddr
        _BASE_VADDR,  # p_paddr
        total_size,  # p_filesz
        total_size,  # p_memsz
        0x1000,  # p_align
    )
    assert len(phdr) == _PHDR_SIZE

    return ehdr + phdr + code


__all__ = ["build_elf64_executable"]
