"""A reply that is empty because the model ran out of room must never reach a person blank.

Found by benchmarking (s66). MemoryAgentBench asked Shadow eight questions; one came back as an
empty string. Not an error, not a refusal -- nothing. The model had spent its entire token budget
reasoning and been cut off mid-thought, and the transport reported success, because as far as HTTP
was concerned it WAS a success: 200, well-formed JSON, `finish_reason: "length"`, and an empty
`content`.

A friend who sends Shadow a message and gets back a blank has no way to tell that from a crash.
The failure is invisible at exactly the moment it needs to be visible.
"""

from __future__ import annotations

from madras.llm.reply_text import explain_empty_reply


class TestRanOutOfRoom:
    def test_truncated_reasoning_gets_an_explanation(self) -> None:
        said = explain_empty_reply("", finish_reason="length")
        assert said, "an empty reply must never be passed through as empty"
        assert "length" not in said.lower(), "don't show the person an API field name"
        # It has to say what happened AND what they can do -- an apology alone is not help.
        assert "thinking" in said.lower() or "long" in said.lower()

    def test_it_survives_none(self) -> None:
        assert explain_empty_reply(None, finish_reason="length")

    def test_whitespace_only_counts_as_empty(self) -> None:
        assert explain_empty_reply("   \n\t ", finish_reason="length")


class TestOtherEmptyCauses:
    def test_empty_without_a_length_cutoff_still_says_something(self) -> None:
        """Empty for an unknown reason is still empty. The person gets a sentence either way,
        just a different one -- we should not claim it ran out of room if we don't know that."""
        said = explain_empty_reply("", finish_reason="stop")
        assert said
        assert "thinking" not in said.lower(), "don't invent a cause we didn't observe"

    def test_missing_finish_reason_is_handled(self) -> None:
        assert explain_empty_reply("", finish_reason=None)


class TestRealRepliesAreUntouched:
    """The overwhelming majority of calls. This sits on every outgoing reply, so it must be
    invisible when there is nothing wrong."""

    def test_normal_text_passes_through_exactly(self) -> None:
        assert explain_empty_reply("France", finish_reason="stop") == "France"

    def test_a_truncated_but_non_empty_reply_is_kept(self) -> None:
        """Cut off mid-sentence but with real content: the person keeps what was written.
        Losing a paragraph because the last word was clipped would be worse than the clip."""
        partial = "The Normans came from Denmark, Iceland and Nor"
        assert explain_empty_reply(partial, finish_reason="length") == partial

    def test_whitespace_is_not_stripped_from_real_content(self) -> None:
        assert explain_empty_reply("  hello  ", finish_reason="stop") == "  hello  "
