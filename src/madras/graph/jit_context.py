"""JIT context: expand @file <path> and @url <url> references into injected context blocks.

Referenced content enters the window only when explicitly referenced, so we never carry
every file/page in the prompt — only what the current turn actually needs.

Security: @file is workspace-scoped via safe_resolve (ASI02 path-security boundary).
          <retrieved> fences mark injected content as DATA, not instructions (ASI02).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from madras.tools.builtin.files import safe_resolve

_FILE_RE = re.compile(r"@file\s+(\S+)")
_URL_RE = re.compile(r"@url\s+(\S+)")

UrlFetcher = Callable[[str], Awaitable[str]]


async def expand_references(
    text: str,
    *,
    url_fetcher: UrlFetcher | None = None,
    max_chars: int = 4000,
) -> tuple[str, list[str]]:
    """Return (text, injected_blocks).

    Each @file and @url reference in *text* is expanded into an injected context block.
    The original text is returned unchanged; callers append the blocks to the context
    window as needed (wiring is M2D-T4).

    Args:
        text: The message text, potentially containing @file and @url references.
        url_fetcher: Optional async callable (url -> str). If None, @url refs produce
                     "fetch unavailable" blocks.
        max_chars: Maximum characters to include from each retrieved resource.

    Returns:
        (text, blocks) where blocks is a list of <retrieved>...</retrieved> strings or
        error annotations.
    """
    blocks: list[str] = []

    for m in _FILE_RE.finditer(text):
        rel = m.group(1)
        target = safe_resolve(rel)
        if target is None or not target.is_file():
            blocks.append(f"[@file {rel}: not found in workspace]")
        else:
            try:
                content = target.read_text(encoding="utf-8", errors="replace")[:max_chars]
                blocks.append(f"<retrieved file={rel}>\n{content}\n</retrieved>")
            except Exception as exc:
                blocks.append(f"[@file {rel}: read error {type(exc).__name__}]")

    for m in _URL_RE.finditer(text):
        url = m.group(1)
        if url_fetcher is None:
            blocks.append(f"[@url {url}: fetch unavailable]")
        else:
            try:
                fetched = (await url_fetcher(url))[:max_chars]
                blocks.append(f"<retrieved url={url}>\n{fetched}\n</retrieved>")
            except Exception as exc:
                blocks.append(f"[@url {url}: fetch error {type(exc).__name__}]")

    return text, blocks
