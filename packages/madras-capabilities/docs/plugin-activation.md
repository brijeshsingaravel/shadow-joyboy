# Plugin-API activation (T5.2)

Two public plugin surfaces exist in Madras today. Both were genuinely unwired extension points
with no worked example and no test coverage before this row — [[Backend Spatial Map]] H5's own
finding. Both now have a real, tested, end-to-end example.

## Providers — `madras.plugins.providers`

A `Provider` implements one capability ABC (`MemoryProvider`, `ModelProvider`, `ContextProvider`,
`ImageProvider`) and registers through `ProviderRegistry` — the **only** integration point. Core
never imports a plugin directly; it resolves providers by `(kind, name)`.

```python
from madras.plugins.providers import MemoryProvider, ProviderRegistry

class MyProvider(MemoryProvider):
    name = "my_provider"
    async def remember(self, item): ...
    async def recall(self, query, *, limit=5): ...

registry = ProviderRegistry()
await registry.register(MyProvider())
provider = registry.get("memory", "my_provider")
```

**Worked example:** `madras.plugins.examples.echo_memory_provider.EchoMemoryProvider` — the
simplest possible real `MemoryProvider`, tested end-to-end in
`tests/test_plugins/test_echo_memory_provider_e2e.py` (registration, resolution by kind+name,
remember/recall, and the `start_all()`/`stop_all()` lifecycle hooks every provider gets called
through uniformly).

## Connectors — `madras.connectors.registry`

A `Connector` is a declarative record (name, app, auth type, scopes, whether it mutates state);
`ConnectorRegistry` holds the catalog and supports relevance search (never dump all 600+ into
context at once); `GovernedConnector` is the actual call path — JIT credential resolution,
approval-gating on mutating actions, and audit, all before the real `ConnectorBackend.execute()`
ever runs.

```python
from madras.connectors.registry import Connector, ConnectorRegistry, GovernedConnector

registry = ConnectorRegistry()
registry.register(Connector(name="my_app__action", app="my_app", mutating=True))
governed = GovernedConnector(backend=my_backend, registry=registry, approve=my_approval_fn)
result = await governed.call("my_app__action", "do_thing", {"arg": 1})
```

**Worked example:** `madras.connectors.examples.example_connector` — a non-mutating and a
mutating connector against a minimal real `ConnectorBackend` (no external SDK needed), tested
end-to-end in `tests/test_connectors/test_example_connector_e2e.py` (search, the non-mutating
call path, a denied mutating call, and an approved mutating call — all four real branches of
`GovernedConnector.call()`).

---
*T5.2, `tamil-and-backend-spatial.md`. → [[Agent OS]] · [[Backend Spatial Map]] · [[RFC-0002]].*
