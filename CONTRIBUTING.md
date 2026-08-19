# Contributing

The honest expectations — how long a reply takes, and what to do if one never comes — are in the
[README](README.md#contributing). This file is the practical half: how to get the tests running so
you can tell whether your change works before you send it.

## Getting it running

```bash
git clone https://github.com/brijeshsingaravel/shadow-joyboy.git shadow && cd shadow
uv sync
```

If you use `pip` instead of [uv](https://github.com/astral-sh/uv), the order matters and the error
you get otherwise does not explain itself:

```bash
pip install -e ./packages/madras-capabilities   # vendored, not on PyPI — must go first
pip install -e .
```

`madras-capabilities` is vendored rather than published, so `pip` has nowhere to fetch it from and
a lone `pip install -e .` fails on a dependency that is sitting right there in the tree. `uv sync`
does both in one step because it reads the workspace.

Most of the test suite needs no database, no model and no network. That is deliberate: you should
be able to clone this and get a signal in under a minute, without setting up infrastructure to fix
a typo.

```bash
uv run pytest
```

The tests that do need Postgres and Qdrant skip themselves when those aren't there, rather than
failing. If you want to run them:

```bash
docker compose up -d
uv run pytest
```

## Before you open a pull request

**Run both gates, not just the tests.** CI runs the linter as well, and it is the easier one to
forget:

```bash
uv run pytest
uv run ruff check src tests
```

This is not a hypothetical. The commit that added the crisis-support wiring passed every test
locally and still turned CI red, on an import block in the wrong order and one line five
characters too long. Two seconds of `ruff` would have caught both.

## What is most useful

**Bugs, unclear docs, and "this didn't work on my machine."** Especially the last one. Most of
what has been fixed here was found by someone using it in a way the author couldn't see from where
he sits — a text box that didn't work on a phone, a settings page nobody could reach. Those
reports are worth more than they feel like when you're writing them.

**Open an issue before a large change.** Not to gatekeep — to stop you building something for a
week that gets turned down for a reason you had no way to know.

## The one rule about tests

**A test here has to be able to fail.** The convention in this repository is that tests check
behaviour rather than mocks, and that a test asserting something already impossible is worse than
no test, because it reads like assurance. If you add a test, make it fail on purpose once before
you make it pass.

## Security

Please don't open a public issue for a security problem. [SECURITY.md](SECURITY.md) has the
address and what to expect.

## Licence

This project is AGPL-3.0. By contributing you agree your contribution is licensed under it too.
There is no CLA to sign and no copyright to assign — your commit stays yours, under the same
licence as everything around it.
