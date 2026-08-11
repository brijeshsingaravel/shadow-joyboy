"""Provider health / uptime weighting (row 98).

B35 + the fallback chain just stop on a 429; this tracks each provider's recent health and weights
model selection toward providers with no recent outage (OpenRouter prioritizes providers with no
outage in the last ~30s). A rolling window of `(timestamp, ok)` events per provider yields an uptime
fraction; the routing weight is `uptime**2 / (cost + eps)` — the inverse-square cost x uptime
weighting (square the uptime so a flapper is punished hard, inverse the cost so cheap wins).

Refines the row-97 auto-router score + the row-94 fallback ORDER, and is no-hammer-aligned: a
provider that just failed is naturally deprioritized (we route elsewhere) and recovers once its
failures age out of the window. `now` is injected (no wall clock), matching the memory convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from madras.llm.model_catalog import ModelInfo

_EPS = 1e-6


@dataclass
class ProviderHealth:
    window_s: float = 30.0
    _events: dict[str, list[tuple[float, bool]]] = field(
        default_factory=dict[str, list[tuple[float, bool]]]
    )

    def record(self, provider: str, ok: bool, *, now: float) -> None:
        events = self._events.setdefault(provider, [])
        events.append((now, ok))
        self._prune(provider, now=now)

    def _prune(self, provider: str, *, now: float) -> None:
        cutoff = now - self.window_s
        kept = [(t, ok) for (t, ok) in self._events.get(provider, []) if t >= cutoff]
        self._events[provider] = kept

    def uptime(self, provider: str, *, now: float) -> float:
        """Fraction of OK events in the window. No events -> 1.0 (healthy until proven)."""
        self._prune(provider, now=now)
        events = self._events.get(provider, [])
        if not events:
            return 1.0
        return sum(1 for _, ok in events if ok) / len(events)

    def recent_failure(self, provider: str, *, now: float) -> bool:
        self._prune(provider, now=now)
        return any(not ok for _, ok in self._events.get(provider, []))

    def weight(self, provider: str, cost: float, *, now: float) -> float:
        """Routing weight: uptime**2 / (cost + eps). Higher = preferred."""
        up = self.uptime(provider, now=now)
        return (up * up) / (max(cost, 0.0) + _EPS)

    def rank_models(self, models: list[ModelInfo], *, now: float) -> list[ModelInfo]:
        """Order models by health weight (healthy + cheap first); stable for ties."""
        return sorted(
            models,
            key=lambda m: self.weight(m.provider, m.input_cost, now=now),
            reverse=True,
        )
