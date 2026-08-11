"""Langfuse session-share (leg B) — map a session view to a Langfuse trace/session + the
public deep-link.

A session groups its work under a Langfuse `session_id`; each trajectory event becomes an
observation (tool calls → spans, others → events), and the deep-link points a viewer at the
Langfuse session replay (timeline + agent-graph). The push is **injectable** (`LangfusePusher`)
so the mapping + link are deterministic + offline-testable; the live push (`make_langfuse_pusher`,
wrapping the Langfuse SDK) runs when the `outkast-langfuse` host port is reachable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import quote

# Only these per-event keys go to the trace (no raw payloads / arg blobs).
_PUBLIC_OBS_KEYS = frozenset({"type", "name", "tool", "ok", "text"})


@dataclass
class LangfuseSession:
    session_id: str
    deep_link: str
    trace: dict[str, Any] = field(default_factory=dict[str, Any])


class LangfusePusher(Protocol):
    async def push(self, trace: dict[str, Any]) -> str | None: ...


def _first_user(view: dict[str, Any]) -> str:
    _raw = view.get("messages")
    msgs: list[dict[str, Any]] = (
        cast("list[dict[str, Any]]", _raw) if isinstance(_raw, list) else []
    )
    return next((str(m.get("content", "")) for m in msgs if m.get("role") == "user"), "")


def _last_answer(view: dict[str, Any]) -> str:
    _raw: list[Any] = view.get("messages") or []
    msgs: list[dict[str, Any]] = []
    for m in _raw:
        if isinstance(m, dict):
            m = cast("dict[str, Any]", m)
            if m.get("role") == "assistant":
                msgs.append(m)
    return str(msgs[-1].get("content", "")) if msgs else ""


def build_langfuse_trace(view: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    """Map a session view → a Langfuse trace payload (session-grouped, with observations)."""
    sid = session_id or str(view.get("session_id", ""))
    _raw_events = view.get("events")
    events: list[Any] = cast("list[Any]", _raw_events) if isinstance(_raw_events, list) else []
    observations: list[dict[str, Any]] = []
    for e in events:
        if not isinstance(e, dict):
            continue
        e = cast("dict[str, Any]", e)
        observations.append(
            {
                "kind": "span" if e.get("type") in ("tool_call", "tool_result") else "event",
                "name": str(e.get("name") or e.get("tool") or e.get("type") or "event"),
                "metadata": {k: e[k] for k in e if k in _PUBLIC_OBS_KEYS},
            }
        )
    return {
        "name": f"session:{view.get('agent', 'agent')}",
        "session_id": sid,
        "user_id": str(view.get("agent", "")),
        "input": _first_user(view),
        "output": _last_answer(view),
        "metadata": {
            "cost_usd": view.get("cost_usd", 0.0),
            "summary": str(view.get("summary", "")),
        },
        "observations": observations,
    }


def langfuse_session_url(host: str, project_id: str, session_id: str) -> str:
    """The public deep-link to a Langfuse session replay."""
    return f"{host.rstrip('/')}/project/{project_id}/sessions/{quote(str(session_id), safe='')}"


async def push_session(
    view: dict[str, Any],
    *,
    pusher: LangfusePusher,
    host: str,
    project_id: str,
    session_id: str | None = None,
) -> LangfuseSession:
    """Build the trace, push it via the injected pusher, and return the session + deep-link."""
    trace = build_langfuse_trace(view, session_id=session_id)
    await pusher.push(trace)
    sid = trace["session_id"]
    return LangfuseSession(
        session_id=sid,
        deep_link=langfuse_session_url(host, project_id, sid),
        trace=trace,
    )


def make_langfuse_pusher(*, public_key: str, secret_key: str, host: str) -> LangfusePusher:
    """The live pusher — wraps the Langfuse SDK. DEFERRED: only runs when the host is reachable.
    Lazily imports `langfuse` so offline use never needs the package."""
    try:
        from langfuse import Langfuse as _Langfuse  # type: ignore[reportMissingTypeStubs]
    except ImportError as exc:  # pragma: no cover - exercised only on the live path
        raise RuntimeError("the 'langfuse' package is required for the live pusher") from exc

    Langfuse: Any = _Langfuse
    client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    class _SDKPusher:
        async def push(self, trace: dict[str, Any]) -> str | None:  # pragma: no cover - live
            t = client.trace(
                name=trace["name"],
                session_id=trace["session_id"],
                user_id=trace["user_id"],
                input=trace["input"],
                output=trace["output"],
                metadata=trace["metadata"],
            )
            for obs in trace["observations"]:
                t.span(name=obs["name"], metadata=obs["metadata"])
            client.flush()
            return getattr(t, "id", None)

    return _SDKPusher()
