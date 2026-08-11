"""The receiving side's judgment (Phase P slice 3a) -- may this arriving work run here?

`crossing.decide_crossing` authorises DEPARTURE and `crossing_transport.perform_crossing` carries
the module. This is the other end, and it exists because **the sender's permission says nothing
about what the program may do once it lands**.

A node that interprets whatever arrives has moved work OUT of the governance boundary Phase P
exists to enforce: crossing would become a way to escape governance, which is the exact inversion
of the feature. So there are **two independent gates, neither inheriting the other** -- the same
shape as mutual TLS. The sender authorises departure; the receiver authorises arrival.

**That relation is named விருந்து (virundhu) -- the guest relation (D84).** The sender is a guest
asking to place work somewhere it does not control; this module is the host, welcoming or declining
on its own judgement. `crossing` names the travelling, which is the ordinary half; the host's right
to refuse is the part worth a name. Kural 84 puts the whole design in one word -- the host receives
the *worthy* guest, so hospitality is a judgement, not indiscriminate admission.

**Pure, exactly as `crossing.py` is, and for the same reason.** No I/O, no network, no live node,
so the governance-critical part is testable before any transport exists. RFC-0002 §5.9 specs gRPC
over mTLS for the wire (founder-confirmed at s56's N6 sweep, with step-ca as the certificate
answer), but nothing decided here depends on that: these judgments are identical whether the bytes
arrive over gRPC, MCP, or a pipe. Building the transport first would have meant building it around
an unproven judgment.

**The one-hop rule.** A received crossing may never cross onward. Without that, A crosses to B, B
breaches its own ceiling and crosses to A -- an infinite loop consuming two machines that presents
as a hang. It is enforced by calling `decide_crossing` with `destination=None`, which can only
return HERE or REFUSE: onward crossing is *unreachable* rather than merely forbidden. A guarantee
by construction beats a guard someone can forget to keep.

**The last two gates (slice 3e).** Earlier this function judged only WHO sent the work and whether
it fits, which was enough to validate an arrival but not to run one: the sending machine also
resolves the program's capabilities and provisions a sandbox for the untrusted ones, and a receiver
that skipped both would execute arriving code under FEWER checks than the sender applies. So both
are answered here, **against THIS node's catalog** -- a capability trusted and built on the sending
machine may be absent, unbuilt, or untrusted on this one, and accepting on the sender's word would
make crossing a way to run capabilities a node never installed.

Taking a `Catalog` and a `Sandbox` costs nothing in purity, which is why they could join a pure
function: `resolve_toolsets` and `requires_sandbox_ids` are both pure given a catalog, so the
governance-critical part stays testable with no node and no network. The gates are the interpreter's
own -- called, not re-implemented -- because two implementations of "may this run" would drift, and
drift between the sending and receiving definitions is precisely how a second gate becomes theatre.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from madras_capabilities.catalog import Catalog
from madras_capabilities.resolve import CapabilityNotBuilt, UnknownCapability, resolve_toolsets
from tamil_lang.nadi import NadiModule, capability_names

from madras.dsl.crossing import CrossingVerdict, decide_crossing
from madras.dsl.sandboxed import is_trusted, requires_sandbox_ids
from madras.security.permissions import Decision, PermissionEngine, PermissionRule

if TYPE_CHECKING:  # only ever tested for `is None`; see `sandboxed.sandbox_for_goal`.
    from madras.tools.sandbox import Sandbox


class ReceiptVerdict(str, Enum):
    """What this node does with work that arrived from somewhere else."""

    ACCEPT = "accept"
    """Authorised by THIS node and fits THIS node. Proceed to interpret it."""

    REFUSE = "refuse"
    """Not authorised here, or does not fit here. Fails closed, and never forwards."""


@dataclass(frozen=True)
class ReceiptDecision:
    """A decision, the origin it judged, and the measurement behind it.

    `origin` is carried separately from `reason` because an audit record needs it as a field, not
    only as prose: every outward crossing is meant to be auditable (D78), and a receipt that cannot
    say WHO sent the work is not an audit record.
    """

    verdict: ReceiptVerdict
    reason: str
    origin: str | None = None


RECEIPT_TOOL = "crossing-receipt"
"""The tool name an ARRIVAL is checked under.

Deliberately NOT `crossing.CROSSING_TOOL`. They are different questions -- "may I send work to
X?" and "may I accept work from Y?" -- and sharing one name would let a rule written to permit
sending silently permit receiving. Two gates need two vocabularies or they are one gate.
"""


def decide_receipt(
    module: NadiModule,
    *,
    origin: str,
    permissions: PermissionEngine | None = None,
    rules: list[PermissionRule] | None = None,
    v_max: int | None = None,
    catalog: Catalog | None = None,
    sandbox: Sandbox | None = None,
) -> ReceiptDecision:
    """Decide whether work arriving from `origin` may run on this node.

    `v_max` is THIS node's ceiling, not the sender's. The sender refused the program at its own
    limit, which is why it crossed; a receiver that reported the sender's number in its refusal
    would send someone to the wrong host.

    `catalog` and `sandbox` are likewise THIS node's. A module that calls no capability asks
    nothing of the catalog and needs none; one that does, and arrives at a node holding no catalog,
    is refused rather than guessed at.
    """
    if not origin:
        return ReceiptDecision(
            ReceiptVerdict.REFUSE,
            "arrival has no origin -- an unidentified sender cannot be authorised, since "
            "permission rules would have nothing to match on",
        )

    # An ungoverned arrival is exactly what Aram forbids. The sender having authorised DEPARTURE
    # is not an answer to whether this node accepts ARRIVALS.
    if permissions is None:
        return ReceiptDecision(
            ReceiptVerdict.REFUSE,
            f"arrival from {origin!r} is ungoverned -- no PermissionEngine was supplied, and the "
            "sender's authorisation does not carry across the boundary",
            origin=origin,
        )

    verdict = permissions.check(
        tool=RECEIPT_TOOL, toolset="runtime", args={"origin": origin}, rules=rules
    )
    if verdict is Decision.DENY:
        return ReceiptDecision(
            ReceiptVerdict.REFUSE, f"arrival from {origin!r} was denied", origin=origin
        )
    if verdict is Decision.ASK:
        return ReceiptDecision(
            ReceiptVerdict.REFUSE,
            f"arrival from {origin!r} needs confirmation, which this path cannot obtain -- "
            "refusing rather than assuming yes",
            origin=origin,
        )

    # THE ONE-HOP RULE. `destination=None` makes CROSS unreachable, so this can only answer "fits
    # here" or "does not". Reusing the sender's own decision function rather than re-measuring is
    # deliberate: two implementations of "does it fit" would drift, which is the defect shape s59
    # found six times over.
    fit = decide_crossing(module, v_max=v_max, destination=None)
    if fit.verdict is not CrossingVerdict.HERE:
        return ReceiptDecision(
            ReceiptVerdict.REFUSE,
            f"arrival from {origin!r} does not fit on this node either and will NOT be forwarded "
            f"-- {fit.reason}",
            origin=origin,
        )

    # THIS NODE'S CATALOG, not the sender's. Only asked if the program actually calls something:
    # requiring a catalog to accept work that needs none would refuse safe arrivals as ceremony.
    capability_ids = capability_names(module)
    if capability_ids:
        if catalog is None:
            return ReceiptDecision(
                ReceiptVerdict.REFUSE,
                f"arrival from {origin!r} calls {', '.join(capability_ids)} but this node holds no "
                "catalog to check them against -- refusing rather than accepting on the sender's "
                "word",
                origin=origin,
            )
        try:
            resolve_toolsets(capability_ids, catalog)
        except (UnknownCapability, CapabilityNotBuilt) as exc:
            return ReceiptDecision(
                ReceiptVerdict.REFUSE,
                f"arrival from {origin!r} cannot run here -- {exc}",
                origin=origin,
            )

        # RFC-0002 §5's Sandboxed law at the receiving end. The DECISION comes from the same
        # predicate `interpret()` uses; `is_trusted` is called again only to NAME the offenders,
        # because a refusal an operator cannot act on is barely a refusal.
        if sandbox is None and requires_sandbox_ids(capability_ids, catalog):
            untrusted = [c for c in capability_ids if not is_trusted(c, catalog)]
            return ReceiptDecision(
                ReceiptVerdict.REFUSE,
                f"arrival from {origin!r} calls {', '.join(untrusted)}, which this node does not "
                "trust unsandboxed, and no sandbox was provisioned to hold it",
                origin=origin,
            )

    return ReceiptDecision(
        ReceiptVerdict.ACCEPT, f"arrival from {origin!r} authorised -- {fit.reason}", origin=origin
    )


__all__ = ["RECEIPT_TOOL", "ReceiptDecision", "ReceiptVerdict", "decide_receipt"]
