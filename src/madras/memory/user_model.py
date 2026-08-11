"""User-model assembler (E-B7 — dialectical user-relationship modeling).

Assembles an evolving model of the user from CURRENT memory items: facts whose subject
starts with ``user`` (name/location/job — as ``extract.py`` tags them), all ``preference``
items, and ``relationship`` edges sourced from the user. The **dialectic is inherited** —
only non-superseded items are read, and the E6 drift-flag + supersession (``retrieval.py``)
refine the underlying facts, so the model self-corrects as the user contradicts/updates it.
No separate reconciliation pass is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from madras.memory.retrieval import MemoryItem, is_current

_USER_REL_SRC = ("user", "the user")


@dataclass
class UserModel:
    facts: list[str] = field(default_factory=list[str])
    preferences: list[str] = field(default_factory=list[str])
    relationships: list[str] = field(default_factory=list[str])
    trust: float | None = None  # row social-intelligence — set by the caller from
    # TrustTracker.score(); not derived from memory items (a live evidence signal,
    # not a durable fact), so build_user_model never touches this field.

    def is_empty(self) -> bool:
        return not (self.facts or self.preferences or self.relationships)


def _label(subject: str) -> str:
    """Strip the leading 'user ' from a subject for a clean label (else the subject)."""
    s = (subject or "").strip()
    return s[5:].strip() or s if s.lower().startswith("user ") else s


def build_user_model(items: list[MemoryItem], *, now: float) -> UserModel:
    """Assemble the user-model from currently-valid items (dialectic inherited)."""
    m = UserModel()
    for it in items:
        if not is_current(it, now):
            continue
        subj = (it.subject or "").strip().lower()
        if it.kind == "fact" and subj.startswith("user"):
            m.facts.append(f"{_label(it.subject)}: {it.content}".strip())
        elif it.kind == "preference":
            m.preferences.append(it.content.strip())
        elif it.kind == "relationship" and subj in _USER_REL_SRC:
            m.relationships.append(it.content.strip())
    return m


def render_user_model(model: UserModel) -> str:
    """Render a compact 'About the user' block; '' if the model is empty."""
    if model.is_empty():
        return ""
    lines = ["## About the user (durable; evolves as you learn more)"]
    lines.extend(f"- {f}" for f in model.facts)
    if model.preferences:
        lines.append("Preferences:")
        lines.extend(f"- {p}" for p in model.preferences)
    if model.relationships:
        lines.append("Relationships:")
        lines.extend(f"- {r}" for r in model.relationships)
    if model.trust is not None:
        lines.append(f"Trust level: {model.trust:.2f} (evidence-based, 0=low, 1=high)")
    return "\n".join(lines)
