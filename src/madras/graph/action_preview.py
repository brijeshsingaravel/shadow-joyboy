"""Action preview for 'preview IS the confirmation' (E-E16).

Builds a renderable preview of a *pending* action from its (tool, args) — with **no side
effects** and **no pre-spend** (media is described, not pre-generated). The preview rides on
the ASK/pending payload so the confirm surface can render the actual drafted artifact
(message body, file content, edit diff, media plan) instead of a bare "requires approval".
Engine/contract only; the UI renders the returned ``{type, content}``.
"""

from __future__ import annotations

import json
from typing import Any


def build_action_preview(tool: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Return ``{type, content}`` previewing what the pending action will do. Pure."""
    a = args or {}
    if tool == "send_message":
        return {
            "type": "message",
            "content": {
                "channel": a.get("channel", ""),
                "to": a.get("to", ""),
                "body": str(a.get("body", "")),
            },
        }
    if tool == "file_write":
        content = str(a.get("content", a.get("text", "")))
        return {
            "type": "file",
            "content": {
                "path": a.get("path", ""),
                "content": content[:2000],
            },
        }
    if tool == "file_edit":
        return {
            "type": "diff",
            "content": {
                "path": a.get("path", ""),
                "old": str(a.get("old", a.get("old_string", "")))[:1000],
                "new": str(a.get("new", a.get("new_string", "")))[:1000],
            },
        }
    if tool == "media_pipeline":
        # describe-what-will-be-produced (no generation pre-spend before approval)
        return {
            "type": "media",
            "content": {
                "intent": a.get("intent", ""),
                "preset": a.get("preset", ""),
                "shots": a.get("shots", []),
                "reference": a.get("reference", ""),
                "kind": a.get("kind", "image"),
            },
        }
    # generic fallback: a bounded args summary
    try:
        summary = json.dumps(a, ensure_ascii=False)[:600]
    except (TypeError, ValueError):
        summary = str(a)[:600]
    return {"type": "text", "content": summary}
