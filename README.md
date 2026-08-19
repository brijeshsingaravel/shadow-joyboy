# Shadow

<!-- Three badges, and each one is a fact a reader can click through and check: the workflow that
     actually ran, the licence file in this repo, and the floor in pyproject.toml. No badge here
     for coverage, downloads, "maintained", or a chat room that doesn't exist. A badge is a claim
     in a small rectangle, and the unearned ones are the reason people stopped reading them. -->
[![tests](https://github.com/brijeshsingaravel/shadow-joyboy/actions/workflows/tests.yml/badge.svg)](https://github.com/brijeshsingaravel/shadow-joyboy/actions/workflows/tests.yml)
[![licence: AGPL-3.0](https://img.shields.io/badge/licence-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

An agent that remembers you between conversations, runs on a machine you own, and asks before it
does anything it can't undo.

Shadow is the flagship agent from Madras AI. This repository is Shadow itself
— extracted so you can read it, run it, and decide for yourself whether it does what it says.

**Status: early.** One person built this. It is not battle-hardened by a thousand users yet, and
things will be rough in places.

**What "tested" means here, since the word is cheap:** 110 tests, run on every push. Most of them
check that this README is telling you the truth — that no tool able to send a message is
registered, that the agent's config asks for nothing it cannot have, that the tool count below
matches what you actually get, and that every toolset named here exists. If a promise on this page
stops being true, the build goes red. The tests needing a database skip with a reason rather than
passing quietly, so a green run on a laptop with no Docker says plainly what it did not cover.

---

## What it actually does

**It remembers.** Tell it something today and ask about it next week. Memory is on by default and
is the whole point — a layered store, and retrieval that works on meaning rather than
keywords, so "what did I say about my sister's wedding" finds the conversation where you
called it "the function in December".

**It runs where you put it.** Two backends: a local model through Ollama, or OpenRouter with
your own key. Ollama is the default, and on that setting nothing you type leaves your machine.

**It asks first.** Actions that can't be undone go through a permission check before they run, not
after. That check is part of how the agent is built, not a setting you can forget to switch on.

**It writes down what it did.** Every action lands in an append-only audit log with no update or
delete path.

## What it deliberately cannot do

**It cannot send messages.** No email, no chat, no SMS. This is not an oversight or a missing
feature — the messaging tools are not in this repository at all, because nobody's first experiment
with a new agent should be able to send email as them. If you want that, you add it knowingly.

**It cannot spawn sub-agents.** Delegation is the most interesting idea in the parent project and
it is not here yet. Extracting it cleanly means untangling it first, and shipping it welded to
everything else would be worse than shipping it later.

**It cannot browse, run shell commands, write files, or execute code — until you turn those on.**
Those toolsets ship, switched off.

**Out of the box you get 14 tools:** the 13 memory ones — `remember`, `recall`, `note`, `relate`,
`memory_export` and the rest — plus `clarify`, which asks instead of assuming.

**Turning more on is one line.** Add the toolset to `agents/roles/shadow.yaml`:

```yaml
toolsets:
  - memory
  - clarify
  - search       # ← added
```

Available: `search`, `file`, `file_write`, `shell`, `code`, `web`, `browser`, `mcp`, `discovery`,
`security`.

**They're off because of what a first run means.** `shell` runs commands on your machine and
`file_write` edits your files. Nobody should find out an agent can do those things by watching it
do them. Switching one on should be a decision you make with your eyes open — that's the whole
difference between a tool and a surprise.

Also absent: planning, kanban, scheduling, vision, speech, image generation, media, database
querying, git/CI, root-cause analysis, and computer use.

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/brijeshsingaravel/shadow-joyboy.git shadow && cd shadow
pip install -e ./packages/madras-capabilities   # vendored, not on PyPI — must go first
pip install -e .
cp .env.example .env
```

**Why two commands.** `madras-capabilities` lives in `packages/` and is not published to PyPI, so
`pip` has nowhere to fetch it from and a single `pip install -e .` fails with
`No matching distribution found`. Installing it first solves that. If you use
[uv](https://github.com/astral-sh/uv), `uv sync` handles both in one step, because it understands
the workspace.

Then edit `.env`. The only thing you must set is where the model comes from.

**Running fully local, nothing leaves your machine** — install [Ollama](https://ollama.com), then:

```bash
ollama pull qwen3.5:4b          # the model it thinks with
ollama pull nomic-embed-text    # the model it remembers with
```

and set `MADRAS_LLM_BACKEND=ollama` in `.env`.

**Both models matter, for different reasons.** The first answers you. The second turns what you
say into the numbers that make recall work by meaning rather than by keyword — it is how "my
sister's wedding" finds the day you called it "the function in December". **Without it Shadow
still runs and still remembers, but falls back to matching words**, and nothing will tell you
that has happened.

The hosted Shadow runs this same shape on a machine we own, though with
`nomic-embed-text-v2-moe` as the embedding model rather than the one above. Either works; that
one simply happened to be pulled first.

Memory needs somewhere to live. Postgres for the record and Qdrant for the semantic search:

```bash
docker compose up -d
```

Then create the tables. **Once, and it is not optional** — nothing here creates its own schema,
so without this the first thing you tell Shadow fails with
`relation "madras_memory" does not exist`:

```bash
python -m madras.cli migrate
```

## Run it

```bash
python -m madras.cli chat
```

## The shape of the code

```
src/madras/
  tools/        the 54 tools, and the registry that gates them by rank
  memory/       the layered store — episodic, semantic, reflexes
  mindpalace/   memory per project, kept separate
  memory_manager/  consolidation and forgetting — the logic, not a schedule (see below)
  graph/        the LangGraph topology, checkpointed so a crash resumes
  security/     the permission engine and the irreversible-action list
  audit/        append-only, hash-chained
  eval_/        the signals every action emits
  persona/      identity, and the drift check on it
```

**`memory_manager/` is not wired to a clock in this repo.** The consolidation and forgetting
logic is here and works when called, but nothing runs it nightly — the scheduler is part of the
parent project and was not extracted. If you want it on a timer, that is yours to add. Said plainly
because "the nightly job" would have you expecting your memory to be tidied while you sleep.

`src/madras/tools/builtin/__init__.py` is worth reading first. That import list **is** the
boundary of what this agent can do — a toolset that isn't imported there cannot be switched on by
configuration, only by editing that file. That's deliberate.

## Two different situations, and they aren't the same

**If you clone this,** you run Shadow on your own machine with your own key and your own database.
Unbounded, costs us nothing, and no data of yours ever reaches us. We won't know you did it.

**If you use the [hosted Shadow](https://shadow.outkastcode.com),** you're on our machine,
with our model and your conversations in our database. That's a much smaller group and a
real responsibility, and it's why the two are described separately rather than as one
product.

## Contributing

Genuinely welcome, with an honest caveat about me rather than about you.

This is maintained by one person with a job, a family, and a small number of people already
depending on the hosted version. A reply may take days. Sometimes it won't come at all — not
because your issue wasn't worth answering, but because that week had other claims on it. I'd
rather say that now than have you read silence as a verdict on your work.

Open an issue before a large change, so you don't build something that gets turned down for a
reason you had no way to know.

Bugs, unclear docs, and "this didn't work on my machine" are the most useful things you can send —
especially the last one. Most of what has been fixed here was found by someone using it in a way I
couldn't see from where I sit.

If I go quiet and you need this to keep working: fork it. The licence is AGPL-3.0 precisely so
that you can, and so that whatever you build on it stays open for the next person. That isn't a
failure of the project. It's what the licence is for.

## Security

Found a way past something this README promises? **Email `brijeshsingaravel@gmail.com` rather than
opening a public issue** — see [SECURITY.md](SECURITY.md) for what's worth reporting, what's known
and deliberate, and what to expect back.

## Licence

**AGPL-3.0.** Copyright © 2026 Singaravel Brijesh. See [LICENSE](LICENSE).

**For almost everyone, this changes nothing.** Use it, read it, run it, modify it, build on it. If
you are a person, a student, a researcher, a hobbyist, or a company using it internally, you have
every freedom you would have had under a permissive licence, and you owe nothing.

**It asks for one thing back, from one case.** If you modify Shadow and offer it to others over a
network — as a hosted service — you must make your modified source available to the people using
it. That is section 13, and it is the entire reason this licence was chosen over Apache-2.0.

**Why.** This was written by one person so that an agent which remembers you could exist and be
free to use. The case it guards against is narrow: someone taking it, improving it privately, and
selling those improvements as a service while the people who need it are no better off. **Nothing
here is aimed at anyone building something and sharing it back.**

**If AGPL genuinely does not work for you**, ask. Plenty of companies cannot accept it as a matter
of policy, and that is a real constraint rather than bad faith. A commercial licence is available
— email `brijeshsingaravel@gmail.com` and say what you are building. **The answer is not
automatically a price**, and for something that helps people it may well be yes for nothing.

## Credit

This stands on other people's work, and the debt is written down rather than implied — see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). The short version: LangGraph for durable
orchestration, Pydantic for making configuration verifiable, Postgres and Qdrant for the memory,
Ollama for making a local model a two-line change, and ruff and pytest for keeping it honest.

Built in Chennai.
