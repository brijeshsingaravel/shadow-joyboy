"""Per-project user rules-file loader (E-B5 — project-as-context + user rules-file).

Generalizes how **Lighthouse** already loads Madras's own rules (`lighthouse_/auditor.py`
``_DOC_FILES = ("CLAUDE.md", "WORKSPACE_CONTEXT.md")``): a user-authored markdown file the
agent loads verbatim into its working context (the cacheable CONTEXT prompt tier). The user
edits it; the agent never rewrites it.

Recognized names, by precedence:
  1. ``.madras/rules.md`` — branded Madras override (wins if present)
  2. ``AGENTS.md``        — the open cross-tool standard (portable)
  3. ``CLAUDE.md``        — Lighthouse precedent (Madras itself uses it)

Returns ``""`` if none present. Bounded to ``MAX_CHARS`` so a huge file can't blow the prompt.
"""

from __future__ import annotations

from pathlib import Path

# precedence order: branded override, then open standard, then the Lighthouse precedent
RULES_FILENAMES: tuple[str, ...] = (".madras/rules.md", "AGENTS.md", "CLAUDE.md")
MAX_CHARS = 16_000


def find_rules_file(root: str | Path) -> Path | None:
    """Return the first recognized rules-file present under ``root`` (by precedence)."""
    base = Path(root)
    for name in RULES_FILENAMES:
        p = base / name
        if p.is_file():
            return p
    return None


def load_project_rules(root: str | Path, *, max_chars: int = MAX_CHARS) -> str:
    """Load the first present rules-file verbatim (bounded). Returns ``""`` if none/unreadable."""
    p = find_rules_file(root)
    if p is None:
        return ""
    try:
        text = p.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…(truncated)"
    return text
