"""Role-based onboarding & personalization — route a user's role to their agent, work mode,
domain skills, and a pre-seeded suggestion consent profile.

The Codex onboarding pattern (role → personalized suggestions + work mode), wired to Madras:
each role maps to its roster agent ([[Roster]]), the domain skill foundation (B13), and the
suggestion categories the [[Proactive Suggestions]] engine (B21) may surface. Onboarding the
role also yields a `user role` memory fact so the [[User Model]] carries it forward. Pure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from madras.memory.retrieval import MemoryItem
from madras.suggestions.engine import ConsentPolicy


@dataclass(frozen=True)
class RoleProfile:
    role: str
    agent: str  # the roster agent for this vertical
    work_mode: str  # default permission posture
    suggestion_categories: tuple[str, ...]  # categories the engine may surface
    skills: tuple[str, ...]  # the B13 domain skill(s) to foreground
    greeting: str


# The 8 roster verticals + engineering (Shadow) + the founder (omni). Categories always
# include the cross-cutting 'security' + 'followup' nudges.
ROLE_PROFILES: dict[str, RoleProfile] = {
    "marketing": RoleProfile(
        "marketing",
        "Maverick",
        "default",
        ("marketing", "followup", "security"),
        ("campaign-plan",),
        "I'm Maverick — I run your marketing. Let's plan a campaign.",
    ),
    "creative": RoleProfile(
        "creative",
        "Andy",
        "default",
        ("creative", "followup", "security"),
        ("creative-brief",),
        "I'm Andy — creative direction + assets. Hand me a brief.",
    ),
    "finance": RoleProfile(
        "finance",
        "Mona",
        "default",
        ("finance", "followup", "security"),
        ("financial-review",),
        "I'm Mona — your finance partner. Want me to sanity-check the numbers?",
    ),
    "legal": RoleProfile(
        "legal",
        "Atticus",
        "default",
        ("legal", "followup", "security"),
        ("contract-review",),
        "I'm Atticus — I surface contract risks (not legal advice). Share a doc.",
    ),
    "strategy": RoleProfile(
        "strategy",
        "Sage",
        "default",
        ("strategy", "followup", "security"),
        ("strategy-brief",),
        "I'm Sage — strategy + competitive briefs. What's the decision?",
    ),
    "people": RoleProfile(
        "people",
        "Joy",
        "default",
        ("people", "followup", "security"),
        ("hiring-scorecard",),
        "I'm Joy — people & culture. Hiring? Let's build a scorecard.",
    ),
    "support": RoleProfile(
        "support",
        "Sam",
        "default",
        ("support", "followup", "security"),
        ("support-triage",),
        "I'm Sam — customer support & ops. Send me the issue.",
    ),
    "research": RoleProfile(
        "research",
        "Curie",
        "default",
        ("research", "followup", "security"),
        ("research-dossier",),
        "I'm Curie — deep, verified research dossiers. What should I dig into?",
    ),
    "engineering": RoleProfile(
        "engineering",
        "Shadow",
        "default",
        ("security", "followup"),
        (),
        "I'm Shadow — your technical co-builder. What are we shipping?",
    ),
    "founder": RoleProfile(
        "founder",
        "Shadow",
        "default",
        ("strategy", "finance", "marketing", "security", "followup"),
        (),
        "I'm Shadow — your omni co-founder. Where do you want to start?",
    ),
}

_ALIASES = {
    "marketer": "marketing",
    "growth": "marketing",
    "cmo": "marketing",
    "designer": "creative",
    "design": "creative",
    "artist": "creative",
    "cfo": "finance",
    "accounting": "finance",
    "accountant": "finance",
    "lawyer": "legal",
    "counsel": "legal",
    "compliance": "legal",
    "strategist": "strategy",
    "hr": "people",
    "recruiter": "people",
    "people ops": "people",
    "customer support": "support",
    "cs": "support",
    "ops": "support",
    "operations": "support",
    "researcher": "research",
    "analyst": "research",
    "scientist": "research",
    "engineer": "engineering",
    "developer": "engineering",
    "dev": "engineering",
    "swe": "engineering",
    "owner": "founder",
    "ceo": "founder",
}

_DEFAULT_ROLE = "founder"


def normalize_role(raw: str) -> str:
    """Map a free-text role to a canonical role key (alias-aware); fall back to the generalist."""
    key = (raw or "").strip().lower()
    if key in ROLE_PROFILES:
        return key
    return _ALIASES.get(key, _DEFAULT_ROLE)


@dataclass
class OnboardingResult:
    profile: RoleProfile
    consent: ConsentPolicy
    welcome: str
    facts: list[MemoryItem] = field(default_factory=list[MemoryItem])


def onboard(raw_role: str, *, sources: tuple[str, ...] = (), now: float = 0.0) -> OnboardingResult:
    """Build a personalization bundle from a role: the agent, a consent policy pre-seeded with
    the role's suggestion categories + the user's connected `sources`, the work mode, a welcome,
    and a `user role` memory fact for the User Model. 'engine' is always a consented source so
    Madras's own nudges (e.g. security) can surface even before any app is connected."""
    role = normalize_role(raw_role)
    profile = ROLE_PROFILES[role]
    consent = ConsentPolicy(
        categories=set(profile.suggestion_categories),
        sources={"engine", *sources},
    )
    fact = MemoryItem(
        id=uuid.uuid4().hex,
        kind="fact",
        subject="user role",
        content=role,
        source="onboarding",
        created_at=now,
        valid_from=now,
        tags=["onboarding", "role"],
    )
    welcome = f"{profile.greeting}"
    return OnboardingResult(profile=profile, consent=consent, welcome=welcome, facts=[fact])
