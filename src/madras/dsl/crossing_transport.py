"""Phase P slice 1 -- the machinery that acts on a CROSS verdict.

`crossing.decide_crossing` is the judgment (HERE / CROSS / REFUSE); this is the transport. They are
separate modules on purpose: the judgment is a pure function of `(module, v_max, destination)` and
stays testable with no node in existence, while this touches a real process and cannot be.

**Nothing new is invented here, deliberately.** D79 already chose the network shape -- MCP-style
JSON-RPC 2.0, "synthesize, don't invent" -- and Phase U already built `dispatch_network`, which
takes a `BridgeManifest` with `transport=network` and really calls it through
`madras.mcp.client`'s governed session machinery. So a crossing destination IS a manifest, and this
module is thin by design: serialise, dispatch, return. A second transport living beside the chosen
one would be exactly the duplication that D79 exists to prevent, and the s59 defect shape --
two implementations of one thing drifting apart -- applied to the question of whether work may
leave the machine.

The consequence worth stating: the LOCAL STAND-IN is not a mock. It is a `stdio` server
descriptor -- a real MCP server spawned as a child process -- so the same governed path runs with
no network, no remote host and no credentials. Pointing this at `base-01` later changes the
descriptor (`stdio` -> `http_sse`), not this code.

**The one thing this module must never do is dispatch a verdict that is not CROSS.** If a REFUSE
could reach the wire, refusing would not refuse and the governance half of Phase P would be
decoration. It is checked first, before the manifest is even read, so the guarantee holds whether
or not the destination happens to be reachable.
"""

from __future__ import annotations

from tamil_lang.nadi import NadiModule

from madras.dsl.bridge_dispatch_network import dispatch_network
from madras.dsl.crossing import CrossingDecision, CrossingVerdict
from madras.models.bridge_manifest import BridgeManifest


class CrossingNotAuthorised(RuntimeError):
    """A crossing was attempted on a verdict that did not authorise one.

    Raised rather than returned: a caller that ignores a return value would silently send work the
    decision refused, and the whole point of three verdicts is that REFUSE is expressible.
    """


async def perform_crossing(
    decision: CrossingDecision,
    module: NadiModule,
    manifest: BridgeManifest,
) -> str:
    """Carry `module` to the destination described by `manifest`, returning the real result text.

    Fails closed on the verdict BEFORE touching the manifest or the transport, so a refusal aimed
    at a perfectly healthy node still sends nothing -- the guarantee is enforced by the decision,
    never by the destination happening to be unreachable.

    The module crosses as its own JSON (`NadiModule` is Pydantic and round-trips exactly), which is
    the IR the whole seam is built on: what leaves the machine is the lowered program, not source
    text that would have to be re-parsed -- and re-parsing at the far end would be a second front
    end, free to disagree with the first about what the program means.

    A transport failure propagates. It is NOT caught and downgraded to running locally: the box has
    already said the program does not fit here, so a silent fallback would run something known not
    to fit. That is the llama.cpp failure mode -- overflow quietly backed by host RAM -- and the
    reason its mis-estimates went unnoticed.
    """
    if decision.verdict is not CrossingVerdict.CROSS:
        raise CrossingNotAuthorised(
            f"crossing not authorised: verdict is {decision.verdict.value!r}, not "
            f"{CrossingVerdict.CROSS.value!r} -- {decision.reason}"
        )

    return await dispatch_network(manifest, {"message": module.model_dump_json()})


__all__ = ["CrossingNotAuthorised", "perform_crossing"]
