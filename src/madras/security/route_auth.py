"""Auth-fails-closed route posture (row 90, eve pattern).

A server's HTTP routes must DENY by default: every route requires auth unless it EXPLICITLY opts out
via `allow_anonymous()` (eve's `none()`), an UNREGISTERED path is denied (a route shipped without a
posture is never silently open), and a dev placeholder authenticator refuses to run in production
(`placeholderAuth()` stays closed). Principal = the row-83 `AuthContext`. Pure; a governance
primitive every server surface (cockpit API / ACP / CodeAct RPC) shares.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

_PROD_ENVS = frozenset({"prod", "production"})


@dataclass(frozen=True)
class RouteAuth:
    path: str
    anonymous: bool = False  # True only via an explicit allow_anonymous() opt-out


@dataclass
class AuthOutcome:
    allowed: bool
    status: int  # 200 admit / 401 deny
    reason: str


@dataclass
class RouteRegistry:
    _routes: dict[str, RouteAuth] = field(default_factory=dict[str, RouteAuth])

    def require_auth(self, path: str) -> None:
        """The default posture — this route needs an authenticated principal."""
        self._routes[path] = RouteAuth(path, anonymous=False)

    def allow_anonymous(self, path: str) -> None:
        """The explicit none() opt-out — the only way a route becomes public."""
        self._routes[path] = RouteAuth(path, anonymous=True)

    def get(self, path: str) -> RouteAuth | None:
        return self._routes.get(path)


def _is_authenticated(principal: Any) -> bool:
    if principal is None:
        return False
    tenant: Any = getattr(principal, "tenant", None)
    if tenant is None and isinstance(principal, dict):
        tenant = cast("dict[str, Any]", principal).get("tenant")
    return bool(tenant)


def authorize_request(
    registry: RouteRegistry,
    path: str,
    *,
    principal: Any = None,
    env: str = "prod",
    placeholder: bool = False,
) -> AuthOutcome:
    """Fail-closed route authorization. Default deny; only an explicitly-anonymous route or a valid
    principal admits; a placeholder authenticator is refused in production."""
    route = registry.get(path)
    if route is None:
        return AuthOutcome(False, 401, "unregistered route — fail-closed default deny")
    if route.anonymous:
        return AuthOutcome(True, 200, "explicitly anonymous (none())")
    # route requires auth from here:
    if placeholder:
        if env.lower() in _PROD_ENVS:
            return AuthOutcome(False, 401, "placeholderAuth() refuses to run in production")
        return AuthOutcome(True, 200, "placeholder auth (dev only)")
    if _is_authenticated(principal):
        return AuthOutcome(True, 200, "authenticated")
    return AuthOutcome(False, 401, "auth required")
