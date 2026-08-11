"""Phase P slice 3b -- the gRPC transport for a crossing (RFC-0002 §5.9).

§5.9 names **gRPC over mTLS** as the device→edge→cloud wire ("every hop authenticated... no
unauthenticated hop, consistent with §5.1's 'gated' law applied to the network itself"), and the
founder confirmed the full N6 scope at s56. This is that transport: a server that judges arrivals
with `crossing_receipt.decide_receipt` before executing them, and a client that carries a lowered
module to one.

**Raw bytes, not protobuf -- a deliberate, disclosable deviation from §5.9's payload note.**
gRPC lets a method register its own serializers, so the transport is genuine gRPC while the payload
stays the Nadi JSON the IR already round-trips exactly. Reasons, in order of weight:

1. the payload format is explicitly still open: the s56 radar flagged **Cap'n Proto** as "a real
   comparison when §5.9 is actually built" -- which is now, and that comparison has not been done;
2. `grpcio-tools` would add a dependency plus a codegen build step, and every `uv sync` here risks
   silently dropping the optional-extras stack (a documented, repeated regression in this project);
3. `NadiModule` is a Pydantic model with its own round-trip guarantee, so protobuf would be a
   second schema over one that already exists -- and two schemas for one thing is the drift shape
   this codebase has paid for repeatedly.

Adopting protobuf later changes the two serializer arguments below and nothing else.

**mTLS IS NOT IMPLEMENTED HERE, and the credentials parameters must not be read as implying it is.**
Both ends accept `credentials`; when omitted the channel is INSECURE. Certificates are step-ca's
job (the s56 radar's Adopt-level answer for exactly this) and provisioning them is a separate row.
Until it lands, this transport is unauthenticated and is only safe over a channel that is already
authenticated -- e.g. the existing SSH tunnel to `base-01`, which is how its data tier is reached
today.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

import grpc
from madras_capabilities.catalog import Catalog
from tamil_lang.nadi import NadiModule

from madras.dsl.crossing_receipt import ReceiptVerdict, decide_receipt
from madras.security.permissions import PermissionEngine, PermissionRule

if TYPE_CHECKING:  # passed straight through to `decide_receipt`; never touched at runtime here.
    from madras.tools.sandbox import Sandbox

SERVICE = "madras.Crossing"
METHOD = "Cross"
_FULL_METHOD = f"/{SERVICE}/{METHOD}"


class CrossingRefused(RuntimeError):
    """The far end declined the work.

    A distinct type from a transport error on purpose: "the node said no" and "the node could not
    be reached" are different failures with different fixes, and collapsing them would send someone
    to check the network when the answer was a permission rule (or the reverse).
    """


async def serve_crossing(
    *,
    executor: Callable[[NadiModule], Awaitable[str]],
    host: str = "127.0.0.1",
    port: int = 0,
    permissions: PermissionEngine | None = None,
    rules: list[PermissionRule] | None = None,
    v_max: int | None = None,
    catalog: Catalog | None = None,
    sandbox: Sandbox | None = None,
    credentials: Any = None,
) -> tuple[Any, int]:
    """Start a crossing receiver. Returns `(server, bound_port)`.

    Every arrival is judged by `decide_receipt` BEFORE `executor` is called -- the gate is not a
    wrapper the executor could be invoked around. `port=0` binds an ephemeral port, which is what
    makes this testable without reserving one.

    `catalog` and `sandbox` are THIS node's, and a receiver started without them still serves:
    arrivals that call no capability are accepted as before, and arrivals that do are refused for
    want of a catalog. That is the fail-closed direction, and it is why adding these gates did not
    have to break the receiver already deployed.

    `host` defaults to loopback deliberately: an unauthenticated receiver (see the module docstring)
    must not be reachable from off-machine by accident. Binding it wider is a caller's explicit
    choice, matching how postgres/redis/qdrant are bound on this project's own hosts.
    """

    async def _handle(request: bytes, context: Any) -> bytes:
        try:
            payload = json.loads(request.decode("utf-8"))
            module = NadiModule.model_validate_json(payload["module"])
            origin = payload.get("origin", "")
        except Exception as exc:  # malformed input is a refusal, not a crash
            return json.dumps(
                {"ok": False, "reason": f"unreadable crossing payload: {exc}"}
            ).encode()

        decision = decide_receipt(
            module,
            origin=origin,
            permissions=permissions,
            rules=rules,
            v_max=v_max,
            catalog=catalog,
            sandbox=sandbox,
        )
        if decision.verdict is not ReceiptVerdict.ACCEPT:
            return json.dumps({"ok": False, "reason": decision.reason}).encode()

        try:
            result = await executor(module)
        except Exception as exc:
            # An execution failure is reported as such, never as a refusal: the crossing WAS
            # authorised, and mislabelling it would point the caller at governance instead of code.
            return json.dumps({"ok": False, "reason": f"execution failed: {exc}"}).encode()
        return json.dumps({"ok": True, "result": result}).encode()

    server = grpc.aio.server()
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                SERVICE,
                {
                    METHOD: grpc.unary_unary_rpc_method_handler(
                        _handle,
                        # None on both sides = the bytes pass through untouched. This is the seam
                        # protobuf would occupy later.
                        request_deserializer=None,
                        response_serializer=None,
                    )
                },
            ),
        )
    )
    address = f"{host}:{port}"
    bound = (
        server.add_secure_port(address, credentials)
        if credentials is not None
        else server.add_insecure_port(address)
    )
    await server.start()
    return server, bound


async def send_crossing(
    module: NadiModule,
    *,
    origin: str,
    target: str,
    credentials: Any = None,
    timeout: float = 300.0,
) -> str:
    """Carry `module` to `target`, returning the far end's result text.

    `timeout` defaults to 300s rather than the MCP path's 25s: a crossing exists BECAUSE the work
    was too large for the sending machine, so the far end is expected to take longer than a tool
    call, and a short ceiling would fail slow-but-correct work while looking like a network error.

    Raises `CrossingRefused` if the far end declined. Transport errors propagate as themselves --
    a node that said no and a node that could not be reached are different problems.
    """
    channel = (
        grpc.aio.secure_channel(target, credentials)
        if credentials is not None
        else grpc.aio.insecure_channel(target)
    )
    async with channel:
        # `request_serializer=None, response_deserializer=None` means grpc passes bytes straight
        # through untouched -- but that leaves the call's type unknowable to a type checker, so the
        # bytes contract is stated here rather than inferred.
        call = cast(
            "Callable[..., Awaitable[bytes]]",
            channel.unary_unary(_FULL_METHOD, request_serializer=None, response_deserializer=None),
        )
        raw: bytes = await call(
            json.dumps({"origin": origin, "module": module.model_dump_json()}).encode("utf-8"),
            timeout=timeout,
        )
    reply = json.loads(raw.decode("utf-8"))
    if not reply.get("ok"):
        raise CrossingRefused(reply.get("reason", "the far end refused without a reason"))
    return str(reply["result"])


__all__ = ["METHOD", "SERVICE", "CrossingRefused", "send_crossing", "serve_crossing"]
