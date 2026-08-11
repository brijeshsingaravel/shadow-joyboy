"""Render a (redacted) session view to a self-contained, shareable HTML page.

Shows both the trajectory (event timeline + eval + cost) and the conversation. No external
assets (inline CSS), and **every piece of model/user content is HTML-escaped** — a public
share must never surface untrusted text as markup (the eve security lesson). Pure: dict → str.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path
from typing import Any, cast

_EVAL_DIMS = (
    "task_completion",
    "tool_selection",
    "argument_correctness",
    "error_recovery",
    "clarification_quality",
    "confidence_calibration",
    "correction_absorption",
    "user_rating",
)

_CSS = "\n".join(
    [
        ":root{--bg:#0e1116;--card:#171b22;--ink:#e6e9ef;--mut:#9aa4b2;",
        "--ok:#3fb950;--no:#f85149;--ln:#2d333b}",
        "*{box-sizing:border-box}",
        "body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,sans-serif}",
        ".wrap{max-width:820px;margin:0 auto;padding:28px}",
        ".hd{border-bottom:1px solid var(--ln);padding-bottom:16px;margin-bottom:20px}",
        ".hd h1{margin:0 0 4px;font-size:20px}.hd .meta{color:var(--mut);font-size:13px}",
        ".card{background:var(--card);border:1px solid var(--ln);border-radius:10px;",
        "padding:16px;margin:16px 0}",
        ".card h2{margin:0 0 12px;font-size:13px;text-transform:uppercase;",
        "letter-spacing:.05em;color:var(--mut)}",
        ".ev{display:flex;gap:10px;padding:6px 0;border-left:2px solid var(--ln);",
        "padding-left:14px;margin-left:4px}",
        ".ev .t{color:var(--mut);min-width:120px;font-size:12px}",
        ".bar{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12px}",
        ".bar .lbl{min-width:170px;color:var(--mut)}",
        ".bar .track{flex:1;height:8px;background:#0b0e13;border-radius:4px;overflow:hidden}",
        ".bar .fill{height:100%;background:var(--ok)}",
        ".msg{padding:10px 12px;border-radius:8px;margin:8px 0;",
        "white-space:pre-wrap;word-wrap:break-word}",
        ".msg.user{background:#1b2330}",
        ".msg.assistant{background:#141a22;border:1px solid var(--ln)}",
        ".msg .role{font-size:11px;color:var(--mut);text-transform:uppercase;margin-bottom:4px}",
        ".pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;",
        "background:#0b0e13;color:var(--mut)}",
        ".foot{color:var(--mut);font-size:12px;text-align:center;margin-top:24px}",
    ]
)


def _eval_bars(signals: dict[str, Any]) -> str:
    rows: list[str] = []
    for dim in _EVAL_DIMS:
        if dim not in signals:
            continue
        v = signals[dim]
        pct = (
            100
            if v is True
            else 0
            if v is False
            else int(max(0.0, min(1.0, float(v))) * 100)
            if isinstance(v, (int, float))
            else 0
        )
        rows.append(
            f'<div class="bar"><span class="lbl">{escape(dim)}</span>'
            f'<span class="track"><span class="fill" style="width:{pct}%"></span></span>'
            f"<span>{pct}%</span></div>"
        )
    return "".join(rows) or '<div class="bar"><span class="lbl">no eval signals</span></div>'


def _timeline(events: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for e in events:
        etype = escape(str(e.get("type", "")))
        label = escape(str(e.get("name") or e.get("tool") or e.get("text") or ""))
        if len(label) > 120:
            label = label[:120] + "…"
        rows.append(f'<div class="ev"><span class="t">{etype}</span><span>{label}</span></div>')
    return "".join(rows) or '<div class="ev"><span class="t">—</span><span>no events</span></div>'


def _conversation(messages: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for m in messages:
        role = str(m.get("role", "")).lower()
        cls = "user" if role == "user" else "assistant"
        out.append(
            f'<div class="msg {cls}"><div class="role">{escape(role) or "—"}</div>'
            f"{escape(str(m.get('content', '')))}</div>"
        )
    return "".join(out) or '<div class="msg assistant">no messages</div>'


def render_session_html(view: dict[str, Any]) -> str:
    """Render a redacted session view → a self-contained HTML page (trajectory + conversation)."""
    agent = escape(str(view.get("agent", "Agent")))
    summary = escape(str(view.get("summary", "")))
    started = escape(str(view.get("started_at", "")))
    cost = view.get("cost_usd", 0.0)
    cost_str = f"${float(cost):.4f}" if isinstance(cost, (int, float)) else "—"
    _raw_events = view.get("events")
    events: list[dict[str, Any]] = (
        cast("list[dict[str, Any]]", _raw_events) if isinstance(_raw_events, list) else []
    )
    _raw_messages = view.get("messages")
    messages: list[dict[str, Any]] = (
        cast("list[dict[str, Any]]", _raw_messages) if isinstance(_raw_messages, list) else []
    )
    _raw_signals = view.get("eval_signals")
    signals: dict[str, Any] = (
        cast("dict[str, Any]", _raw_signals) if isinstance(_raw_signals, dict) else {}
    )

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{agent} — shared session</title><style>{_CSS}</style>"
        '</head><body><div class="wrap">'
        f'<div class="hd"><h1>{agent} <span class="pill">shared session</span></h1>'
        f'<div class="meta">{summary or "—"} · {started or "—"} · cost {cost_str}</div></div>'
        f'<div class="card"><h2>Trajectory</h2>{_timeline(events)}</div>'
        f'<div class="card"><h2>Evaluation</h2>{_eval_bars(signals)}</div>'
        f'<div class="card"><h2>Conversation</h2>{_conversation(messages)}</div>'
        '<div class="foot">Shared via Madras · read-only</div>'
        "</div></body></html>"
    )


# ---- vault-native markdown (leg A: render → a visual Obsidian note → Share Note) ----


def _mermaid_label(s: Any) -> str:
    """Safe Mermaid node label: strip chars that break the diagram, truncate."""
    return re.sub(r'["\n\[\]{}|<>]', " ", str(s)).strip()[:40] or "—"


def _mermaid_trajectory(events: list[dict[str, Any]]) -> str:
    if not events:
        return 'flowchart LR\n  e0["no events"]'
    nodes: list[str] = []
    edges: list[str] = []
    for i, e in enumerate(events):
        raw = f"{e.get('type', '')}: {e.get('name') or e.get('tool') or ''}".strip(": ")
        nodes.append(f'  e{i}["{_mermaid_label(raw)}"]')
        if i:
            edges.append(f"  e{i - 1} --> e{i}")
    return "flowchart LR\n" + "\n".join(nodes + edges)


def render_session_markdown(view: dict[str, Any]) -> str:
    """Render a redacted session view → an Obsidian note (Mermaid trajectory + eval table +
    conversation) — the visual unit leg A publishes via the Share Note plugin."""
    agent = str(view.get("agent", "Agent"))
    summary = str(view.get("summary", ""))
    _raw_events = view.get("events")
    events: list[dict[str, Any]] = (
        cast("list[dict[str, Any]]", _raw_events) if isinstance(_raw_events, list) else []
    )
    _raw_messages = view.get("messages")
    messages: list[dict[str, Any]] = (
        cast("list[dict[str, Any]]", _raw_messages) if isinstance(_raw_messages, list) else []
    )
    _raw_signals = view.get("eval_signals")
    signals: dict[str, Any] = (
        cast("dict[str, Any]", _raw_signals) if isinstance(_raw_signals, dict) else {}
    )
    cost = view.get("cost_usd", 0.0)

    fm = (
        "---\n"
        f"shared_session: {view.get('session_id', '')}\n"
        f"agent: {agent}\n"
        f"date: {view.get('started_at', '')}\n"
        f"cost_usd: {cost}\n"
        "tags: [shared-session]\n"
        "---\n"
    )
    out = [fm, f"# {agent} — shared session\n", f"> {summary}\n" if summary else ""]
    out.append("## Trajectory\n```mermaid\n" + _mermaid_trajectory(events) + "\n```\n")
    if signals:
        rows = "\n".join(f"| {k} | {v} |" for k, v in signals.items())
        out.append("## Evaluation\n\n| dimension | value |\n|---|---|\n" + rows + "\n")
    out.append("## Conversation\n")
    for m in messages:
        role = str(m.get("role", "")).strip() or "—"
        content = str(m.get("content", "")).replace("\n", "\n> ")
        out.append(f"**{role}:**\n> {content}\n")
    return "\n".join(p for p in out if p)


def write_session_note(
    view: dict[str, Any], out_dir: str | Path, *, filename: str | None = None
) -> Path:
    """Write the markdown session note into a vault folder; returns the path."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    name = filename or f"session-{view.get('session_id', 'unknown')}.md"
    path = d / name
    path.write_text(render_session_markdown(view), encoding="utf-8")
    return path
