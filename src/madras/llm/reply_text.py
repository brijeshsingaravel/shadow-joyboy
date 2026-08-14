"""Clean an assistant reply before a person reads it.

MOVED FROM `server/` (s67). Nothing here is about HTTP -- it is about what a model said and what
a person should see instead when the model said nothing usable. Living under `server/` meant the
CLI could not use it, and the open-source extraction drops `server/` entirely, so a repo built
from this one shipped without the blank-reply guard and would hand somebody an empty message.
A reply boundary belongs next to the thing that produces replies.

Right now that means one thing: removing `<retrieved>` fences the model invented.

WHY THEY APPEAR. `<retrieved>...</retrieved>` marks injected content as DATA rather than
instructions (ASI02) -- `extract.py`, `graph/compaction.py`, `graph/jit_context.py` and the
`recall` tool all use it. The convention is visible to the model in its own prompt, and models
imitate it when presenting remembered information. s64 established that nothing in the recall
path adds these tags to a reply; the model writes them itself. Verified again live at s66:

    <retrieved>brinjal</retrieved>

    Your grandfather grows brinjal in his garden in Kumbakonam.

WHY NOT JUST ASK IT TO STOP. The recall block already instructs "Never quote this list back to
them", and it still happened -- less than before, but still. An instruction is a request; a
strip is a guarantee.

WHAT THIS MUST NOT DO. Eat ordinary angle brackets. Someone asking about HTML, or writing
`3 < 5`, must get their text back exactly. The pattern therefore matches the literal tag name and
nothing else. Guarded by tests/test_server/test_reply_fence_stripping.py.
"""

from __future__ import annotations

import re

# Matches only an opening `<retrieved ...>` or a closing `</retrieved>` -- never a bare `<`, and
# never another tag. Attributes are allowed because jit_context emits `<retrieved file=...>`.
_FENCE = re.compile(r"</?retrieved(?:\s[^>]*)?>", re.IGNORECASE)


def strip_data_fences(text: str | None) -> str:
    """Remove `<retrieved>` fences from an outgoing reply, keeping everything they contained.

    Returns "" for anything that is not a string, because this sits on the outgoing path of
    every reply and must never be the reason a request fails.
    """
    if not isinstance(text, str):
        return ""
    if "retrieved" not in text.lower():
        return text  # the overwhelmingly common case, untouched and cheap
    # A fence that occupied the first line leaves the reply starting with blank lines, so tidy
    # the edges. Only reached when a fence was actually present -- untouched text returns above.
    return _FENCE.sub("", text).strip()


# What a person sees instead of nothing. Deliberately says what happened in ordinary words and
# what they can do about it -- an apology on its own is not help, and "finish_reason: length" is
# an API field, not an explanation.
_RAN_OUT_THINKING = (
    "I thought about that for too long and ran out of room before I could answer. "
    "Ask me again with a narrower question and I should get there."
)
_EMPTY_UNKNOWN = (
    "Something went wrong and I came back with nothing. That's a fault at my end, not yours — "
    "please ask me again."
)


def explain_empty_reply(text: str | None, *, finish_reason: str | None = None) -> str:
    """Return `text` unchanged, unless it is empty — then say so in words a person can act on.

    FOUND BY MEASURING (s66). Benchmarking Shadow against MemoryAgentBench, one answer in eight
    came back as an empty string. Not an error and not a refusal: HTTP 200, well-formed JSON,
    `finish_reason: "length"`, `content: ""`. The model had spent its whole token budget reasoning
    and been cut off mid-thought. Every layer reported success and the person got a blank message.

    A friend cannot tell a blank reply from a broken app. This is the last point where we still
    know why it was empty, so this is where it gets said.

    A TRUNCATED-BUT-NON-EMPTY reply is left exactly as it is. Losing a written paragraph because
    the final word was clipped would be a worse outcome than the clip.
    """
    if isinstance(text, str) and text.strip():
        return text
    if finish_reason == "length":
        return _RAN_OUT_THINKING
    return _EMPTY_UNKNOWN
