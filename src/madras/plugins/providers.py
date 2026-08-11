"""Pluggable provider ABCs + registry — the marketplace architecture contract.

A plugin implements one of the provider ABCs (memory / model / context / image) and registers
through the `ProviderRegistry` — the ONLY integration point. Core never imports a plugin and a
plugin never edits core ("plugins never touch core"): core resolves capabilities via the registry
by (kind, name). Lifecycle hooks (`on_register`/`on_start`/`on_stop`) let a plugin set up + tear
down its own resources. Pairs with the [[Marketplace Manifest]] (which declares the plugin).
"""

from __future__ import annotations

import abc
from typing import Any


class Provider(abc.ABC):  # noqa: B024 — base; concrete capability ABCs add abstract methods
    """Base for every plugin provider. Subclasses set `kind` + `name` and implement their ABC."""

    kind: str = ""
    name: str = ""

    async def on_register(self) -> None:  # noqa: B027 — optional lifecycle hook
        """Called once when registered (override to set up resources)."""

    async def on_start(self) -> None:  # noqa: B027 — optional lifecycle hook
        """Called when the host starts (override to open connections)."""

    async def on_stop(self) -> None:  # noqa: B027 — optional lifecycle hook
        """Called on shutdown (override to release resources)."""


class MemoryProvider(Provider):
    kind = "memory"

    @abc.abstractmethod
    async def remember(self, item: Any) -> None: ...

    @abc.abstractmethod
    async def recall(self, query: str, *, limit: int = 5) -> list[Any]: ...


class ModelProvider(Provider):
    kind = "model"

    @abc.abstractmethod
    async def complete(self, prompt: str, **kwargs: Any) -> str: ...


class ContextProvider(Provider):
    kind = "context"

    @abc.abstractmethod
    async def fetch(self, query: str) -> list[Any]: ...


class ImageProvider(Provider):
    kind = "image"

    @abc.abstractmethod
    async def generate(self, prompt: str, **kwargs: Any) -> Any: ...


_KIND_ABC: dict[str, type[Provider]] = {
    "memory": MemoryProvider,
    "model": ModelProvider,
    "context": ContextProvider,
    "image": ImageProvider,
}


class ProviderRegistry:
    """The single integration point. Validates each plugin against its ABC + uniqueness; core
    resolves providers here by (kind, name) without ever importing the plugin."""

    def __init__(self) -> None:
        self._by_kind: dict[str, dict[str, Provider]] = {}

    async def register(self, provider: Provider) -> None:
        kind = provider.kind
        if kind not in _KIND_ABC:
            raise ValueError(f"unknown provider kind '{kind}'")
        if not isinstance(provider, _KIND_ABC[kind]):
            raise TypeError(f"provider does not implement the {kind} ABC")
        if not provider.name:
            raise ValueError("provider must declare a name")
        bucket = self._by_kind.setdefault(kind, {})
        if provider.name in bucket:
            raise ValueError(f"duplicate {kind} provider '{provider.name}'")
        bucket[provider.name] = provider
        await provider.on_register()

    def get(self, kind: str, name: str) -> Provider:
        return self._by_kind[kind][name]

    def list(self, kind: str | None = None) -> list[Provider]:
        if kind is not None:
            return list(self._by_kind.get(kind, {}).values())
        return [p for bucket in self._by_kind.values() for p in bucket.values()]

    async def start_all(self) -> None:
        for p in self.list():
            await p.on_start()

    async def stop_all(self) -> None:
        for p in self.list():
            await p.on_stop()
