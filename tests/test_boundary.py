"""The boundary of what this agent can do — enforced, not merely described.

The README makes a promise:

    "It cannot send messages. No email, no chat, no SMS. This is not an oversight or a missing
     feature — the messaging tools are not in this repository at all, because nobody's first
     experiment with a new agent should be able to send email as them."

That is a sentence. These tests are what make it a guarantee.

WHY THIS FILE EXISTS AND WHY IT IS FIRST. A person deciding whether to trust this repo cannot
read 122 modules. They can read a README, and they are entitled to expect that what it says is
enforced by something other than the author's continued good intentions. Every test here fails
loudly the day the repo quietly gains power it promised not to have — including if the person who
widens it is the author, tired, at one in the morning.

These need no database, no model, and no network. They run in about a second, so there is no
excuse for skipping them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import madras.tools.builtin  # noqa: F401 — importing registers every built-in tool
from madras.tools.registry import REGISTRY, Rank

REPO = Path(__file__).resolve().parent.parent

# The twelve this repo ships, decided deliberately and recorded here so a change to the set is a
# change to this list — visible in a diff, not a surprise in production.
SHIPPED_TOOLSETS = {
    "browser", "clarify", "code", "discovery", "file", "file_write",
    "mcp", "memory", "search", "security", "shell", "web",
}

# Words that would appear in the name of a tool able to contact someone on the user's behalf, or
# to start another agent. Matched against tool names, deliberately broadly: a false positive here
# costs one conversation about naming, and a false negative costs somebody's inbox.
CAN_REACH_A_PERSON = ("send", "email", "mail", "sms", "message", "notify", "post_to")
CAN_SPAWN_AN_AGENT = ("delegate", "spawn", "sub_agent", "subagent")


def _role(name: str = "shadow") -> dict:
    return yaml.safe_load((REPO / "agents" / "roles" / f"{name}.yaml").read_text(encoding="utf-8"))


class TestNothingCanContactAnyone:
    """The repo's single hardest promise."""

    def test_no_registered_tool_can_send_anything(self) -> None:
        offenders = [
            t.name for t in REGISTRY.all()
            if any(w in t.name.lower() for w in CAN_REACH_A_PERSON)
        ]
        assert not offenders, (
            f"A tool that can contact someone is registered: {offenders}. "
            "The README promises this repository cannot send messages. Either remove the tool, "
            "or change the README — but they must not disagree."
        )

    def test_the_messaging_toolset_does_not_exist(self) -> None:
        assert "messaging" not in REGISTRY.toolsets()

    def test_the_messaging_module_is_not_in_the_tree(self) -> None:
        """Absent, not merely unregistered. A file that exists can be imported by accident."""
        assert not (REPO / "src/madras/tools/builtin/messaging_tools.py").exists()


class TestNothingCanStartAnotherAgent:
    """Delegation is deferred, not switched off — see the README. Until it can be extracted
    cleanly it must not be present at all, because a half-extracted sub-agent system is worse
    than none."""

    def test_no_registered_tool_can_spawn(self) -> None:
        offenders = [
            t.name for t in REGISTRY.all()
            if any(w in t.name.lower() for w in CAN_SPAWN_AN_AGENT)
        ]
        assert not offenders, f"delegation reached the registry: {offenders}"

    def test_the_delegation_toolset_does_not_exist(self) -> None:
        assert "delegation" not in REGISTRY.toolsets()


class TestTheToolSurfaceIsExactlyWhatWeSaid:
    def test_the_shipped_toolsets_are_the_twelve(self) -> None:
        assert REGISTRY.toolsets() == SHIPPED_TOOLSETS, (
            "The tool surface changed. That is allowed — but it is a decision, so update "
            "SHIPPED_TOOLSETS in this file and the README in the same commit."
        )

    def test_every_toolset_actually_has_tools(self) -> None:
        """A toolset with no tools is a switch wired to nothing."""
        empty = SHIPPED_TOOLSETS - {t.toolset for t in REGISTRY.all()}
        assert not empty, f"declared but empty: {sorted(empty)}"

    def test_the_registry_is_not_empty(self) -> None:
        """Guards against the failure that would make every test above pass vacuously: if the
        import registered nothing, 'no messaging tool exists' is true and meaningless."""
        assert len(REGISTRY.all()) > 40, (
            f"only {len(REGISTRY.all())} tools registered — the import probably failed, which "
            "would make every 'nothing bad is present' test above pass for the wrong reason"
        )


class TestShadowAsksForNothingItCannotHave:
    """The gap that made this file worth writing.

    Shadow's role config declared `messaging` and `delegation` — plus `planning`, `rca`, `vision`,
    `schedule`, `media` and `image_gen`, none of which ship. It was harmless only because the code
    had been deleted: `allowed()` filters by what is registered, so a request for a toolset that
    does not exist quietly resolves to nothing.

    Harmless is not the same as safe. The config was still ASKING for the ability to send email,
    and the only thing standing in the way was a deleted file. Put `messaging_tools.py` back —
    by re-running an extraction with a changed list, or copying one file — and Shadow would gain
    it silently, because its configuration already said yes.
    """

    def test_shadow_declares_only_toolsets_that_exist(self) -> None:
        declared = set(_role().get("toolsets") or [])
        ghosts = declared - REGISTRY.toolsets()
        assert not ghosts, (
            f"shadow.yaml asks for toolsets this repo does not ship: {sorted(ghosts)}. "
            "They resolve to nothing today, so this is not currently a vulnerability — it is a "
            "standing invitation to become one."
        )

    def test_shadow_does_not_ask_for_messaging_or_delegation(self) -> None:
        declared = set(_role().get("toolsets") or [])
        assert "messaging" not in declared
        assert "delegation" not in declared

    def test_what_shadow_resolves_to_can_neither_send_nor_spawn(self) -> None:
        """The end-to-end check: the config, the rank gate and the registry together."""
        declared = list(_role().get("toolsets") or [])
        got = REGISTRY.allowed(agent_rank=Rank.INTERN, toolsets=declared)
        bad = [
            t.name for t in got
            if any(w in t.name.lower() for w in CAN_REACH_A_PERSON + CAN_SPAWN_AN_AGENT)
        ]
        assert not bad, f"Shadow resolves to tools it must not have: {bad}"
        assert got, "Shadow resolved to no tools at all — that is a broken config, not a safe one"


class TestOnByDefaultMatchesTheReadme:
    """The README tells a stranger what Shadow can do the moment they run it. That sentence and
    this config have to agree, and the agreement should not depend on anyone remembering.

    They disagreed when this was written. The README said memory and clarification were on and
    everything else shipped switched off; the config turned all twelve on, so a first run could
    execute shell commands and write files. Nobody should learn an agent can do that by watching
    it do it.
    """

    ON_BY_DEFAULT = {"memory", "clarify"}

    def test_shadow_starts_with_exactly_memory_and_clarify(self) -> None:
        declared = set(_role().get("toolsets") or [])
        assert declared == self.ON_BY_DEFAULT, (
            f"Shadow starts with {sorted(declared)}. The README says "
            f"{sorted(self.ON_BY_DEFAULT)}. Change both together or neither."
        )

    def test_the_dangerous_ones_are_off(self) -> None:
        """Named individually, so switching one on is a deliberate edit to a test that says why."""
        declared = set(_role().get("toolsets") or [])
        for risky in ("shell", "file_write", "code", "browser"):
            assert risky not in declared, (
                f"`{risky}` is on by default. It ships, and it is one line from working — but a "
                "first run should not be able to touch someone's machine before they've decided."
            )

    def test_the_default_agent_is_still_useful(self) -> None:
        """Safe and useless is not a win. Memory alone must still give it real tools."""
        got = REGISTRY.allowed(agent_rank=Rank.INTERN, toolsets=list(self.ON_BY_DEFAULT))
        assert len(got) >= 10, (
            f"only {len(got)} tools with the default config — memory is the whole promise of "
            "this repo, so if it resolves to almost nothing the default is wrong, not safe"
        )


# EVERY yaml under agents/, not just roles/. Globbing roles/ alone missed thirteen configs,
# one of them bundles/tools/messaging.yaml -- "Draft and send messages across governed
# channels" -- sitting in a repo whose README says the messaging tools are not here at all.
# No code backed it so it granted nothing, but a reader who found it would rightly stop
# believing the README. A test that checks one directory proves one directory.
@pytest.mark.parametrize("role_file", sorted((REPO / "agents").rglob("*.yaml")))
def test_every_role_in_the_repo_respects_the_boundary(role_file: Path) -> None:
    """Not just Shadow. Any role config shipped here is a door, and they all need the same lock."""
    cfg = yaml.safe_load(role_file.read_text(encoding="utf-8")) or {}
    if not isinstance(cfg, dict):
        return
    named = set(cfg.get("toolsets") or [])
    if isinstance(cfg.get("toolset"), str):
        named.add(cfg["toolset"])   # bundle files name a single toolset, not a list
    ghosts = named - REGISTRY.toolsets()
    assert not ghosts, (
        f"{role_file.relative_to(REPO)} describes toolsets this repo does not ship: "
        f"{sorted(ghosts)}. It grants nothing, but it contradicts the README in the same repo."
    )
