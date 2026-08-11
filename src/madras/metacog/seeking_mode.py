"""Knowledge-Seeking Engine's mode selector (Human-Aligned frame §8.1, row
knowledge-seeking-engine).

6 seeking modes (frame's own taxonomy): directed (a specific factual gap), exploratory
(open-ended browsing/curiosity), social (what do others think/say), experiential (learn by
trying), reflective (skeptical -- interrogate an existing belief), structured (build a
complete, organized picture). Deterministic keyword classification -- same idiom as
Resource Awareness's classify_urgency(), no LLM call for a cheap upstream decision.
"""

from __future__ import annotations

SEEKING_MODES = (
    "directed",
    "exploratory",
    "social",
    "experiential",
    "reflective",
    "structured",
)

_MARKERS: dict[str, tuple[str, ...]] = {
    "reflective": (
        "is it true",
        "really true",
        "actually",
        "double-check",
        "verify",
        "sure about",
        "skeptical",
        "challenge",
        "what would prove",
        "am i wrong",
        "play devil's advocate",
    ),
    "social": (
        "what do people think",
        "what does everyone",
        "consensus",
        "who says",
        "reviews",
        "opinions on",
        "community think",
    ),
    "experiential": (
        "try it",
        "let's experiment",
        "hands-on",
        "walk me through doing",
        "practice",
        "test it out",
        "let's attempt",
    ),
    "exploratory": (
        "tell me about",
        "explore",
        "browse",
        "anything interesting",
        "curious about",
        "what's out there",
        "survey of",
    ),
    "structured": (
        "comprehensive",
        "complete overview",
        "full picture",
        "organize",
        "structured summary",
        "everything about",
    ),
}


def classify_seeking_mode(question: str) -> str:
    """One of SEEKING_MODES; "directed" is the default (a specific factual question is the
    common case -- the other 5 modes are the marked exceptions)."""
    text = (question or "").lower()
    for mode in ("reflective", "social", "experiential", "exploratory", "structured"):
        if any(marker in text for marker in _MARKERS[mode]):
            return mode
    return "directed"
