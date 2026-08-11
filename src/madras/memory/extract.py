"""Salient memory extraction (Step 2) — pull ATOMIC, durable facts from a turn.

Mem0's core insight: don't store raw conversation dumps — extract the few atomic,
reusable statements (identity, preferences, stable facts, explicit "remember this").
This is the deterministic extractor (pattern-based, fully testable); a richer LLM
extractor can layer on nightly via the Memory Manager. Each candidate becomes a
(kind, subject, content) the fabric can store, dedupe, and supersede.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAX = 240  # cap a candidate's length — atomic, not a paragraph


@dataclass
class Candidate:
    kind: str  # fact | preference
    subject: str  # the entity/topic (drives contradiction)
    content: str
    salience: float = 0.6  # Mem0 importance in [0,1]: how much this matters (drives confidence
    #                        + eviction priority — "how much it matters", not just recency)


# (regex, kind, subject, salience) — first matching group becomes the value. Salience encodes
# durability: identity > location/job > tools > preferences; explicit directives are maximal.
_PATTERNS: list[tuple[re.Pattern[str], str, str, float]] = [
    (re.compile(r"\bmy name is ([A-Z][\w'-]+(?: [A-Z][\w'-]+)?)", re.I), "fact", "user name", 0.9),
    (re.compile(r"\b(?:i am|i'm|im) ([A-Z][a-z]+)(?:\.|,|\s+and\b|$)"), "fact", "user name", 0.85),
    (re.compile(r"\bcall me ([A-Z][\w'-]+)", re.I), "fact", "user name", 0.9),
    (re.compile(r"\bi (?:live|am based) in ([\w '.-]{2,40})", re.I), "fact", "user location", 0.8),
    (
        re.compile(r"\bi (?:work as|am) (?:a |an )?([\w '.-]{3,40}?)(?: at | for |\.|,|$)", re.I),
        "fact",
        "user job",
        0.8,
    ),
    (
        re.compile(r"\bmy (?:job|role|title) is (?:a |an )?([\w '.-]{3,40})", re.I),
        "fact",
        "user job",
        0.8,
    ),
    (
        re.compile(r"\bi (?:prefer|like|love|enjoy|favou?r) ([\w '.,-]{2,80})", re.I),
        "preference",
        "user preference",
        0.6,
    ),
    (
        re.compile(r"\bi (?:hate|dislike|avoid|don'?t like) ([\w '.,-]{2,80})", re.I),
        "preference",
        "user preference",
        0.6,
    ),
    (
        re.compile(r"\bi (?:use|work with|am using) ([\w '.,+#-]{2,60})", re.I),
        "preference",
        "user tools",
        0.65,
    ),
]

# explicit memory directives → store the trailing clause verbatim as a fact
_DIRECTIVE = re.compile(
    r"\b(?:remember|note|keep in mind|for (?:future )?reference|don'?t forget)\b"
    r"[:,]?\s+(?:that\s+)?(.+)",
    re.I,
)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" .,'\"").strip()[:_MAX]


def extract_salient(text: str) -> list[Candidate]:
    """Pull atomic memorable statements from a (user) turn. Deterministic; may return []."""
    out: list[Candidate] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, subject: str, content: str, salience: float) -> None:
        content = _clean(content)
        if len(content) < 2:
            return
        key = (subject, content.lower())
        if key in seen:
            return
        seen.add(key)
        out.append(Candidate(kind=kind, subject=subject, content=content, salience=salience))

    for line in re.split(r"(?<=[.!?])\s+|\n", text or ""):
        m = _DIRECTIVE.search(line)
        if m:
            # explicit "remember this" — maximal salience (Mem0: explicit user signal)
            _add("fact", "directive", m.group(1), 1.0)
            continue
        for pat, kind, subject, salience in _PATTERNS:
            mm = pat.search(line)
            if mm:
                val = mm.group(1)
                # store the full natural statement as content, keyed by the subject
                _add(kind, subject, f"{subject.replace('user ', 'user ')}: {_clean(val)}", salience)
    return out
