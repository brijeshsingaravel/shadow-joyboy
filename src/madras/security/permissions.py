"""Claude-Code-style tool permissions: modes + allow/deny/ask rules + a store.

Decision per tool call: ALLOW (run), DENY (refuse), ASK (human approval, via the
LangGraph interrupt wired in M2C-T4). Rules match tool name + an fnmatch glob on a
canonical arg string. Modes mirror Claude Code: default / plan / accept-edits / bypass.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from typing import Any

import asyncpg

from madras.security.irreversible import IRREVERSIBLE_ACTIONS


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionMode(str, Enum):
    DEFAULT = "default"  # reads auto-allow; dangerous -> ask
    PLAN = "plan"  # never execute mutations (read-only allowed)
    ACCEPT_EDITS = "accept-edits"  # auto-allow file_write in workspace
    BYPASS = "bypass"  # founder dev mode: allow everything


READ_ONLY_TOOLSETS = frozenset({"web", "file", "search", "vision"})
DANGEROUS_TOOLSETS = frozenset({"shell", "code", "file_write", "browser"})
# Governed meta-action toolsets that don't need per-call approval: delegating to a
# subagent and writing a note are themselves fully governed (the subagent's own tools
# pass through this same engine + the circuit breaker) and are non-destructive.
AUTO_ALLOW_TOOLSETS = READ_ONLY_TOOLSETS | frozenset(
    # 'schedule' is auto-allowed: creating a schedule is non-destructive — the scheduled
    # RUN goes through the full governed loop (rank/permissions/eval/audit) when it fires.
    # 'messaging' is auto-allowed at the permission layer because send_message SELF-GATES:
    # on-behalf sends draft-then-require the user's explicit approval (clarify) before any
    # dispatch; self-notifications auto-send. One clean approval at the draft, not two.
    {"delegation", "memory", "planning", "image_gen", "tts", "clarify", "schedule", "messaging"}
)


@dataclass(frozen=True)
class PermissionRule:
    tool: str  # exact tool name, or "*" for any
    arg_pattern: str  # fnmatch glob against the canonical arg string ("*" = any)
    decision: Decision

    def matches(self, tool: str, arg_str: str) -> bool:
        if self.tool != "*" and self.tool != tool:
            return False
        return fnmatch.fnmatch(arg_str, self.arg_pattern)


def canonical_arg(tool: str, args: dict[str, Any]) -> str:
    """The string a rule's glob is matched against (the security-relevant arg)."""
    if tool == "terminal":
        return str(args.get("cmd", ""))
    if tool in ("file_write", "patch", "file_read"):
        return str(args.get("path", ""))
    if tool == "code_exec":
        return str(args.get("code", ""))[:120]
    # The crossing gates (Phase P). Names are literals rather than imports because `dsl.crossing`
    # imports THIS module -- importing back would be circular.
    #
    # Found by deploying (s61): both gates passed their security-relevant argument here believing
    # a rule could match on it, and both fell through to "" -- so every pattern except `*` matched
    # nothing. "Allow crossings from device-01" silently matched no origin, and because an
    # unmatched origin falls through to ASK (which the receiver refuses), the symptom looked like
    # correct deny-by-default rather than a dead rule.
    if tool == "crossing":
        return str(args.get("destination", ""))
    if tool == "crossing-receipt":
        return str(args.get("origin", ""))
    return ""


def default_rules() -> list[PermissionRule]:
    rules: list[PermissionRule] = [
        # Hard safety denials
        PermissionRule("terminal", "*rm -rf /*", Decision.DENY),
        PermissionRule("terminal", "*sudo *", Decision.DENY),
    ]
    # Irreversible action names from Shadow Mode -> ASK
    for a in sorted(IRREVERSIBLE_ACTIONS):
        rules.append(PermissionRule(a, "*", Decision.ASK))
    return rules


def approval_rule(tool: str, args: dict[str, Any], *, scope: str = "exact") -> PermissionRule:
    """Turn an approved call into a persistable ALLOW rule (Codex prefix_rule pattern).
    scope='exact' → only this exact arg auto-allows; scope='prefix' → the command family
    (first token) auto-allows. tool='terminal' cmd 'git status' + prefix → tool='terminal'
    arg_pattern='git*'."""
    arg = canonical_arg(tool, args)
    if not arg:
        pattern = "*"
    elif scope == "prefix":
        pattern = (arg.split()[0] + "*") if arg.split() else "*"
    else:
        pattern = arg  # exact (matched via fnmatch; literal command auto-allows)
    return PermissionRule(tool=tool, arg_pattern=pattern, decision=Decision.ALLOW)


async def remember_approval(
    store: PermissionStore, project: str, tool: str, args: dict[str, Any], *, scope: str = "exact"
) -> PermissionRule:
    """Persist an 'allow always' decision so the same call is never re-asked (the learn step)."""
    rule = approval_rule(tool, args, scope=scope)
    await store.add(project, rule)
    return rule


class PermissionEngine:
    def __init__(self, *, extra_rules: list[PermissionRule] | None = None) -> None:
        self._defaults = default_rules()
        self._extra = list(extra_rules or [])

    @classmethod
    async def from_store(
        cls,
        store: PermissionStore,
        *,
        project: str,
        extra_rules: list[PermissionRule] | None = None,
    ) -> PermissionEngine:
        """Build an engine pre-loaded with the project's persisted (learned) rules, so a
        previously-approved call auto-allows without re-prompting."""
        persisted = await store.load(project)
        return cls(extra_rules=[*(extra_rules or []), *persisted])

    def add_rule(self, rule: PermissionRule) -> None:
        """Add a learned rule to this live engine (in addition to persisting it)."""
        self._extra.append(rule)

    def check(
        self,
        *,
        tool: str,
        toolset: str,
        args: dict[str, Any],
        mode: PermissionMode = PermissionMode.DEFAULT,
        rules: list[PermissionRule] | None = None,
    ) -> Decision:
        arg_str = canonical_arg(tool, args)
        all_rules = self._defaults + self._extra + list(rules or [])

        # 1. Explicit DENY rule always wins.
        if any(r.decision is Decision.DENY and r.matches(tool, arg_str) for r in all_rules):
            return Decision.DENY
        # 2. Bypass mode: allow everything not explicitly denied.
        if mode is PermissionMode.BYPASS:
            return Decision.ALLOW
        # 3. Explicit ALLOW rule (user "allow always").
        if any(r.decision is Decision.ALLOW and r.matches(tool, arg_str) for r in all_rules):
            return Decision.ALLOW
        # 4. Plan mode: read-only allowed, mutations denied.
        if mode is PermissionMode.PLAN:
            return Decision.ALLOW if toolset in READ_ONLY_TOOLSETS else Decision.DENY
        # 5. Accept-edits mode: auto-allow workspace file writes.
        if mode is PermissionMode.ACCEPT_EDITS and toolset == "file_write":
            return Decision.ALLOW
        # 6. Explicit ASK rule.
        if any(r.decision is Decision.ASK and r.matches(tool, arg_str) for r in all_rules):
            return Decision.ASK
        # 7. Defaults by toolset.
        if toolset in AUTO_ALLOW_TOOLSETS:
            return Decision.ALLOW
        if toolset in DANGEROUS_TOOLSETS:
            return Decision.ASK
        return Decision.ASK


# ---- Postgres-backed store for persisted ("allow always") rules ----

# The schema lives in infra/migrations/0002_permissions.sql, which owns it -- the only
# migration that has ever touched this table. This module used to carry a byte-identical copy
# and execute it in `setup()`; that made the schema definable from two places and required
# DDL rights the app role must not have (D83).


class MissingPermissionsTable(RuntimeError):
    """The permissions table does not exist -- migrations have not been applied.

    Deliberately loud. This table is what `PermissionEngine` consults to decide what an agent may
    do; a caller that treated its absence as "no rules" would fall through to whatever the engine's
    default is, and a governance store that silently becomes empty is the worst possible failure
    for a governance store."""


class PermissionStore:
    def __init__(self, *, postgres_url: str) -> None:
        self._url = postgres_url
        self._table = "madras_tool_permissions"
        self._verified = False
        self._pool: asyncpg.Pool | None = None  # type: ignore[type-arg]

    async def _get_pool(self) -> asyncpg.Pool:  # type: ignore[type-arg]
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        return self._pool

    async def setup(self) -> None:
        """Verify the permissions table exists. Does NOT create it (s61, D83 step 5).

        Owned by `0002_permissions.sql`, verified byte-identical before removal and the only
        migration that has ever touched this table. Creating it here again blocked the RLS
        cutover -- the app role has no DDL, and `CREATE TABLE IF NOT EXISTS` is refused on
        privilege grounds even when the table exists.

        Raising rather than no-op'ing matters more here than anywhere else in this sweep: an
        absent permissions table must never be mistaken for an empty rule set.
        """
        if self._verified:
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", self._table)
        if not exists:
            raise MissingPermissionsTable(
                f"{self._table} does not exist -- apply infra/migrations "
                f"(0002_permissions.sql creates it)"
            )
        self._verified = True

    async def load(self, project: str) -> list[PermissionRule]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT tool, arg_pattern, decision FROM madras_tool_permissions WHERE project=$1",
                project,
            )
        return [PermissionRule(r["tool"], r["arg_pattern"], Decision(r["decision"])) for r in rows]

    async def add(self, project: str, rule: PermissionRule) -> int:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO madras_tool_permissions (project, tool, arg_pattern, decision) "
                "VALUES ($1,$2,$3,$4) RETURNING id",
                project,
                rule.tool,
                rule.arg_pattern,
                rule.decision.value,
            )
            assert row is not None, "INSERT ... RETURNING always returns a row"
            return int(row["id"])

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
