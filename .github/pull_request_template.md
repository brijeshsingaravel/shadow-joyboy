<!-- Short on purpose, for the same reason the issue form is: a long checklist makes a small,
     correct fix feel like paperwork, and small correct fixes are most of what arrives. Delete any
     line that doesn't apply. -->

**What this changes, and why**

<!-- One or two sentences is plenty. If there's an issue, link it. -->

**How you checked it**

<!-- What you ran, or what you clicked. "Ran the tests" is a fine answer. "I couldn't get the
     tests running but this fixes it on my machine" is also a fine answer — say so and it can be
     checked from this end rather than being a reason not to send it. -->

---

Before sending, if you were able to run them:

- [ ] `uv run pytest`
- [ ] `uv run ruff check src tests` — the easier one to forget, and CI runs both

Neither box being ticked is not a blocker. CI runs both on this pull request anyway, and a red
tick there is information, not a verdict on you.
