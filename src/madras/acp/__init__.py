"""ACP (Agent Client Protocol) — IDE-native agent surface + multi-API model adapters."""

from madras.acp.adapters import ADAPTERS, ModelApiAdapter, get_adapter
from madras.acp.protocol import AcpServer, acp_error, acp_result

__all__ = ["ADAPTERS", "AcpServer", "ModelApiAdapter", "acp_error", "acp_result", "get_adapter"]
