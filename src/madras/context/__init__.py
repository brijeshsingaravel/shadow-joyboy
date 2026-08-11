"""Context-engineering helpers (W4·B4): action-space masking + filesystem-as-context.

Also exposes trajectory/context compression (B35-row36): `compress_trajectory`.
"""

from madras.context.compress import CompressionStats, compress_trajectory

__all__ = ["CompressionStats", "compress_trajectory"]
