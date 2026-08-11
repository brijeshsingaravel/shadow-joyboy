"""Governed session sharing — opaque, revocable, read-only public links to a session.

`opencode share`, governed: a share mints an opaque `secrets` token (no session id leaked),
read-only scope, optional expiry, and is revocable. `redact_for_share` strips everything
non-public before a session is ever rendered for a viewer. The visual rendering lives in
`share_render.py`; the public route resolves a token here. Pure + deterministic (in-memory
store; a Postgres-backed one can implement the same surface).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, cast

# Fields safe to expose on a public share. Everything else is dropped by redaction.
_PUBLIC_VIEW_KEYS = frozenset(
    {
        "session_id",
        "agent",
        "summary",
        "started_at",
        "ended_at",
        "duration_secs",
        "cost_usd",
        "eval_signals",
        "events",
        "messages",
    }
)
# Within an event, only these are public (never raw payloads / internal ids / args blobs).
_PUBLIC_EVENT_KEYS = frozenset({"type", "name", "tool", "ok", "text", "ts"})


@dataclass
class ShareLink:
    token: str
    session_id: str
    scope: str = "read"
    created: float = 0.0
    expires_at: float | None = None
    revoked: bool = False

    @property
    def path(self) -> str:
        return f"/share/{self.token}"


def redact_for_share(view: dict[str, Any]) -> dict[str, Any]:
    """Keep only public fields of a session view; scrub each event to public keys."""
    out: dict[str, Any] = {k: view[k] for k in view if k in _PUBLIC_VIEW_KEYS}
    events: Any = out.get("events")
    if isinstance(events, list):
        redacted: list[dict[str, Any]] = []
        for e in cast("list[Any]", events):
            if isinstance(e, dict):
                event = cast("dict[str, Any]", e)
                redacted.append({k: event[k] for k in event if k in _PUBLIC_EVENT_KEYS})
        out["events"] = redacted
    return out


class SessionShareStore:
    def __init__(self) -> None:
        self._by_token: dict[str, ShareLink] = {}

    def create(
        self,
        session_id: str,
        *,
        scope: str = "read",
        now: float = 0.0,
        ttl_secs: float | None = None,
    ) -> ShareLink:
        token = secrets.token_urlsafe(16)
        link = ShareLink(
            token=token,
            session_id=session_id,
            scope=scope,
            created=now,
            expires_at=(now + ttl_secs) if ttl_secs is not None else None,
        )
        self._by_token[token] = link
        return link

    def get(self, token: str) -> ShareLink | None:
        return self._by_token.get(token)

    def resolve(self, token: str, *, now: float = 0.0) -> str | None:
        """Return the session id for a live token, or None if missing / revoked / expired."""
        link = self._by_token.get(token)
        if link is None or link.revoked:
            return None
        if link.expires_at is not None and now >= link.expires_at:
            return None
        return link.session_id

    def revoke(self, token: str) -> bool:
        link = self._by_token.get(token)
        if link is None:
            return False
        link.revoked = True
        return True

    def list_for_session(self, session_id: str) -> list[ShareLink]:
        return [link for link in self._by_token.values() if link.session_id == session_id]
