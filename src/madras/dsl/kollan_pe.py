"""kollan_pe.py -- N6: writes a genuinely standalone Windows PE64 executable from compiled
`.tamil` machine code -- `kollan_elf.py`'s Windows sibling. Same motivation: `run_compiled_goal`
only ever executes raw bytes in-memory via `VirtualAlloc`, hosted inside THIS Python process;
this writes a real `.exe` file the Windows loader itself loads and runs as its own process.

Structurally harder than ELF (D72's own established split: Windows has no stable raw-syscall
interface, so a clean process EXIT must go through a real DLL import -- `ExitProcess` in
kernel32.dll -- there is no "just emit a syscall instruction" escape hatch here). ONE section
(code + import table together, the same "sectionless-style" minimal-PE precedent KeyJ's own
"Writing ultra-small Windows executables" documents) to keep the RVA cross-references in a single
place rather than scattered across multiple sections.

The caller's own compiled bytes must end by loading the desired exit code into ECX (Win64's
first int arg register) and NOT include a trailing `ret` -- `build_pe64_executable` appends the
real `call [rip+iat_slot]` into `ExitProcess` itself.
"""

from __future__ import annotations

import struct

_IMAGE_BASE = 0x140000000
_SECTION_VA = 0x1000
_FILE_ALIGN = 0x200
_SECT_ALIGN = 0x1000
_HEADERS_SIZE = 0x200  # DOS + PE + COFF + Optional(PE32+) + 1 section header, padded to file align


def _round_up(n: int, align: int) -> int:
    return (n + align - 1) // align * align


def build_pe64_executable(code: bytes) -> bytes:
    """Wrap `code` (already-compiled x86-64 machine code that loads an exit code into ECX and
    does NOT end in a `ret`) with a real, standalone PE64 executable calling `ExitProcess` via
    kernel32.dll's import table. Returns the complete file bytes -- writing to disk is the
    caller's own job, matching `build_elf64_executable`'s identical contract."""
    # Win64 ABI: the caller of ANY external function must reserve 32 bytes of "shadow space"
    # on the stack (for the callee to spill RCX/RDX/R8/R9 into) and keep RSP 16-byte-aligned
    # at the CALL instruction. RSP is guaranteed 16-aligned at process entry (0 mod 16), and
    # `sub rsp, 0x20` (32, itself a multiple of 16) keeps it 16-aligned right up to the CALL --
    # after the implicit return-address push, RSP % 16 == 8 inside the callee, exactly what the
    # ABI requires. Skipping this is a real, live-caught bug: `ExitProcess` reads/writes through
    # that shadow space internally -- without it, it corrupts whatever is below the (never
    # allocated) stack slots and segfaults deep inside KERNELBASE.dll.
    prologue = b"\x48\x83\xec\x20"  # sub rsp, 0x20
    call_len = 6  # FF 15 + rel32 -- `call [rip+disp32]` through the IAT slot
    code_len = len(prologue) + len(code) + call_len

    hintname = struct.pack("<H", 0) + b"ExitProcess\x00"  # OrdinalHint(2) + name, real bytes
    dllname = b"kernel32.dll\x00"

    iat_off = code_len
    ilt_off = iat_off + 16
    importdir_off = ilt_off + 16
    hintname_off = importdir_off + 40  # one real descriptor (20B) + one null terminator (20B)
    dllname_off = hintname_off + len(hintname)
    # Word-alignment of the hint/name entry is a convention some tools rely on, not a strict
    # Windows LOADER requirement -- skipping it (verified live, not assumed) keeps the offset
    # arithmetic and the actual byte layout below in inherent agreement, rather than the two
    # independently computing "should this be padded" and silently disagreeing (a real bug this
    # row already caught once: computing `dllname_off`'s alignment from a DIFFERENT condition
    # than the one the byte-construction code below actually checks).
    dllname_end = dllname_off + len(dllname)

    iat_rva = _SECTION_VA + iat_off
    ilt_rva = _SECTION_VA + ilt_off
    importdir_rva = _SECTION_VA + importdir_off
    hintname_rva = _SECTION_VA + hintname_off
    dllname_rva = _SECTION_VA + dllname_off

    call_site_rva = _SECTION_VA + len(prologue) + len(code)
    next_instr_rva = call_site_rva + call_len
    rel32 = iat_rva - next_instr_rva
    call_iat = b"\xff\x15" + struct.pack("<i", rel32)
    full_code = prologue + code + call_iat
    assert len(full_code) == code_len

    iat = struct.pack("<QQ", hintname_rva, 0)
    ilt = struct.pack("<QQ", hintname_rva, 0)
    import_dir = (
        struct.pack("<IIIII", ilt_rva, 0, 0, dllname_rva, iat_rva)
        + b"\x00" * 20  # the array's own null-entry terminator (no explicit count field)
    )

    section_data = full_code + iat + ilt + import_dir + hintname + dllname
    assert dllname_off == len(full_code) + len(iat) + len(ilt) + len(import_dir) + len(hintname)
    assert len(section_data) == dllname_end

    entry_rva = _SECTION_VA
    raw_size = _round_up(len(section_data), _FILE_ALIGN)
    virt_size = len(section_data)
    image_size = _round_up(_SECTION_VA + virt_size, _SECT_ALIGN)

    dos_header = b"MZ" + b"\x00" * 0x3A + struct.pack("<I", 0x40)
    dos_header += b"\x00" * (0x40 - len(dos_header))
    assert len(dos_header) == 0x40

    pe_sig = b"PE\x00\x00"
    coff = struct.pack(
        "<HHIIIHH",
        0x8664,  # Machine = AMD64
        1,  # NumberOfSections
        0,  # TimeDateStamp
        0,  # PointerToSymbolTable
        0,  # NumberOfSymbols
        240,  # SizeOfOptionalHeader (PE32+)
        0x0022,  # Characteristics: EXECUTABLE_IMAGE | LARGE_ADDRESS_AWARE
    )

    data_dirs = [(0, 0)] * 16
    data_dirs[1] = (importdir_rva, 40)  # Import Table
    opt_header = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x20B,  # Magic = PE32+
        0,
        0,  # Linker major/minor
        raw_size,  # SizeOfCode
        0,  # SizeOfInitializedData
        0,  # SizeOfUninitializedData
        entry_rva,  # AddressOfEntryPoint
        _SECTION_VA,  # BaseOfCode
        _IMAGE_BASE,  # ImageBase
        _SECT_ALIGN,  # SectionAlignment
        _FILE_ALIGN,  # FileAlignment
        6,
        0,  # OS version major/minor
        0,
        0,  # Image version major/minor
        6,
        0,  # Subsystem version major/minor
        0,  # Win32VersionValue
        image_size,  # SizeOfImage
        _HEADERS_SIZE,  # SizeOfHeaders
        0,  # CheckSum
        3,  # Subsystem = WINDOWS_CUI (console)
        0,  # DllCharacteristics
        0x100000,
        0x1000,  # StackReserve/Commit
        0x100000,
        0x1000,  # HeapReserve/Commit
        0,  # LoaderFlags
        16,  # NumberOfRvaAndSizes
    )
    for rva, size in data_dirs:
        opt_header += struct.pack("<II", rva, size)
    assert len(opt_header) == 240

    section_header = b".text\x00\x00\x00" + struct.pack(
        "<IIIIIIHHI",
        virt_size,  # VirtualSize
        _SECTION_VA,  # VirtualAddress
        raw_size,  # SizeOfRawData
        _HEADERS_SIZE,  # PointerToRawData
        0,
        0,  # PointerToRelocations/Linenumbers
        0,
        0,  # NumberOfRelocations/Linenumbers
        0x60000020,  # Characteristics: CODE | EXECUTE | READ
    )
    assert len(section_header) == 40

    headers = dos_header + pe_sig + coff + opt_header + section_header
    headers += b"\x00" * (_HEADERS_SIZE - len(headers))

    file_bytes = headers + section_data
    file_bytes += b"\x00" * (_HEADERS_SIZE + raw_size - len(file_bytes))
    return file_bytes


__all__ = ["build_pe64_executable"]
