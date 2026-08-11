"""Actions that cannot be taken back, and therefore require approval.

A LEAF MODULE ON PURPOSE. It imports nothing -- not even from elsewhere in `madras` -- so that
anything may depend on it without inheriting a subsystem.

WHY IT MOVED HERE (s66). This set used to live in `memory_manager/shadow_mode.py`, and
`security/permissions.py` imported it from there. Because `memory_manager/__init__.py` eagerly
imports the consolidator, the nightly job and the reflex extractor, that one import loaded 14
modules -- the whole nightly batch, the mind palace, its ledgers and the planning analyst -- to
obtain six strings. Measured in a fresh interpreter, not guessed.

The direction was also backwards. **"This action is irreversible, so ask first" is a security
statement**, not a memory one. Security is the foundation; it should not depend on a batch job
that runs at night, and a broken import in that job should not be able to take the permission
engine down with it.

`memory_manager.shadow_mode` now reads the set from here, and continues to re-export it, so every
existing caller keeps working. Guarded by tests/test_security/test_permissions_import_isolation.py.

TO ADD AN ACTION: put it here. Both the permission engine (which turns each into an ASK rule) and
Shadow Mode (which plans rather than executes them) will pick it up with no further change.
"""

from __future__ import annotations

IRREVERSIBLE_ACTIONS: frozenset[str] = frozenset(
    {
        "send_message",
        "send_email",
        "publish_content",
        "financial_transaction",
        "delete_data",
        "external_api_write",
    }
)
