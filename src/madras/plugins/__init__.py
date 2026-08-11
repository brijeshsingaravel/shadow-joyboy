"""Plugin provider contract — ABCs + registry so marketplace plugins never touch core."""

from madras.plugins.providers import (
    ContextProvider,
    ImageProvider,
    MemoryProvider,
    ModelProvider,
    Provider,
    ProviderRegistry,
)

__all__ = [
    "ContextProvider",
    "ImageProvider",
    "MemoryProvider",
    "ModelProvider",
    "Provider",
    "ProviderRegistry",
]
