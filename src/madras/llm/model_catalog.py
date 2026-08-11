"""Model catalog — per-model metadata grounding routing + the leaderboard.

OpenRouter's `GET /api/v1/models` is the canonical source (Madras already routes through it),
richer than models.dev: `context_length`, `pricing`, `architecture` modalities, and
`supported_parameters` (tools/structured/reasoning). `from_openrouter()` maps an entry to a
`ModelInfo`; `ModelCatalog` indexes them and answers capability/cost/context queries the task router
([[Task Model Routing]]) and the Proving-Ground leaderboard consume.

The catalog ships a small FREE-FLEET seed (offline, zero-cost). `sync_openrouter()` merges a fetched
`/models` list — `fetch_openrouter_models()` does a single public GET (opt-in, never auto-run; the
no-API-hammering rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    provider: str = ""
    context_window: int = 0
    input_cost: float = 0.0  # USD per input token
    output_cost: float = 0.0  # USD per output token
    input_modalities: tuple[str, ...] = ("text",)
    output_modalities: tuple[str, ...] = ("text",)
    tool_call: bool = False
    structured_output: bool = False
    reasoning: bool = False
    free: bool = False

    def supports(self, modality: str) -> bool:
        return modality in self.input_modalities


def from_openrouter(entry: dict[str, Any]) -> ModelInfo:
    """Map one OpenRouter `/api/v1/models` entry to a ModelInfo."""
    arch: dict[str, Any] = entry.get("architecture") or {}
    pricing: dict[str, Any] = entry.get("pricing") or {}
    params = set(entry.get("supported_parameters") or [])
    model_id = str(entry.get("id", ""))
    provider = model_id.split("/", 1)[0] if "/" in model_id else ""
    return ModelInfo(
        id=model_id,
        name=str(entry.get("name", "")),
        provider=provider,
        context_window=int(entry.get("context_length") or 0),
        input_cost=_to_float(pricing.get("prompt")),
        output_cost=_to_float(pricing.get("completion")),
        input_modalities=tuple(arch.get("input_modalities") or ("text",)),
        output_modalities=tuple(arch.get("output_modalities") or ("text",)),
        tool_call="tools" in params or "tool_choice" in params,
        structured_output="structured_outputs" in params,
        reasoning="reasoning" in params or bool(entry.get("reasoning")),
        free=model_id.endswith(":free") or _to_float(pricing.get("prompt")) == 0.0,
    )


# Small offline seed for the LiteLLM free fleet (cost 0; overridable via sync).
_FREE_FLEET_SEED: tuple[ModelInfo, ...] = (
    ModelInfo("kimi-k2", "Kimi K2", "moonshot", 131072, 0.0, 0.0, tool_call=True, free=True),
    ModelInfo(
        "gemini-flash",
        "Gemini Flash",
        "google",
        1048576,
        0.0,
        0.0,
        input_modalities=("text", "image"),
        tool_call=True,
        structured_output=True,
        free=True,
    ),
    ModelInfo("llama-70b", "Llama 3 70B", "meta", 131072, 0.0, 0.0, tool_call=True, free=True),
    ModelInfo(
        "deepseek",
        "DeepSeek R1",
        "deepseek",
        65536,
        0.0,
        0.0,
        tool_call=True,
        reasoning=True,
        free=True,
    ),
)


@dataclass
class ModelCatalog:
    _by_id: dict[str, ModelInfo] = field(default_factory=dict[str, ModelInfo])

    @classmethod
    def with_free_fleet(cls) -> ModelCatalog:
        cat = cls()
        for info in _FREE_FLEET_SEED:
            cat.add(info)
        return cat

    def add(self, info: ModelInfo) -> None:
        self._by_id[info.id] = info

    def get(self, model_id: str) -> ModelInfo | None:
        return self._by_id.get(model_id)

    def ids(self) -> list[str]:
        return sorted(self._by_id)

    def supports(self, model_id: str, modality: str) -> bool:
        info = self._by_id.get(model_id)
        return bool(info and info.supports(modality))

    def sync_openrouter(self, entries: list[dict[str, Any]]) -> int:
        """Merge a fetched OpenRouter `/models` list; returns the number added/updated."""
        n = 0
        for entry in entries:
            info = from_openrouter(entry)
            if info.id:
                self._by_id[info.id] = info
                n += 1
        return n

    def filter(
        self,
        *,
        tool_call: bool | None = None,
        structured_output: bool | None = None,
        reasoning: bool | None = None,
        free: bool | None = None,
        min_context: int | None = None,
        modality: str | None = None,
        max_input_cost: float | None = None,
    ) -> list[ModelInfo]:
        out: list[ModelInfo] = []
        for info in self._by_id.values():
            if tool_call is not None and info.tool_call != tool_call:
                continue
            if structured_output is not None and info.structured_output != structured_output:
                continue
            if reasoning is not None and info.reasoning != reasoning:
                continue
            if free is not None and info.free != free:
                continue
            if min_context is not None and info.context_window < min_context:
                continue
            if modality is not None and not info.supports(modality):
                continue
            if max_input_cost is not None and info.input_cost > max_input_cost:
                continue
            out.append(info)
        return sorted(out, key=lambda m: m.id)


async def fetch_openrouter_models(
    *,
    base_url: str = OPENROUTER_MODELS_URL,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """Single public GET of OpenRouter's model list (opt-in; never auto-run)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(base_url)
        resp.raise_for_status()
        return resp.json().get("data", [])
