"""The two rails the README promises: it asks before it acts, and it writes down what it did.

    "It asks first. Actions that can't be undone go through a permission check before they run,
     not after. That check is part of how the agent is built, not a setting you can forget to
     switch on."

    "It writes down what it did. Every action lands in an append-only audit log with no update
     or delete path."

Both are structural claims -- "by construction", "no update or delete path" -- and a structural
claim is exactly the kind that can rot without anyone noticing, because nothing visibly breaks
when it does. An agent that asks first still looks fine on the day the asking stops.

No database, no model, no network.
"""

from __future__ import annotations

import inspect
from typing import ClassVar

from madras.audit.writer import AuditLogWriter
from madras.security.irreversible import IRREVERSIBLE_ACTIONS
from madras.security.permissions import Decision, PermissionEngine


class TestItAsksBeforeDoingWhatCannotBeUndone:
    def test_every_irreversible_action_asks(self) -> None:
        """Not one of them may be silently allowed. This is the list, and the list is the rule."""
        engine = PermissionEngine()
        for action in sorted(IRREVERSIBLE_ACTIONS):
            # `toolset` is required: the engine decides on (tool, toolset, args) together, so a
            # read-only toolset can auto-allow without every tool needing its own rule.
            got = engine.check(tool=action, toolset="messaging", args={})
            assert got in (Decision.ASK, Decision.DENY), (
                f"`{action}` is irreversible and resolved to {got}. It must stop for a human. "
                "If this action has become safe, take it out of IRREVERSIBLE_ACTIONS on purpose "
                "-- do not let it drift out by accident."
            )

    def test_sending_a_message_is_on_the_list(self) -> None:
        """The specific one the whole repo is shaped around. `messaging` does not ship at all,
        and if it is ever added back this rule is the second line of defence."""
        assert "send_message" in IRREVERSIBLE_ACTIONS
        assert "send_email" in IRREVERSIBLE_ACTIONS

    def test_the_obvious_destructive_shell_commands_are_refused(self) -> None:
        engine = PermissionEngine()
        for cmd in ("rm -rf /", "sudo rm -rf /home"):
            got = engine.check(tool="terminal", toolset="shell", args={"command": cmd})
            assert got is not Decision.ALLOW, f"`{cmd}` was allowed outright"

    def test_the_list_is_not_empty(self) -> None:
        """The guard on the guards. An empty set would make every assertion above pass while
        protecting nothing -- the same shape as a benchmark scoring 0/8 because nothing ran."""
        assert len(IRREVERSIBLE_ACTIONS) >= 5


class TestTheRecordCannotBeRewritten:
    """`append` and read methods, and nothing else. An audit log you can edit is a diary, and a
    diary proves nothing to anyone who wasn't already inclined to believe you."""

    FORBIDDEN: ClassVar[tuple[str, ...]] = (
        "update", "delete", "remove", "edit", "drop", "truncate", "purge", "clear",
    )

    def test_the_writer_exposes_no_way_to_change_a_record(self) -> None:
        offenders = [
            name for name, _ in inspect.getmembers(AuditLogWriter, inspect.isfunction)
            if not name.startswith("_")
            and any(word in name.lower() for word in self.FORBIDDEN)
        ]
        assert not offenders, (
            f"AuditLogWriter grew a way to alter the record: {offenders}. The README promises "
            "append-only with no update or delete path."
        )

    def test_it_can_still_append_and_be_read(self) -> None:
        """The other half: append-only is only a virtue if it appends and can be verified."""
        for needed in ("append", "query", "verify_chain"):
            assert hasattr(AuditLogWriter, needed), f"AuditLogWriter lost `{needed}`"

    def test_no_raw_delete_or_update_sql_in_the_module(self) -> None:
        """Belt and braces. A method named `archive` could still run a DELETE, so check the
        source for the statements themselves rather than trusting the method names."""
        import madras.audit.writer as writer_module

        source = inspect.getsource(writer_module).lower()
        for statement in ("delete from", "update ", "truncate ", "drop table"):
            assert statement not in source, (
                f"`{statement.strip()}` appears in audit/writer.py. Whatever it is doing, an "
                "append-only log should not contain it."
            )
