"""Proactive suggestion engine — the planned Proactive Suggestions capability.

Providers propose context-surfaced suggestions; the engine gates them by **consent**
(privacy-first DENY by default — both the suggestion's category AND its source/connected-app
must be opted in), personalizes by **role**, dedups, ranks, and returns the top-N. Pure — no
infra, no LLM; an LLM provider can be registered like any other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Suggestion:
    id: str
    title: str
    category: str  # the kind of nudge (e.g. 'security', 'followup', 'marketing')
    source: str  # where it came from (e.g. 'gmail', 'calendar', 'engine')
    body: str = ""
    roles: tuple[str, ...] = ()  # which roles it applies to; () = all roles
    rationale: str = ""
    confidence: float = 0.5
    score: float = 0.0  # ranking key; falls back to confidence when 0

    @property
    def rank(self) -> float:
        return self.score or self.confidence


@dataclass
class ConsentPolicy:
    """Privacy-first consent: a suggestion surfaces only if BOTH its category and its source
    are opted in (or allow_all for dev). Default = deny everything."""

    categories: set[str] = field(default_factory=set[str])
    sources: set[str] = field(default_factory=set[str])
    allow_all: bool = False

    def grant_category(self, category: str) -> None:
        self.categories.add(category)

    def grant_source(self, source: str) -> None:
        self.sources.add(source)

    def allows(self, s: Suggestion) -> bool:
        if self.allow_all:
            return True
        return s.category in self.categories and s.source in self.sources


# A provider takes the current context and proposes candidate suggestions.
Provider = Callable[[dict[str, Any]], list[Suggestion]]


class SuggestionEngine:
    def __init__(self) -> None:
        self._providers: list[Provider] = []

    def register(self, provider: Provider) -> None:
        self._providers.append(provider)

    def surface(
        self,
        context: dict[str, Any],
        *,
        consent: ConsentPolicy,
        role: str | None = None,
        limit: int = 5,
    ) -> list[Suggestion]:
        """Collect from providers, gate by consent, personalize by role, dedup, rank, top-N."""
        candidates: list[Suggestion] = []
        for provider in self._providers:
            try:
                candidates.extend(provider(context) or [])
            except Exception:
                continue

        gated = [s for s in candidates if consent.allows(s)]
        if role is not None:
            gated = [s for s in gated if not s.roles or role in s.roles]

        # dedup by id, keeping the highest-ranked instance
        best: dict[str, Suggestion] = {}
        for s in gated:
            if s.id not in best or s.rank > best[s.id].rank:
                best[s.id] = s

        ranked = sorted(best.values(), key=lambda s: s.rank, reverse=True)
        return ranked[:limit]
