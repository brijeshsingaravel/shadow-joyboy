"""CodeAct — executable-code-as-action execution mode (sandbox-RPC bridge).

See plans/2026-06-21-codeact-sandbox-rpc.md. The agent's acts are Python run in the sandbox;
the prelude exposes the agent's GOVERNED tools as functions that call back here via ToolBridge,
preserving the rank-gate + eval + audit + approval of a textual tool-call.
"""

from madras.codeact.rpc_server import BridgeResponse, ToolBridge

__all__ = ["BridgeResponse", "ToolBridge"]
