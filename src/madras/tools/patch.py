"""apply_patch envelope parser + context-hunk applier (pure logic, no I/O).

Adopts Codex's public patch envelope — robust *context-based* hunks (match by
surrounding text, not line numbers) so weak/free models don't have to count
lines. The governed apply_patch tool (tools/builtin/dangerous.py) layers
workspace confinement, read-before-write, atomic apply + rollback, and audit on
top of this.

Envelope::

    *** Begin Patch
    *** Add File: path
    +new line
    *** Update File: path
    *** Move to: newpath        (optional rename)
    @@                          (optional hunk separator / context hint)
     context line
    -removed line
    +added line
    *** Delete File: path
    *** End Patch
"""

from __future__ import annotations

from dataclasses import dataclass, field

_BEGIN = "*** Begin Patch"
_END = "*** End Patch"
_ADD = "*** Add File:"
_UPDATE = "*** Update File:"
_DELETE = "*** Delete File:"
_MOVE = "*** Move to:"


class PatchError(ValueError):
    """Raised when a patch envelope is malformed or a hunk cannot be applied."""


@dataclass
class PatchOp:
    action: str  # "add" | "update" | "delete"
    path: str
    new_content: str | None = None  # for "add"
    hunks: list[list[str]] = field(default_factory=list[list[str]])  # for "update"
    move_to: str | None = None  # for "update" rename


def _is_header(line: str) -> bool:
    return line.startswith(("*** ",))


def parse_patch(text: str) -> list[PatchOp]:
    """Parse a patch envelope into ordered operations. Raises PatchError."""
    if not text or not text.strip():
        raise PatchError("empty patch")
    lines = text.splitlines()
    # Locate the envelope.
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == _BEGIN)
    except StopIteration as exc:
        raise PatchError(f"patch must start with '{_BEGIN}'") from exc
    body = lines[start + 1 :]
    if not any(ln.strip() == _END for ln in body):
        raise PatchError(f"patch must end with '{_END}'")

    ops: list[PatchOp] = []
    i = 0
    n = len(body)
    while i < n:
        line = body[i]
        stripped = line.strip()
        if stripped == _END:
            break
        if line.startswith(_ADD):
            path = line[len(_ADD) :].strip()
            i += 1
            content_lines: list[str] = []
            while i < n and not _is_header(body[i]):
                cl = body[i]
                content_lines.append(cl[1:] if cl.startswith("+") else cl)
                i += 1
            ops.append(PatchOp(action="add", path=path, new_content="\n".join(content_lines)))
            continue
        if line.startswith(_UPDATE):
            path = line[len(_UPDATE) :].strip()
            i += 1
            move_to: str | None = None
            if i < n and body[i].startswith(_MOVE):
                move_to = body[i][len(_MOVE) :].strip()
                i += 1
            hunks: list[list[str]] = []
            current: list[str] = []
            while i < n and not _is_header(body[i]):
                hl = body[i]
                if hl.startswith("@@"):
                    if current:
                        hunks.append(current)
                        current = []
                else:
                    current.append(hl)
                i += 1
            if current:
                hunks.append(current)
            if not hunks:
                raise PatchError(f"update for {path!r} has no hunks")
            ops.append(PatchOp(action="update", path=path, hunks=hunks, move_to=move_to))
            continue
        if line.startswith(_DELETE):
            ops.append(PatchOp(action="delete", path=line[len(_DELETE) :].strip()))
            i += 1
            continue
        # Stray line outside any operation (e.g. the Begin marker echoed) — skip.
        i += 1

    if not ops:
        raise PatchError("patch contains no operations")
    return ops


def _split_hunk(hunk: list[str]) -> tuple[str, str]:
    """Return (search_text, replace_text) for one context hunk."""
    search: list[str] = []
    replace: list[str] = []
    for ln in hunk:
        if ln.startswith("+"):
            replace.append(ln[1:])
        elif ln.startswith("-"):
            search.append(ln[1:])
        else:
            # Context line — tolerate a missing leading space.
            text = ln[1:] if ln.startswith(" ") else ln
            search.append(text)
            replace.append(text)
    return "\n".join(search), "\n".join(replace)


def apply_hunks(old: str, hunks: list[list[str]]) -> str:
    """Apply context hunks to ``old`` sequentially. Raises PatchError on no-match."""
    content = old
    for idx, hunk in enumerate(hunks):
        search, replace = _split_hunk(hunk)
        if search == "":
            # Pure insertion with no anchor is ambiguous; require context.
            raise PatchError(f"hunk {idx} has no context to anchor the change")
        if search not in content:
            raise PatchError(
                f"[NO-MATCH] hunk {idx} did not match the file; re-read the file and "
                "copy the exact surrounding lines as context."
            )
        content = content.replace(search, replace, 1)
    return content
