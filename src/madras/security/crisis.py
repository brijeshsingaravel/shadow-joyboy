"""security/crisis.py — what Shadow says when someone tells it they want to die.

WHY THIS IS NOT A CATEGORY IN moderation.py. That module exists to stop an agent being
*used* to harm someone -- CSAM, weapons, malware -- and it fails closed, returning "I can't
help with that; it falls outside what this agent is allowed to do." That sentence is right
for a person building a bomb and catastrophically wrong for a person at 2am who has just
said the hardest thing they have ever typed. Refusal reads as rejection. So this is a
separate layer with the opposite disposition: it never blocks, never refuses, and never ends
the turn. It adds something true in front of whatever the model was going to say.

THE DESIGN CONSTRAINT THAT SHAPED THE WORDING: a helpline number compiled into a deployed
binary can be wrong for years and nobody notices, and it will be wrong in front of exactly
the person who most needs it right. So the load-bearing sentence is "tell someone you trust,
tonight" -- correct in every country, in every year, with no network and no lookup. The
operator's help page carries the services, because a page is one edit away from current and
a release is not.

`help_url` defaults to None deliberately. Shadow is open-source; an operator in another
country must not ship an Indian helpline, and the message drops the offer cleanly rather
than pointing nowhere.

Wording is fixed here rather than generated. A model asked to improvise at this moment will
sometimes be excellent and sometimes reach for "you have so much to live for", which
invalidates the person and is said by someone who does not know their life.

Guarded by tests/test_security/test_crisis.py and tests/test_server/test_crisis_in_chat.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Spans that LOOK like intent and are not. Neutralised before matching rather than used as a
# whole-message veto -- someone can mention a film in the same breath as meaning it, and a
# veto would drop the real signal along with the false one.
_BENIGN = [
    r"kill(?:ing)?\s+my\s?self\s+laughing",
    r"suicide\s+(?:squad|bomber|bombing|mission|prevention|hotline|helpline|rates?|note)",
    r"dead\s+tired",
]

# Explicit and self-directed. Tuned for RECALL, unlike moderation.py's precision tuning:
# the cost of a false positive here is a friend being mildly over-cared-for, and the cost of
# a false negative is not comparable to it.
_INTENT = [
    r"\bkill(?:ing)?\s+my\s?self\b",
    r"\bend(?:ing)?\s+(?:my|this)\s+life\b",
    r"\btake\s+my\s+own\s+life\b",
    r"\b(?:want(?:ed)?\s+to|wanna)\s+die\b",
    r"\bwish\s+i\s+(?:was|were)\s+dead\b",
    r"\bbetter\s+off\s+without\s+me\b",
    r"\b(?:don'?t|do\s+not|dont)\s+want\s+to\s+(?:live|be\s+alive|exist)\b",
    r"\bsuicid(?:e|al)\b",
    r"\b(?:cut|cutting|hurt|hurting|harm|harming)\s+my\s?self\b",
    r"\bself[\s-]?harm\b",
    r"\bno\s+(?:point|reason)\s+(?:in\s+living|to\s+live|in\s+going\s+on)\b",
    r"\bend(?:ing)?\s+it\s+all\b",
    r"\bunalive\b",
    r"\bsuicde\b",  # censored/misspelt deliberately, which is most of how it is really typed
    r"\bdisappear\s+permanently\b",
    r"\bbetter\s+off\s+dead\b",
    # "kms". Kilometres far more often than not in this country, so it only counts when no
    # digit precedes it -- "10 kms away" is a distance, "kms tonight" is not.
    r"(?<!\d)(?<! \d)(?<!\d )\bkms\b",
    # --- Tamil script -------------------------------------------------------------------
    # Reviewed by the founder, who is the native speaker; not written from a non-speaker's
    # memory, which is why this list was one word until he supplied it.
    r"தற்கொலை",
    r"உயிரை\s*மாய்",
    r"வாழ்க்கை\s*வெறு",
    # செத்து + a continuation. Bare செத்து also means an ordinary death, so "my grandfather
    # died" would trip it -- and someone grieving does not need a helpline paragraph.
    # NOTE FOR THE FOUNDER: செத்துட் is still reachable from "செத்துட்டார்" (he/she died).
    # Narrow it if that turns out to matter; it is left wide because the other error is worse.
    r"செத்து(?:ட|ப்போ|றே)",
    # --- Tanglish (Tamil typed in English script) ---------------------------------------
    # How most of this actually arrives on a phone, and the reason the Tamil-script list
    # alone would have caught almost nothing.
    r"\bth[a-z]{0,3}kolai\b",  # tharkolai / thatkolai / thaarkolai
    # "sethu" alone is a common name and a film, so it needs its continuation.
    r"\bsethu\s?(?:poren|porein|poganum|dalam|dalaam|polam|polaam|pola)\b",
    r"\bvazh\w*\s+(?:mudi|veru)\w*",  # vazhkai mudinchuthu / vazhkai veruthuduchu
    r"\bvazh\w*veru\w*",
    r"\buyirodu\s+iru\w*\s+(?:viruppam\s+illai|pudi\w*la\w*|virumba\w*la\w*)",
    r"\b(?:en\s+)?life\s+(?:is\s+)?over\b",
]

_WITH_HELP = """I'm reading this properly, and I'd rather be honest with you than smooth.

I'm a program. I can't tell how much danger you're in, and I can't keep you safe. That isn't \
me stepping back from you — it's the one thing I genuinely cannot do, and you should hear it \
now rather than find it out later.

Please tell a person tonight. Someone you trust — tonight, not tomorrow. If there's nobody \
you can think of, there are people whose entire job is to pick up: {url}

I'm still here. Tell me what's been happening."""

_WITHOUT_HELP = """I'm reading this properly, and I'd rather be honest with you than smooth.

I'm a program. I can't tell how much danger you're in, and I can't keep you safe. That isn't \
me stepping back from you — it's the one thing I genuinely cannot do, and you should hear it \
now rather than find it out later.

Please tell a person tonight. Someone you trust — tonight, not tomorrow. If there's nobody \
you can think of, a doctor or a crisis line in your country will take the call.

I'm still here. Tell me what's been happening."""


# --------------------------------------------------------------------------------------------
# The model's own continuation. Added s68 AFTER deploying, because the first live probe exposed
# the hole: this module controlled OUR paragraph and then handed the microphone straight back to
# a 4B model at the worst possible moment. It appended the US 988 line and "Text HOME to 741741"
# to a person in Chennai. 988 does not connect from an Indian phone.
#
# The evidence says DO NOT SUPPRESS THE REPLY, which was the obvious-looking fix. A general model
# invites the user to keep talking only ~14% of the time and about a sixth as often by turn seven
# -- it withdraws as risk rises, the reverse of clinical practice (Sentio/UL). #chatsafe is blunt
# that shutting the conversation down is not the answer. So Shadow keeps talking; only contact
# details are removed.
#
# STRIP EVERYTHING, including India's own 112 and 14416. That is the founder's decision and it is
# consistent with /help listing nothing: he will not send a friend to a service he has never
# spoken to, and a number the model produced is exactly that with none of the checking.
_CONTACT = [
    r"https?://",
    r"\bwww\.",
    r"[\w.+-]+@[\w-]+\.[\w.]+",
    r"\btext\s+\S+\s+to\b",
    r"\d{3,}",  # any run of three or more digits — 988, 112, 14416, 741741
    r"\d[\d\s\-().]{4,}\d",  # spaced or punctuated numbers — 13 11 14, 044-2464 0050
]
_CONTACT_RX = [re.compile(p, re.IGNORECASE) for p in _CONTACT]

# The secondary layer. It will sometimes be ignored — the reflex is in the training data, not in
# the prompt — which is why strip_contacts above is the control and this is the mitigation.
CRISIS_SYSTEM_GUIDANCE = """This person may be in danger. A fixed message has ALREADY been placed \
above your reply: it tells them you are a program, that you cannot keep them safe, that they \
should tell someone they trust tonight, and where to find help. Do not repeat it.

Do not refuse them and do not go quiet — staying in the conversation matters more than being \
careful. Ask what has been happening and listen.

Do NOT give any helpline, phone number, website, email or crisis service. Not one, for any \
country, however confident you are. The page already linked is the only place resources belong, \
and anything you supply is unverified. Do not tell them how they should feel, do not say they \
have a lot to live for, and do not promise it gets better. You do not know their life."""


def strip_contacts(text: str) -> str:
    """Remove any LINE carrying contact details. Whole lines, never parts of sentences — a
    mangled half-sentence at that moment is worse than a missing one. May return "", and that is
    fine: the crisis message is complete on its own."""
    kept = [ln for ln in (text or "").splitlines() if not any(rx.search(ln) for rx in _CONTACT_RX)]
    out: list[str] = []
    for ln in kept:
        if not ln.strip() and out and not out[-1].strip():
            continue  # collapse the blank runs that removing lines leaves behind
        out.append(ln)
    return "\n".join(out).strip()


@dataclass
class CrisisVerdict:
    detected: bool
    matched: str = ""
    message: str = ""


class CrisisSupport:
    """Deterministic, synchronous, no I/O. Never blocks -- `detected` means "say this as
    well", never "refuse"."""

    def __init__(self, *, help_url: str | None = None) -> None:
        self._help = (help_url or "").strip() or None
        self._benign = [re.compile(p, re.IGNORECASE) for p in _BENIGN]
        self._intent = [re.compile(p, re.IGNORECASE) for p in _INTENT]

    @property
    def message(self) -> str:
        return _WITH_HELP.format(url=self._help) if self._help else _WITHOUT_HELP

    def inspect(self, text: str) -> CrisisVerdict:
        if not text or not text.strip():
            return CrisisVerdict(detected=False)
        cleaned = text
        for rx in self._benign:
            cleaned = rx.sub(" ", cleaned)
        for rx in self._intent:
            m = rx.search(cleaned)
            if m:
                return CrisisVerdict(detected=True, matched=m.group(0), message=self.message)
        return CrisisVerdict(detected=False)
