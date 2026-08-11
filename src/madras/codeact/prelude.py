"""CodeAct prelude codegen — sandbox side (S2).

Generates the Python prelude injected *before* the LLM's code inside the sandbox. It exposes the
agent's allowlisted tools as plain functions that call back to the host ToolBridge (S1) over the
RPC endpoint S3 serves, plus a ``result()`` sink the host parses for the action's structured
output. The per-session token is embedded so the sandbox can authenticate; the sandbox is the
only consumer and its network is locked to the RPC endpoint (S3), so the token never leaves.

The generated module uses only the stdlib (``json``, ``urllib``) so it runs in a bare sandbox.
"""

from __future__ import annotations

#: Marker the host scans stdout for to recover the action's structured result.
RESULT_MARKER = "__MADRAS_RESULT__"

_CLIENT_TEMPLATE = '''import json as _json
import urllib.error as _urlerr
import urllib.request as _urllib

_ENDPOINT = {endpoint!r}
_TOKEN = {token!r}
_RESULT_MARKER = {marker!r}


def _rpc(name, args):
    """Call one governed tool on the host bridge; returns its result dict.

    A refused call (401/403) or failure carries a JSON {{"ok": false, "error": ...}} body, which
    we return rather than raise — so the agent's code can inspect the error (error evidence stays
    in-context) instead of crashing the whole action."""
    body = _json.dumps({{"token": _TOKEN, "name": name, "args": args or {{}}}}).encode("utf-8")
    req = _urllib.Request(
        _ENDPOINT, data=body, headers={{"Content-Type": "application/json"}}, method="POST"
    )
    try:
        with _urllib.urlopen(req, timeout=60) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except _urlerr.HTTPError as exc:
        try:
            return _json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {{"ok": False, "error": "rpc http %d" % exc.code}}


def result(value):
    """Emit the action's structured result for the host to capture."""
    print(_RESULT_MARKER + _json.dumps(value))
'''

_TOOL_TEMPLATE = """def {name}(**kwargs):
    return _rpc({name!r}, kwargs)
"""


def generate_prelude(*, allowlist: set[str], token: str, endpoint: str) -> str:
    """Build the sandbox prelude exposing ``allowlist`` tools as functions over the RPC bridge.

    Tool names that are not valid Python identifiers are skipped (they cannot be a function name);
    the model can still reach them via ``_rpc("name", {...})`` directly.
    """
    if not token:
        raise ValueError("generate_prelude requires a non-empty session token")
    head = _CLIENT_TEMPLATE.format(endpoint=endpoint, token=token, marker=RESULT_MARKER)
    funcs = [_TOOL_TEMPLATE.format(name=n) for n in sorted(allowlist) if n.isidentifier()]
    return head + "\n" + "\n".join(funcs)
