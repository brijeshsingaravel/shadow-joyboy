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

# Words that follow "I am a/an ..." without ever naming a job. Deliberately SHORT and about
# hedging or state, not a general stop-list: the aim is to drop "a bit tired" and "a mess right
# now" while keeping every real occupation, including ones nobody here would think to list.
_HEDGE_OPENER = re.compile(
    r"^(?:bit|little|mess|wreck|failure|fraud|joke|burden|lot|"
    r"tired|sad|happy|glad|sorry|fine|okay|ok|nobody|no one|nothing)\b",
    re.I,
)


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
    # "I work as ..." -- unambiguous, article optional.
    (
        re.compile(r"\bi work as (?:a |an )?([\w '.-]{3,40}?)(?: at | for |\.|,|$)", re.I),
        "fact",
        "user job",
        0.8,
    ),
    # "I am A/AN ..." -- THE ARTICLE IS REQUIRED, and that is the whole fix.
    #
    # This pattern used to accept a bare `i am`, and "I am" is overwhelmingly used for STATES,
    # not occupations. Found in production in the founder's first real conversation through the
    # website: "I am so glad you are here" was stored as `user job: so glad you are here`.
    #
    # The cosmetic version of that bug is Shadow believing someone's job is a greeting. The
    # version that matters is a crisis turn -- "i am not okay" becoming a durable fact about
    # who that person IS, surfaced back to them weeks later on an ordinary day, by an agent
    # whose entire promise is that it remembers.
    #
    # An occupation almost always takes an article ("I am a teacher", "I am an engineer");
    # a state almost never does ("I am tired", "I am not okay", "I am so glad"). Requiring it
    # keeps the real cases and drops the damaging ones. "I am a bit tired" still slips through,
    # which is why the stop-list below exists.
    (
        re.compile(r"\bi am (?:a|an) ([\w '.-]{3,40}?)(?: at | for |\.|,|$)", re.I),
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
        # THE ARTICLE IS NOT ENOUGH ON ITS OWN. "I am a bit tired" and "I am a mess right now"
        # both take one, and neither is an occupation. These openers are hedges and
        # self-descriptions, never job titles, so a job candidate starting with one is dropped.
        # `content` arrives already prefixed as "<subject>: <value>", so the value is what has
        # to be tested -- anchoring on `content` would only ever see the word "user".
        if subject == "user job" and _HEDGE_OPENER.match(content.split(": ", 1)[-1]):
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
