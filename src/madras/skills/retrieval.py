"""Skill retrieval with progressive disclosure.

L0 (name+description) for every active skill is cheap to keep in the prompt so the
agent knows what's available. The full body (L1) is injected only for skills whose
name/description tokens match the current task — keeping the window lean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from madras.skills.format import Skill

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "of",
        "and",
        "or",
        "for",
        "how",
        "do",
        "with",
        "in",
        "on",
        "is",
        "it",
        "this",
        "that",
        "your",
        "you",
    }
)


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def _match_score(skill: Skill, query_tokens: set[str]) -> int:
    skill_tokens = _tokens(skill.name.replace("-", " ") + " " + skill.description)
    return len(skill_tokens & query_tokens)


@dataclass
class RetrievedSkills:
    l0_lines: list[str]  # metadata for ALL active skills
    full_bodies: list[str]  # "## Skill: <name>\n<body>" for matched skills
    matched_names: list[str]


async def retrieve_skills(
    store: Any,
    *,
    project: str,
    user_input: str,
    max_full: int = 2,
    library_project: str | None = "library",
    max_library: int = 3,
) -> RetrievedSkills:
    """Curated project skills (L0 for all + matched bodies) PLUS on-demand matches from the
    shared `library` (the harvested/ingested skills) — searched by relevance so only the
    relevant few load, never all N. This is what makes the whole ingested library usable to
    an agent without bloating context."""
    try:
        active: list[Skill] = await store.list_active(project=project)
    except Exception:
        active = []
    l0 = [s.l0() for s in active]
    q = _tokens(user_input)
    scored = sorted(
        ((_match_score(s, q), s) for s in active),
        key=lambda t: t[0],
        reverse=True,
    )
    matched = [s for score, s in scored if score > 0][:max_full]
    full = [f"## Skill: {s.name}\n{s.body}" for s in matched]
    names = [s.name for s in matched]

    # Shared library: search-on-demand (bodies of the top matches only — no L0 dump).
    if library_project and q:
        try:
            lib: list[Skill] = await store.search_active(
                project=library_project, terms=[f"%{t}%" for t in q], limit=max_library
            )
        except Exception:
            lib = []
        seen = set(names)
        for s in lib:
            if s.name in seen:
                continue
            full.append(f"## Skill: {s.name} (library)\n{s.body}")
            names.append(s.name)

    return RetrievedSkills(l0_lines=l0, full_bodies=full, matched_names=names)


def skills_context_block(retrieved: RetrievedSkills) -> str:
    """Assemble the skills text for the prompt's context tier (L0 list + matched bodies)."""
    parts: list[str] = []
    if retrieved.l0_lines:
        parts.append("Available skills:\n" + "\n".join(retrieved.l0_lines))
    if retrieved.full_bodies:
        parts.append("Relevant skill details:\n" + "\n\n".join(retrieved.full_bodies))
    return "\n\n".join(parts)
