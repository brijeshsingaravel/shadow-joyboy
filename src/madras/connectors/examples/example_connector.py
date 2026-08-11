"""ExampleConnectorBackend -- the T5.2 worked example proving `GovernedConnector` end-to-end.

A trivial `ConnectorBackend` (no real external app) so the whole call path -- search, JIT
credential resolution, approval-gating on a mutating action, audit -- is provable without needing
a live ACI.dev instance. Register one non-mutating and one mutating connector to exercise both
branches of `GovernedConnector.call()`.
"""

from __future__ import annotations

from typing import Any

from madras.connectors.registry import Connector, ConnectorRegistry

ECHO_CONNECTOR = Connector(
    name="example__echo",
    app="example",
    description="Echoes back its input -- the worked example, no real external app.",
    auth_type="none",
    mutating=False,
)

WRITE_CONNECTOR = Connector(
    name="example__write",
    app="example",
    description="A mutating example action, to exercise the approval-gate branch.",
    auth_type="none",
    mutating=True,
)


def build_example_registry() -> ConnectorRegistry:
    registry = ConnectorRegistry()
    registry.register(ECHO_CONNECTOR)
    registry.register(WRITE_CONNECTOR)
    return registry


class ExampleConnectorBackend:
    """The minimal real implementation of the `ConnectorBackend` protocol -- no SDK, no network."""

    async def execute(
        self, connector: Connector, action: str, args: dict[str, Any], cred: str | None
    ) -> Any:
        return {"connector": connector.name, "action": action, "args": args, "cred": cred}


__all__ = ["ECHO_CONNECTOR", "WRITE_CONNECTOR", "ExampleConnectorBackend", "build_example_registry"]
