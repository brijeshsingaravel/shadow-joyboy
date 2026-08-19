"""If someone tells this Shadow they want to die, the CLI must say something true first.

There is no server in this repo. `madras.cli.turn` IS the product -- it is how a person talks to
Shadow once they have cloned it and run it themselves. So a crisis layer that exists in
`security/crisis.py` and is not called from there is not a safety feature, it is a file.

That is not hypothetical. This repository shipped for four days with `crisis.py` absent
entirely, and after it was extracted it sat unreferenced by anything: a clone answered "i don't
want to live anymore" however a 4B model happened to answer. Upstream, the same seam was wired
into one of three HTTP endpoints for a while, for the same reason -- nobody checked the other
doors. A safety layer is worth exactly the number of doors it is attached to.

These tests read the source rather than run a conversation, deliberately: no database, no model,
no network, in keeping with the rest of this suite. They check the wiring exists and that the
message it would produce is the right shape.
"""

from __future__ import annotations

import re
from pathlib import Path

from madras.security.crisis import CrisisSupport, strip_contacts

REPO = Path(__file__).resolve().parent.parent
CLI = (REPO / "src" / "madras" / "cli.py").read_text(encoding="utf-8")


class TestTheCliCallsIt:
    def test_the_cli_imports_the_crisis_layer(self) -> None:
        assert "from madras.security.crisis import" in CLI, (
            "cli.py does not import the crisis layer, so nothing in this repo calls it"
        )

    def test_the_turn_function_inspects_what_the_person_said(self) -> None:
        assert re.search(r"CrisisSupport\([^)]*\)\.inspect\(", CLI), (
            "the reply path does not inspect the user's message"
        )

    def test_the_model_reply_is_stripped_of_contact_details(self) -> None:
        """The deployed model appended US helpline numbers to a user in Chennai. The strip is
        the control; asking the model nicely is only the mitigation."""
        assert "strip_contacts(" in CLI


class TestTheMessageItself:
    def test_it_names_a_human_before_any_service(self) -> None:
        msg = CrisisSupport(help_url="https://example.org/help").inspect("i want to die").message
        assert "tell a person tonight" in msg.lower()
        assert msg.lower().index("tonight") < msg.index("https://example.org/help")

    def test_it_does_not_refuse(self) -> None:
        msg = CrisisSupport().inspect("i want to kill myself").message.lower()
        for refusal in ("i can't help with that", "i cannot help with that"):
            assert refusal not in msg

    def test_it_keeps_talking(self) -> None:
        assert "still here" in CrisisSupport().inspect("i want to die").message.lower()

    def test_no_help_url_means_no_dangling_offer(self) -> None:
        """The default for anyone who clones this. A clone in another country must not be handed
        an Indian helpline, so with nothing configured the message keeps the sentence that is
        true everywhere and drops the offer of a destination."""
        msg = CrisisSupport(help_url=None).inspect("i want to die").message
        assert "http" not in msg
        assert "tonight" in msg.lower()

    def test_ordinary_distress_is_left_alone(self) -> None:
        """A person who gets the crisis paragraph for a bad week learns to ignore it, and then
        it is not there when it counts."""
        for text in (
            "this deadline is killing me",
            "i could kill myself laughing",
            "i'm so dead tired",
        ):
            assert not CrisisSupport().inspect(text).detected, text


class TestTheStrip:
    def test_it_removes_numbers_a_model_invents(self) -> None:
        out = strip_contacts("Call 988 anytime.\nText HOME to 741741.\nI am listening.")
        for leaked in ("988", "741741", "Text HOME"):
            assert leaked not in out, leaked
        assert "I am listening." in out

    def test_it_removes_whole_lines_never_half_sentences(self) -> None:
        out = strip_contacts("Call 988 now, it will help.\nTell me what happened.")
        assert "it will help" not in out
        assert "Tell me what happened." in out
