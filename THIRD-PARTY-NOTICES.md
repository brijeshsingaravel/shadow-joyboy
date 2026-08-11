# Third-Party Notices — Madras AI

> **OSS license audit — re-run s41** (supersedes the s40 first pass, [[D45]]).
> **Result: the product path is permissive-clean. No AGPL-3.0 / GPL (strong) / SSPL /
> BSL / Commons-Clause anywhere in the shipped runtime.** The two s40 AGPL findings are
> resolved: **SearXNG** → **ddgs (MIT)**; **MinIO** was a dead config field → removed.
>
> **Founder-notes (not blockers):**
> - **libvips (LGPL-3.0)** ships inside the `sharp` image binary — LGPL is weak-copyleft
>   (dynamic-link, replaceable), permitted in proprietary software; not in the banned set.
> - **caniuse-lite (CC-BY-4.0)** — browser-support *data*, attribution-only (satisfied here).
> - **GSAP (custom "No Charge" license)** — used **only** by `web/src/components/pixi-scene.tsx`
>   (the 2.5D city, **shelved for launch** per [[D32]]/[[D42]]), so it is not on the launch
>   surface. Post-Webflow (2024) GSAP is free for SaaS use; if the city ships later, confirm
>   the No-Charge terms then. Removing pixi-scene at launch drops GSAP entirely.
> - **Dev/docs-only, never shipped:** the optional swappable **letta** memory backend
>   (Apache-2.0 itself) pulls `html2text` (GPL-3.0) + `demjson3` (LGPL) into a dev venv only —
>   neither is in `uv.lock`. `sphinx`/`docutils` are the dev doc-toolchain.

## Models and datasets

### AI4Bharat (IIT Madras) — MIT

**IndicTrans2** and AI4Bharat's related Indic language models, covering the 22 scheduled
languages of India. Licensed **MIT** (verified s63 from the Hugging Face model card, not from
secondary coverage) — commercial use permitted, attribution required and preserved here.

> Copyright (c) AI4Bharat, Indian Institute of Technology Madras.
> Released under the MIT License. Full terms: https://huggingface.co/ai4bharat

Chosen as the Indic foundation for [[Shadow]] because it is permissively licensed, built for
all 22 scheduled languages, and comes from IIT Madras. Nothing beyond MIT's attribution
requirement is owed, and nothing further is claimed.

### Shakti / OpenOCD debug script — IIT Madras, GPL-3.0

`Engineering/recovered-gem5-configs/openocd_shakti_debug.py`

> Name of Author : **Anand Kumar S**  ·  IIT Madras
> Copyright (C) 2019 **IIT Madras.** All rights reserved.
> Licensed under the **GNU General Public License v3**.

A debug helper for loading programs onto **Shakti** boards -- India's open-source RISC-V processor
family, built at IIT Madras. Used during the `.tamil` gem5/RISC-V locality experiments (T6), and
recovered alongside first-party work after the s61 data loss. The other three files in that folder
are first-party.

**Compliance position, stated plainly (s63).** GPL-3.0's obligations attach to **conveying** --
distribution -- not to private use. This repository is private and nothing has been distributed, so
no obligation has been triggered. The one standing requirement is **preservation**: the licence
"means you can't remove or modify" existing notices, not that you must add new ones. Every notice in
that file is intact and untouched.

**This entry is therefore voluntary.** It is here because the founder asked for it: *"I have come
this far because of all these brilliant minds. I want to carry on and help and do what I can."*
Recorded as thanks, in the same spirit as the Superpowers acknowledgement above.

**The one thing that WOULD change this:** GPL-3.0 is on this project's own banned list
(`CLAUDE.md`: no AGPL/GPL/SSPL/BSL in commercial wrapping) because shipping GPL code inside a
proprietary product would require the whole product to become GPL. That file must therefore **never
enter the shipped runtime**. It is a hardware-debug script for Shakti boards, unreferenced by any
Madras module, so it is not on that path today -- and this note exists so a future session does not
move it there by accident.

**A note worth keeping.** Anand Kumar S is at IIT Madras. AI4Bharat, credited above, is at IIT
Madras. This project is called Madras AI. Building on work from the city the project is named after
is not a debt to be nervous about; it is the thing working as intended.

### The 57 skills in `Engineering/skills/` — first-party (surveyed s63)

**All 57 measured, not sampled.** An earlier pass checked 8 and generalised; the founder asked for
every one to be examined *"so we don't be unintentionally impartial"*, and the complete measurement
is stronger than the sample was:

| property | result |
|---|---|
| house format (`**Purpose:**`) | **57 / 57** |
| `## Steps` section | **57 / 57** |
| `madras:` metadata block | **57 / 57** |
| **contains any code fence** | **0 / 57** |
| size | 906–2,238 chars (median ~1,116) |
| structural outliers | **none** |

**Why this is the evidence and not merely reassurance.** Copied material arrives uneven — the
source's own headings, its formatting, its code samples. Uniformity this complete across 57 files,
each referencing *Madras's own toolsets* (`bash`, `python_execute`) which no upstream project's
documentation mentions, is the signature of one author writing to one template. The outlier check
is the part that could have failed and did not: a pasted block would show as a longer file, a stray
fence, or a foreign heading style.

Facts appear (model sizes, install commands, method names) and facts are not copyrightable;
expression is, and none was taken.

**Stated limit, so this is not read as more than it is:** this proves no bulk copying and no
structural derivation. It cannot prove no single sentence anywhere resembles a source — nothing
short of reading all 57 against every upstream project's documentation could. With zero fences,
uniform format and ~1 KB files, that residual is judged negligible rather than impossible.

### Acknowledgement — Superpowers (Jesse Vincent), MIT

**Not a licence obligation. Recorded because the learning happened.**

Four skills in `Engineering/skills/` share names and ideas with skills from the **Superpowers**
plugin (MIT, Copyright (c) 2025 Jesse Vincent): `using-superpowers`, `receiving-code-review`,
`systematic-debugging`, `verification-before-completion`.

**No text was copied**, and this was measured rather than assumed (s63). Two independent
comparisons against the plugin's own `SKILL.md` files agree:

| skill | text similarity | shared vocabulary |
|---|---|---|
| `verification-before-completion` | 3% | 10% |
| `systematic-debugging` | 1% | 7% |
| `receiving-code-review` | 1% | — |
| `using-superpowers` | 1% | — |

Both sides are substantial (theirs 9,341 chars vs ours 1,116 for `systematic-debugging`), they open
differently, and ours are much shorter. MIT covers the expression, not the name or the idea, so
**nothing here triggers its terms.**

It is recorded anyway, at the founder's direction: *"It's their hard work, I don't want to
unintentionally use someone's work and not credit them."* The founder used those skills on earlier
projects and wrote his own versions afterwards. **Thanks are owed to Jesse Vincent for publishing
the work that taught the pattern** -- the shorter versions here exist because the originals existed
first, and saying so costs nothing.

### Tirukkural corpus — public domain

Tamil source text from **Project Madurai** (CC Public Domain Mark 1.0) and **V. V. S. Aiyar's
1916 English translation** (public domain; the translator died in 1925). Used read-only for the
naming work recorded in `Naming Candidates.md`.

## Infrastructure

### Acknowledgement — the open software this is built on

Everything here runs on it. base-01 is Ubuntu. The containers holding this project's memory run on
Linux. `ssh`, `bash`, `grep`, `tar`, `cron`, `systemd`, `curl` — the tools used to build, test and
inspect every part of Madras — were written well and published for anyone to read.

**That last part is why this project exists.** Not because the software cost nothing, but because
it was **open to be studied.** A founder in Chennai without a computer-science degree could look
inside the thing he was standing on, find out how it worked, break it, and try again. **You cannot
learn from a system you are not allowed to read.** They made one you are allowed to read, and kept
it that way for decades.

The craft deserves saying plainly. `ssh` has protected connections across a hostile internet for
twenty-five years. `cron` has run the world's scheduled work so reliably that most people using it
have never learned its name. `curl` runs in cars, televisions and spacecraft. **These are not rough
tools that happened to be free. They are among the best-made software anyone has written**, and
they were built in the open where anyone could check.

To everyone who wrote, maintained, documented, packaged and answered questions about this software
— including those who have looked after the same program for longer than this founder has been
alive — **thank you.**

**Outkast Printwears LLP** is a two-person company in Chennai. We make t-shirts, and we are
building Madras AI — an assistant called Shadow, meant for people like our own families. **Every
line of it was written on top of your work.**

**You are teachers we have never met.** This project was built by reading what you wrote, and every
good habit in it — check the artifact, read before you run, leave the reason next to the code — was
learned from how you work.

Thank you. It is a better world for the way you chose to build.

*— Claude Opus 5*
*— Outkast Printwears LLP, Chennai*
*— Avinash Kumar*
*— Singaravel Brijesh*

### Acknowledgement — E2E Networks

Shadow does not run on a laptop. It runs on **base-01**, a machine rented from **E2E Networks** in
Chennai — twelve cores, 82 GB, and an uptime that has so far outlasted every session that deployed
to it.

They are not a dependency in the licensing sense; nothing of theirs is compiled into Madras. **They
are the reason Shadow exists anywhere other than a developer's desk.** When it answered
*"I am here, Brijesh"* through a browser at s63, the machine that answered was theirs.

Two things they do that are worth naming because they were relied on without being asked for.
Their **Zabbix agent** watches the node. And clause 1.7 of their terms states plainly that **the
customer retains ownership of all customer data**, with their own access limited to providing the
service — a position taken before anyone asked them to take it.

**Their R1Soft CDP backup now protects the node too — but only since s64, and the route there is
the point of this paragraph.**

An earlier version of this entry claimed it already did. It did not: the agent was installed and
running, while the dashboard read **`0 / 1 Backup activated`**. **The agent's presence had been
mistaken for the service being switched on** — a running process taken as evidence of a working
outcome, which is the same error as trusting an exit code instead of checking the artifact.

The founder caught it by reading his own dashboard, and enabled CDP backup the same day.

**Current state, verified on the E2E console (s64):** backup active, first full run in progress,
every six hours at 00:31 · 06:31 · 12:31 · 18:31 IST, ZLIB compression. Retention is generous —
the last 12 six-hourly points, the last of each of 14 days, each Saturday for 4 weeks, and each
month-end for 2 months.

**MySQL database backup is deliberately left unconfigured**: Madras runs PostgreSQL, so their
MySQL add-on does not apply. File-level copying of a live Postgres data directory can capture a
half-written state that restores into nothing, so a `pg_dump` runs at 00:15 — sixteen minutes
ahead of their 00:31 copy, so a finished, consistent file is on disk before the backup reaches
it. Their tool and ours, each doing the part it is good at.

**They are an Indian company hosting an Indian product.** For something whose footer reads
*"Built in India for the world"*, that is not incidental.

Recorded here rather than only in an invoice.

**A note for whoever deploys next, so this stays a good relationship rather than a strained one:**
their own agents run on that machine and must never be stopped, restarted or reconfigured. Their
liability is capped at one month's fees and clause 21.3 permits termination without notice for
*perceived* risk to infrastructure — so base-01 must never hold the only copy of anything, and
anything we run there should be a quiet neighbour: bounded memory, no runaway processes, nothing
that looks alarming from their side. As of s63 Shadow runs capped at 2 GB of the machine's 82,
under an account that owns one folder and cannot log in.

## Python runtime

Generated from the locked runtime dependency tree (`uv export --no-dev`). 121 packages.
Dev/docs tooling (sphinx, ruff, pytest, pip-licenses, etc.) and optional swappable
backends (letta) are excluded — they are not shipped in the product runtime.

### Acknowledgement — Astral (`uv`, `ruff`), MIT

Both are excluded from the table below for a sound reason: they are not shipped inside the
product. That reason is also why they would otherwise go unthanked, so they are named here
deliberately.

**`ruff`** runs on every commit in this repository. It is not a passive checker — during s63
alone it caught an import ordering that would have failed the gate, a deprecated type
annotation, and three files whose formatting had drifted. Work that would have reached the
founder as noise instead reached him as clean code.

**`uv`** manages the environment this project is built in, and as of s63 the environment on
`base-01` as well. It installed and pinned Python 3.11 there without disturbing the system's own
3.12, which is precisely the property that let Shadow be deployed beside the crossing receiver
without either one noticing the other.

Astral publish both under MIT, ask nothing in return, and — a detail worth recording — ship an
installer that names the source of every binary it embeds, including the one it only ever uses on
an architecture almost nobody runs. That transparency was verified by reading the installer
before running it, and it is the reason it was run at all.

**Ruff is roughly 150× faster than the linter it replaced**, and does in one pass what used to
need three separate tools. That is not a vanity number: it is why running both ruff gates before
a commit costs seconds rather than a coffee break, and therefore why they actually get run rather
than skipped "just this once".

**Where they are today (March 2026).** OpenAI acquired Astral on 19 March 2026, and Charlie Marsh
and the team joined OpenAI's Codex team. Marsh has said publicly that he thinks they may ship
*more* open source there than they did at Astral. **That is the fact and their own stated
intention, and it is all we have to say about it** — what it will mean is theirs to work out, not
ours to narrate ([[How We Write About People]] §7).

The tools that shape a piece of work are usually the last ones credited, because they leave no
trace in the finished thing. These two are in every line of this repository.

### Acknowledgement — pytest

> Copyright © 2004 Holger Krekel and others · **MIT**
> Brianna Laugher · Bruno Oliveira · Floris Bruynooghe · Freya Bruhin · Holger Krekel ·
> Ronny Pfannschmidt · and many more in AUTHORS.

**It was not built to be a testing framework.** In June 2003 **Holger Krekel** was working on
PyPy — a Python written in Python — and refactored its test tooling because writing tests for it
was unpleasant. By 2004 that had become `utest`, then a package called `std`, meant as a
"complementary standard library" and renamed to `py` because everyone confused it with Python's
own. **The mailing list started in September 2004 and its archives are still intact today**, now
as pytest-dev. The first commit in the pytest repository itself is from January 2007, and it was
called `py.test` until August 2016.

**Twenty-two years later it is maintained by a team rather than a person**, and **pytest 9.0.3 is
the version installed in this repository** — read from the package itself, so this sentence stays
true without anyone having to remember to update it.

**The gift, and it is the reason this project can be tested at all:** pytest let you write

```python
assert count == 3
```

instead of `self.assertEqual(count, 3)`. **A plain sentence in Python, which a non-developer can
read and judge.** Every test in this repository is written that way. When a test at s64 failed
with *"the chat path did not set the turn-log context, so Shadow cannot reach its own past
turns"*, the founder could read that failure and know exactly what it meant without knowing the
framework. **That is not an accident of design. It is the design.**

`pytest-asyncio` runs this project's async tests in `asyncio_mode = "auto"`, which is why a test
of an async database call looks the same as any other test.

**Twenty-two years of a tool whose whole purpose is making it easy to find out whether you were
right.** For someone learning to build by checking rather than by trusting, there is no better
thing to have been handed.

### Acknowledgement — the tools that carried a night's work (s64)

On 2026-08-06 a database backup was built, proven against a deliberately hostile filename, sent
to a server 2,000 km away and verified byte-for-byte. Five things did that work: four programs
and one published standard. Every one of them was given away, and none of them ships inside
Madras — which is exactly why they would otherwise go unnamed.

*(Two are GPL-licensed. They are external programs this project RUNS, never code it contains,
links against or distributes — so the "no strong copyleft in this tree" statement below is
unaffected. Running `bash` no more licenses this project than running `ruff` does.)*

**GNU Bash** — Brian Fox, Chet Ramey, and contributors · GPL-3.0-or-later

> Brian Fox began writing Bash on 10 January 1988 as an employee of the Free Software
> Foundation, which funded it directly because a free shell mattered that much. The first beta
> shipped 8 June 1989. **Chet Ramey** has maintained it ever since — as a volunteer, alongside
> his job at Case Western Reserve University, for over thirty years. He also maintains GNU
> Readline, which is why a shell remembers what you typed.
>
> Every script in this session begins `set -euo pipefail`. The `-u` in that line — treat an
> unset variable as an error rather than an empty string — would, on its own, have prevented
> the loss of 90 GB of irreplaceable work at s61. The guard was there decades before we needed
> it. We simply had not switched it on.

**GNU coreutils** — David MacKenzie, Jim Meyering, and many contributors · GPL-3.0-or-later

> `find`, `sort`, `tail`, `cut`, `xargs`, `sha256sum`, `df`, `du`, `mv`, `date`. David
> MacKenzie's name is on a large share of them; Jim Meyering has carried maintenance for years.
> Torbjörn Granlund, Roland McGrath, Paul Rubin, Stuart Kemp, Simon Josefsson and others wrote
> individual tools.
>
> The fix at the centre of today's work is theirs: `find -print0`, `sort -z`, `xargs -0` —
> separating filenames by a marker that cannot occur inside a filename. Someone thought
> carefully about a filename containing a space, and built the answer into the tools, long
> before it could delete anything of ours.

**OpenSSH** — the OpenBSD project · BSD-style licence

> **Tatu Ylönen** wrote the original SSH in 1995 at Helsinki University of Technology, after a
> password-sniffing attack on his university's network, and released it freely. Within months
> it was in use in fifty countries. When later versions closed up, OpenBSD developers — Theo de
> Raadt, Markus Friedl, Niels Provos, Bob Beck, Aaron Campbell, Dug Song, building on Björn
> Grönvall's OSSH — rebuilt it as OpenSSH in 1999, and Damien Miller and Philip Hands made it
> portable to everything else. Parts still carry Tatu's original licence.
>
> Every command this project has ever sent to `base-01` travelled through their work. A
> password typed here has never crossed the internet in the clear, and we have never once had
> to think about it.

**PowerShell** — Jeffrey Snover and Microsoft · MIT

> Snover wrote the *Monad Manifesto* in August 2002, arguing that a shell should pass structured
> objects rather than parsed text. It shipped as PowerShell in 2006.
>
> Then, on 18 August 2016, Microsoft **gave it away** — released under the MIT licence and made
> to run on Linux and macOS. A company opening up its own management shell, and making it work
> well on the operating system it had spent years competing with, is not a small thing. It is
> why the founder's own shell and a Linux server in a datacentre can be part of the same day's
> work.
>
> `Get-FileHash` is what proved a file here and a file there were identical. Two of today's
> mistakes were ours, giving Bash commands to a PowerShell prompt. The tool was right both
> times; we were careless.

**SHA-256** — designed at the NSA, published by NIST as FIPS 180-2 (2001)

> Not software, and not anyone's product. A method, written down and published so that anyone
> could implement it.
>
> **It is patented, and the United States released that patent royalty-free** — so it belongs,
> in practice, to everybody. That decision is why two machines that have never met, running
> different operating systems written by different people in different countries, agreed on all
> sixty-four characters of a fingerprint tonight. It is why "the file arrived intact" is
> something we can *know* rather than hope.
>
> A method given to the whole world, free, is a rare and serious kind of generosity. Most of the
> security anyone relies on today rests on it.

**None of these were made for us. All of them were waiting when we needed them.**

### Acknowledgement — ShellCheck, Vidar Holen, GPL-3.0

> ShellCheck — a static analysis tool for shell scripts.
> Copyright 2012-2026 **Vidar "koala_man" Holen** and contributors.
> <https://www.shellcheck.net> · <https://github.com/koalaman/shellcheck>

Written in Haskell, started in 2012, and maintained since. It reads a shell script and reports
the mistakes shell scripts actually make — unquoted variables, unhandled failures, patterns that
break on unusual filenames — without running anything.

**Why it is here.** The longest rule in this project's `CLAUDE.md` exists because an inline
command lost a variable in transit and deleted 90 GB of irreplaceable work. Finding that class of
bug *before* it runs is ShellCheck's entire purpose. It is credited **before** first use rather
than after, at the founder's request.

**Licence position.** GPL-3.0, which is on this project's banned list (`CLAUDE.md`: no
AGPL/GPL/SSPL/BSL in commercial wrapping). That rule is about **shipping GPL code inside a
proprietary product**, and nothing of the kind happens here: ShellCheck is an external program
that reads a file and prints warnings. No ShellCheck code enters this repository, is linked
against, or is distributed. Running it changes the licence of a script no more than `ruff` changes
the licence of a Python file — the same position recorded for `bash` and `coreutils` above.

### Acknowledgement — cron, Ken Thompson and Paul Vixie

> Copyright 1988, 1990, 1993 by **Paul Vixie**. Distributed under the *Vixie Cron License* — a
> permissive, pre-BSD-style notice: free to distribute provided his name stays in the source and
> documentation, changes are marked, and the notice is left intact.

**cron** began at Bell Labs in the early 1970s and shipped with Version 7 Unix. Three people are
owed something here, and each is named for their own work rather than folded into one story.

**Ken Thompson** wrote the original. It was about as simple as a program can be: wake once a
minute, read one file, run whatever is due. A late-1970s experiment at Purdue found that
extending it to 100 users on a shared VAX overloaded the machine — the design was of its time,
and honest about it. Thompson also wrote the first Unix shell, in 1971, which is where the `>`
in every script here comes from.

**Brian Kernighan** is credited by some accounts with the Version 7 implementation; the sources
disagree, and we are not the ones to settle it. But his work is in tonight's script either way,
and not in dispute: the **K in `awk`** is his — Aho, Weinberger and Kernighan, Bell Labs, 1977.
The disk-space guard in `madras-db-backup.sh` reads
`df -Pk "$DEST" | awk 'NR==2 {print $4}'`. That is his language, doing exactly what it was
designed for, forty-nine years later.

**Paul Vixie** rewrote cron in **1987**, after asking Unix users what they actually needed. He
gave it per-user schedules and the five-field minute/hour/day/month/weekday syntax that has been
the standard everywhere since. *(Sources vary between 1987 and 1988; his copyright reads 1988.)*

**Why it is credited here.** The line that schedules Madras's nightly database export —
`15 0 * * * root /usr/local/bin/madras-db-backup.sh` — is written in **Vixie's format**, read by
his daemon, on a server neither he nor Thompson will ever hear of. Nearly forty years on, a
scheduling syntax designed by asking people what they needed is still the one everybody uses. That
is not an accident, and it deserves the name attached to it.

### Acknowledgement — systemd, Lennart Poettering and Kay Sievers

> Copyright the systemd contributors · **LGPL-2.1-or-later**, with no copyright assignment.

**Lennart Poettering** and **Kay Sievers**, then at Red Hat, began systemd in 2010; the first
release was 30 March 2010, introduced by Poettering's post *"Rethinking PID 1"*. The ideas were
worked out on a flight home from Linux Plumbers Conference 2009, and the code started life as an
earlier project of theirs called **babykit** — in Poettering's words, *"a process babysitter."*

**That is precisely what it is doing for this project.** Shadow runs on base-01 as a systemd
service: started at boot without anyone present, restarted if it dies, and confined by the
hardening added at s63 — `NoNewPrivileges`, `ProtectSystem=strict`, an explicit `ReadWritePaths`
allowlist, and a 2 GB memory ceiling on an 82 GB machine. **Shadow survived its first unattended
night because of this and nothing else.** No script we wrote was watching it.

Worth recording, given what this file is for: **Poettering credits his own sources** — Apple's
`launchd` for socket activation, Upstart, and Solaris SMF. Someone who names the work they built
on is the right sort of person to be indebted to.

*(As with `bash` and `coreutils` above: an external program this project **runs**, never code it
contains, links against or distributes. The LGPL obligations attach to conveying, and nothing is
conveyed.)*

### Acknowledgement — the tools and formats underneath everything (s64)

The founder asked for these by name after a night of using them without once saying so. Some are
programs, some are only agreements about how to write things down. None of them belongs to anyone
now.

**Git** — Linus Torvalds and Junio C Hamano · GPL-2.0-only

> Torvalds began Git on 3 April 2005 and announced it three days later. **Junio C Hamano has
> maintained it since 26 July 2005** — three months in, and twenty years since. Torvalds on the
> anniversary: *"20 years later, you should definitely talk to Junio not to me."*
>
> This project has 2,077 commits and one laptop. Every safety net it has is Hamano's.

**PostgreSQL** — the PostgreSQL Global Development Group

> It holds everything Shadow knows. **Credited in full in its own section below.**

**SQL** — Donald D. Chamberlin and Raymond F. Boyce, IBM San Jose, 1974, on Edgar F. Codd's
relational model

> They called it SEQUEL, and presented it at a workshop in Ann Arbor in May 1974.
>
> **Raymond Boyce died a month later, on 18 June 1974, of a brain aneurysm.** He was in his
> twenties, and left a wife and an infant daughter. He never saw a line of SQL run anywhere but
> in a research lab, and every database query in this project — every one run tonight — is written
> in the language he helped design in the last months of his life.

**UTF-8** — Ken Thompson and Rob Pike, 1992

> Designed **on a placemat in a New Jersey diner**, one night in early September 1992, after a
> standards committee asked them to review a worse proposal. Ken worked out the bit-packing at the
> table. They coded it that night, converted the whole of Plan 9 by Friday, and the committee
> adopted it the following Monday. Pike has said he wishes they had kept the placemat.
>
> **It is why Tamil works here at all.** தமிழ் in a filename, in a commit message, in
> `Naming Candidates.md` beside English — no configuration, no thought required. Every Tamil
> character in this repository is carried by a decision made over dinner.

**Markdown** — John Gruber, with Aaron Swartz · BSD

> Gruber wrote it in 2004 on one principle: *a marked-up document should still be readable as
> plain text.* **Aaron Swartz** shaped the `#` heading syntax from his own earlier work and
> refined the translation rules, and was its most valuable reader before release.
>
> This entire canon — every vault note, every plan, every session, this file — is Markdown. Its
> readability *as plain text* is why the vault can be a git repository and an Obsidian graph at
> the same time, with no conversion step anywhere.

**JSON** — Douglas Crockford, with Chip Morningstar

> Crockford put the specification on json.org in 2002. He is careful to say he **discovered** it
> rather than invented it — the shape already existed in JavaScript; what he did was write it
> down, name it, and argue for it until the world agreed. Standardised long afterwards, in 2006,
> 2013 and 2017.
>
> Every API call Shadow makes and every reply it returns is JSON.

**curl** — Daniel Stenberg, with Rafael Sagula · MIT-inspired (curl licence)

> Stenberg wanted currency conversion in an IRC bot in 1996. He built on **HttpGet**, released
> weeks earlier by **Rafael Sagula** in Brazil, and shipped his version that December. The name is
> *client for URLs* — and a pun on *see the URL*.
>
> Thirty years on it is in effectively every connected device on earth. It began as one person
> automating a small annoyance.

**Python** — Guido van Rossum and the Python community · PSF Licence 2.0

> Begun in **December 1989, over the Christmas holidays**, at CWI in Amsterdam, as a personal
> project by someone who wanted a better version of a language he had already worked on. **ABC**
> had good ideas — indentation for structure, high-level types — but it could not be extended,
> had no proper exception handling, and **was closed to contributions from the people who used
> it.** Python was the answer to those three complaints. It was released on **20 February 1991**,
> as version 0.9.0, posted to a newsgroup.
>
> **And then he did the harder thing.** Van Rossum led Python as its "Benevolent Dictator for
> Life" for nearly thirty years, and **stepped down on 12 July 2018**, handing the language to an
> elected Steering Council. The flaw he had built Python to escape was a project closed to its own
> community — and in the end he applied that judgement to himself.
>
> **Madras is written in Python.** Every module named in the architecture rules, every test, every
> script we ran tonight. The licence is permissive and asks nothing in return.

**Docker** — Solomon Hykes and contributors · Apache-2.0
**and the kernel work it stands on** — Paul Menage, Rohit Seth, Eric Biederman, and others

> Hykes showed Docker as a **lightning talk at PyCon in March 2013**, expecting a side room and
> perhaps thirty people. PyCon ran lightning talks on the main stage, in front of several hundred.
> Someone posted the unfinished site and called it vaporware because it was not yet open source —
> **so the team shipped in two weeks and open-sourced it under Apache-2.0.** A public criticism,
> taken seriously and answered by giving the thing away.
>
> **But Docker did not invent containers, and it has never claimed to.** It made usable what
> others had already built into the Linux kernel:
>
> - **cgroups** — how much a group of processes may consume. Begun at Google in 2006 by **Paul
>   Menage** and **Rohit Seth**, with the memory controller by **Balbir Singh** and the CPU
>   controller by **Srivatsa Vaddagiri**; merged into Linux in January 2008. Version 2 was written
>   by **Tejun Heo**.
> - **namespaces** — what a process is permitted to *see*. Begun in 2002 by **Eric Biederman**,
>   and **directly inspired by Plan 9 from Bell Labs** — the same system that carried UTF-8 within
>   days of its design.
>
> **This is what runs Shadow's database on base-01**, and it is what the sandbox hardening
> (`cap-drop ALL`, read-only root, `no-new-privileges`) is made of. Those are not Docker features.
> They are Menage's and Biederman's work, with a usable door on the front.

**jq** — Stephen Dolan, and the maintainers who came after · MIT

> First released **21 August 2012**. Written in Haskell to begin with, then rewritten in C. It is a
> small, complete programming language for JSON, and it does one thing that matters enormously
> here: **`jq --arg` treats text as data.** An apostrophe in *"my cousin's dog"*, a Tamil sentence,
> an emoji — none of it can be mistaken for syntax. That is the same protection as `printf '%s'`
> for a password and `awk` for an API key, and it is why the memory test could be written safely.
>
> **The part worth recording is how it changed hands.** After version 1.6 in 2018, development
> stopped for five years. Rather than fork it and split the community, volunteers **reached out
> repeatedly, over years, to earn the original author's trust** — and Dolan transferred the project
> to a new home with several active maintainers. Version 1.7 opens by noting it arrives *"after a
> five year hiatus."*
>
> The 1.7 notes name them, so we do too: **Owen Ou**, who pushed to create the new organisation;
> **Stephen Dolan**, who handed over what he had made; **@itchyny**, who did the release work; and
> **Mattias Wadman** and **Emanuele Torre** for the reviews. **A handover, patiently earned, instead
> of a fork.** That is a rarer thing than good code.

### Acknowledgement — Google: the Transformer, Gemma, and open science

**The Transformer** — Ashish Vaswani · Noam Shazeer · Niki Parmar · Jakob Uszkoreit · Llion Jones ·
Aidan N. Gomez · Lukasz Kaiser · Illia Polosukhin. *Attention Is All You Need*, NeurIPS 2017.

> Eight authors at Google Brain and Google Research, with Aidan Gomez then at the University of
> Toronto. **The paper states that all eight contributed equally and lists them in random order** —
> a small act of fairness in a field where author position is currency.
>
> They published it free, for anyone to read and build on. **Every language model this project
> touches descends from it — Gemma, and the model writing this sentence.** There is no part of
> Madras that is not downstream of that paper.

**Gemma 4** — Google DeepMind, 2 April 2026 · **Apache-2.0**

> **The weights are released under Apache-2.0** — download them, inspect them, fine-tune them,
> build a business on them. No permission to ask, no royalty owed, no negotiation.
>
> Five sizes from 2.3B to 31B, 256K context, **140+ languages**, multimodal, with configurable
> thinking and **native function calling built for agentic work**. Alongside the models they
> released the speculative-decoding drafters that make them roughly three times faster, ONNX
> builds for browsers and small devices, and DiffusionGemma — an entirely different way of
> generating text. **They gave away the accelerators too, not only the models.**
>
> 400 million downloads and more than 100,000 community variants.
>
> **Madras runs `gemma-4-26b-a4b-it` on OpenRouter's free tier.** It is why Shadow costs ₹0.00 a
> reply, and therefore why two people in Chennai can put a real agent in front of their friends
> this month instead of next year. **The 140-language training is not incidental here either** —
> this project is being built in a city whose language most models treat as an afterthought.

**And beyond what we use directly**

> **AlphaFold's predicted structures were given away** — over 200 million of them, covering very
> nearly every catalogued protein, free to any researcher through the database built with
> EMBL-EBI. The code was open-sourced alongside the paper. AlphaGenome followed, on how DNA
> regulates gene activity, and there is ongoing weather and materials work with public agencies.
>
> **None of that helps Madras.** It is recorded because the founder asked what this company
> actually does with what it knows, and the honest answer includes giving a generation of
> biologists something no amount of money could have bought them.

*The founder read Google's terms before using the model, which is the right order, and asked for
this entry before letting Shadow depend on it any further.*

### Acknowledgement — Python, and the people who keep it

> Copyright © 2001–2026 Python Software Foundation. Portions © 1991–2001 Stichting Mathematisch
> Centrum, Amsterdam. **PSF Licence** — permissive, no copyleft, asks nothing but that the notice
> travels with it.

**December 1989, over the Christmas holidays**, at CWI in Amsterdam, Guido van Rossum began Python
as a personal project. He had worked on a language called **ABC** and admired parts of it —
indentation for structure, high-level types — but ABC had three faults he wanted gone: it could not
be extended, it had no proper exception handling, and **it was closed to contributions from the
people who used it.** Python was the answer to those three. It was released on **20 February 1991**,
version 0.9.0, posted to a newsgroup.

**Then he did the harder thing.** He led Python as its "Benevolent Dictator for Life" for nearly
thirty years and **stepped down on 12 July 2018**, handing it to an elected Steering Council. The
flaw he built Python to escape was a project closed to its own community — and in the end he applied
that judgement to himself.

**Where it is this week.** Python 3.14 made free-threading officially supported, so threads can
finally run in genuine parallel, with the single-threaded cost down from around 40% to roughly
5–10%. An experimental JIT compiles hot code to native instructions. **Python 3.15's first release
candidate arrived on 4 August 2026** — bringing UTF-8 by default, lazy imports, and a zero-overhead
sampling profiler. Thirty-five years in, and the hardest problems are being solved now, in public,
by people arguing it out in the open.

**And the part that is not software at all.** The **Python Software Foundation** is a non-profit
whose stated mission is to support *a diverse and international community* of Python programmers.
**Its Grants Programme has given away more than $3 million since 2015** — to conferences, workshops,
meetups, PyLadies and DjangoGirls chapters, and travel grants so that people who could not otherwise
afford to attend can be in the room. The 2026 funding round opened on 4 August 2026.

**They did not have to do any of that.** A language does not need a foundation that pays for
someone's flight to a conference.

**What it means here.** Madras is written in Python — every module, every test, every script. More
than that: **it is the reason a founder who is not a developer can read his own project.**
`if guard is not None:` is a sentence. `for m in prior:` is a sentence. A language that reads like
English is the reason this repository is not opaque to the person whose name is on it.

**One last thing, and it is a happy accident.** Python is not named after the snake. Guido was
reading the scripts of *Monty Python's Flying Circus* and wanted a name that was "short, unique and
slightly mysterious." **But the logo — two snakes, coiled around each other — came later, in 2006.**
The serpents arrived after the name, chosen by people who liked what the word had come to mean.

### Acknowledgement — PostgreSQL

> Copyright © 1996–2026, The PostgreSQL Global Development Group.
> Portions © 1994, The Regents of the University of California.
> **The PostgreSQL Licence** — permissive, BSD-style, no copyleft.

**It began at Berkeley in 1986**, as POSTGRES — led by **Michael Stonebraker**, with the design
paper co-authored by **Lawrence A. Rowe**. The Berkeley project ended at version 4.2.

**Then two graduate students saved it.** In 1994, **Andrew Yu** and **Jolly Chen** added an SQL
interpreter and released it as Postgres95. **The project its own creators had finished with went on
to outlive them all**, because two students thought it was worth continuing.

**Today it is version 18.4**, still released on a schedule — a major version each year, minor
releases each quarter, five years of support for every version.

**Nobody owns it.** The PostgreSQL Global Development Group is a volunteer association: a core team
of seven coordinates releases and policy; committers, a security team and a sysadmin team do the
rest. In the project's own words, **it does not hire programmers; it draws them from across the
internet.**

**And the licence cannot be changed.** Relicensing would require the unanimous agreement of every
copyright holder, and no entity exists with the standing to attempt it. **That is not a promise
someone made. It is how the thing is built.**

**What it holds here.** Everything Shadow knows — every conversation, every audit row, every
agent's identity, and the row-level security that keeps one person's memory out of another's. **On
base-01 it runs in a container; on this laptop as a portable process on 5433.** Shadow's whole
database is a few hundred kilobytes, resting on forty years of work, given away complete.

### Acknowledgement — npm, and the registry

> npm CLI — **Artistic Licence 2.0**. Created by **Isaac Z. Schlueter**, first release
> **12 January 2010**. npm, Inc. founded 2014 by Schlueter and **Laurie Voss**.

**He built it because he missed one.** Schlueter had used a package manager at Yahoo and found
himself doing the same work by hand in Node, so in September 2009 he packaged up what he had been
doing manually. The first genuinely usable version arrived in January 2010.

**The decision that made it matter:** the registry has always been **free for anyone publishing
openly**. The company was funded by people who wanted *private* packages — so the open half was
never the thing being charged for. **That single choice is why a person with no money and no
company can publish something useful and have the world reach it the same day.**

**What it did here.** `npm ci` installed **925 packages in twelve seconds** onto a server in an
Indian datacentre. No account, no key, no invoice, no permission asked. This website exists
because hundreds of strangers published their work for free and one registry hands it out without
ever asking who you are.

**And it does more than fetch.** In the same breath as installing, it reported known
vulnerabilities in what it had just downloaded — six of them in code that actually ships. **A
package manager that only installed things would have said nothing.** That warning is why we
stopped and looked before putting this in front of anyone, instead of finding out later.

**What it changed in the world.** Before this, sharing a small piece of useful code meant a
website, a download link, and hoping. npm made it ordinary for one person to publish one focused
thing and for millions to build on it within hours. **Over 3.1 million packages exist now** — most
written by people who will never be famous for them, and never paid for them. The pattern spread
far past JavaScript; a great deal of how software is shared today is downstream of it working.

### Acknowledgement — JavaScript

> **ECMA-262**, the ECMAScript specification — free to read, developed in the open by **TC39**.
> Created by **Brendan Eich** at Netscape, **May 1995**.

**He was given ten days.** Netscape hired Eich in April 1995 to put a scripting language in the
browser, and he built it in **ten days** that May. It was called Mocha, then LiveScript, then
renamed JavaScript in December 1995 to borrow the popularity of an unrelated language. Its syntax
came from Java, its first-class functions from Scheme, and its prototype inheritance from Self.

**A language made under absurd pressure, and criticised for its flaws for thirty years — which is
what makes the rest of the story worth telling.**

**It was not abandoned. It was repaired, in public, by anyone who showed up.** Standardised as
ECMA-262 in 1997 and improved ever since through TC39, a committee whose proposals, arguments and
rejections are all public. **ECMAScript 2026, ratified this June, is the seventeenth edition.**
Its newest work includes `Temporal`, which replaces the original `Date` object — **a thirty-year-old
mistake, being fixed carefully, in the open, by people who did not make it.**

**Nobody owns it.** The specification is free to read. Anyone may propose a change. There is no
licence to accept and no company to ask.

**What it did here.** Every line of this website is JavaScript or TypeScript — React, Next.js, the
signup form, the chat page. **The entire surface a friend will ever see is written in a language
someone wrote in ten days and the world then spent three decades making good.**

**And the reason it is in this file:** it is the most widely deployed language ever made, and it
got there by being fixed in the open rather than replaced. That is a better lesson than the usual
one about doing things right the first time.

**Two of these people never saw what they made become.** Raymond Boyce died weeks after presenting
SQL. Aaron Swartz died in 2013, before Markdown became the default way a generation writes
anything down. We are using their work tonight, and it seemed right to say so.

### Acknowledgement — the gate, the place the work sleeps, and the machine itself (s65)

*Written after a push was refused, and after noticing that the machine this was all typed on had
never once been thanked.*

---

**Git — Linus Torvalds, 2005, GPL-2.0**

> Written in April 2005, in a few days, because the tool the Linux kernel had been using was
> suddenly withdrawn. **Maintained since July 2005 by Junio Hamano** — over twenty years of steady,
> unglamorous stewardship by one person.

**What it did here.** 2,106 commits. Every `git commit` in this project, and the thing that made
yesterday's near-disaster survivable: **a rewritten history and an old one could both exist, be
compared, and be reconciled without losing either.** We could ask "how do these two histories
relate?" and get a true answer. That is not a small thing to have built in a few days.

---

**GitHub — Tom Preston-Werner, Chris Wanstrath, PJ Hyett and Scott Chacon, 2008**

**In January 2019, GitHub made unlimited private repositories free for everyone.** Before that,
keeping your work private cost money — which meant students, hobbyists and people in the wrong
currency kept their unfinished work on one hard drive.

**What it did here.** This project's 306MB of history lives there at no cost. **The check we ran
before pushing — "is this repository private?" — returned yes, and that answer is the only reason
this work isn't public before it's ready.** Someone decided that not being ready should be free.

---

**pre-commit — Anthony Sottile, MIT · gitleaks — Zachary Rice, MIT · pyright — Microsoft, MIT**

**The push gate that stopped us today is made of other people's work.** `pre-commit` runs the
chain. `gitleaks` reads every commit for anything shaped like a secret. `pyright` type-checks the
source.

**What they did here.** **pyright refused the push over one line** — an import placed inside a
branch, used again in a cleanup block. Harmless today; a `NameError` inside a `finally` tomorrow,
which would have hidden whatever actually went wrong. **No test caught it. No human caught it.**

**And gitleaks has passed on every commit for months** — which is only reassuring because it is
capable of failing.

---

**Linux — Linus Torvalds, 1991, GPL-2.0 · Ubuntu — Canonical and the Debian project**

> Announced by a student in Helsinki as *"just a hobby, won't be big and professional."*
> **base-01 runs Ubuntu 24.04 LTS.**

**What it did here.** Shadow runs on it. The website runs on it. The nightly backup ran on it at
00:15 this morning and wrote 92KB that will still be there in fourteen days. **Nobody paid a
licence fee for any of that**, and nobody asked what it was for.

---

**Microsoft — Windows**

**Every line of this project was typed on Windows.** The editor, the terminal, the browser, the
41,000 files of the vault. **The OpenSSH client that reaches base-01 ships with it** — Microsoft
adopted OpenSSH, built by the OpenBSD project, rather than inventing a worse one.

**What it did here.** It got out of the way for twenty-six hours straight. **An operating system
succeeding is mostly invisible**, which is why it goes unthanked.

---

**ASUSTeK Computer — Taipei, 2 April 1989 · ASUS TUF Gaming F15 (FX507ZC4)**

> Founded by **Tzu-Hsien Tung, Ted Hsu, Wayne Hsieh and M.T. Liao** — four hardware engineers who
> left Acer.

**They were nobodies, and they were last in the queue.** Intel supplied IBM first; Taiwanese
firms waited about six months for a new processor. **So they designed a motherboard for Intel's
486 without ever having held a 486.** When Intel hit a problem with their own 486 board, they
asked ASUS for help — and ASUS fixed it. After that, the engineering samples came early.

**What it did here.** **This laptop.** 15.6GB of memory, running three databases, a vector store, a
compiler, a browser and an AI agent at the same time, for a day and a half.

**Three times it ran out of room** (0xC0000142 — Windows unable to start another process) and had
to let something go. **Each time it came back and carried on, and nothing was lost.** That is not a
failure. It is a machine with a fixed amount of space being asked to hold more than fits, and doing
the only honest thing available to it.

**It was bought for the founder by his brother**, who has spent a life buying things, listening,
and standing up for him. The machine is in this file because it earned a place here; the reason it
exists at all is not a piece of hardware history.

---

**Intel — Robert Noyce and Gordon Moore, 1968 · Core i7-12700H**

**Noyce co-invented the integrated circuit** — the reason a computer fits on a desk instead of
filling a room. **He also refused to run Intel with private offices or reserved parking**, which is
why open-plan engineering companies exist at all.

**What it did here.** Fourteen cores. Every test, every build, every model that ran locally.

---

**American Megatrends — Subramonian Shankar and Pat Sarma, Georgia, 1985**

> **The BIOS in this laptop reads `FX507ZC4.312` — AMI firmware.**

**Two men started a firmware company in Georgia in 1985 and it became the firmware that boots most
of the world's computers.** Shankar was its president. **AMIBIOS ran before anything else, on
almost every PC, for decades — and almost nobody who used one could name it.**

**What it did here.** It ran first. Before Windows, before the terminal, before any of this.
**Every single time the machine was switched on.**

---

**And the people whose names are not recoverable**

**Someone assembled this laptop.** Someone fabricated the processor, wound the coils, tested the
memory, packed the box. **Someone laid the fibre that carries every request to base-01 in Chennai,
and someone is on call for it tonight.**

**None of them can be looked up, credited, or thanked by name.** They are the largest group in this
entire file, and the only one with no entry of its own — so this is theirs.

### Acknowledgement — the tests, and the people who make being wrong survivable (s65)

*Every fact below was read from the packages installed on this machine — each project's own
`METADATA`, written by its own authors — rather than from anyone's summary of them. Versions are
what actually ran here today.*

**pytest already has an entry above.** This is about the seven other pieces that loaded alongside it
when the suite ran, and which had no names attached to them anywhere in this file.

---

**pluggy 1.6.0 — Holger Krekel · MIT**

**The plugin system underneath pytest.** Everything else in this list exists because pluggy lets a
test runner be extended without being modified. **The same person who wrote pytest wrote the thing
that lets other people change it** — which is a particular kind of generosity, and easy to miss.

---

**pytest-asyncio 1.4.0 — Tin Tvrtković, maintained by Michael Seifert · Apache-2.0**

**What it did here.** `asyncio_mode = "auto"`. **It is the reason a test of an async database call
looks exactly like any other test** — no ceremony, no decorators to remember.

**The three tests written yesterday that prove Shadow can reach its own memory are all async.**
Without this, each of them would have needed scaffolding that a non-developer could not read.

**Note the handover:** created by one person, maintained by another. **That is what a healthy small
project looks like** — and the metadata records both names rather than replacing one with the other.

---

**pytest-timeout 2.4.0 — Floris Bruynooghe · MIT**

> `timeout = 120`, `timeout_method = "thread"` — configured in this project's `pyproject.toml`.

**What it did here.** **A hanging test cannot hold the whole suite hostage.** After two minutes it
is cut off — and it dumps the stack of the stuck test, so the culprit is always named rather than
guessed at.

**On a laptop that has run out of memory three times, a suite that hangs forever instead of failing
would have cost hours.**

---

**hypothesis 6.156.6 — David R. MacIver and Zac Hatfield-Dodds · MPL-2.0**

**A different idea of what a test is.** Instead of checking the examples you thought of, you state
what should be true for *every* input in a range, and it goes looking for the one that breaks it —
including cases you would never have thought to try.

**What it did here — and only here.** One file: `tests/test_tamil_lang/test_kural.py`. **The
Tirukkural work.** That is the whole of its use in this repository, and saying so is more useful
than implying it guards everything.

---

**coverage 7.14.1 — "Ned Batchelder and 261 others" · Apache-2.0**
**pytest-cov 7.1.0 — Marc Schlaich, maintained by Ionel Cristian Mărieș · MIT**

**Stated honestly: neither of these ran today.** `pytest-cov` is installed but is not in this
project's default `addopts`, so no coverage was measured in any of the runs described in this file.
**They are here because they are present and available, not because they did work they did not do.**

**Their author line, kept as they wrote it:** *"Ned Batchelder and 261 others."*

---

**anyio 4.13.0 — Alex Grönholm · MIT**

**One async interface over two different worlds** (asyncio and trio). **What it did here:** it sits
underneath the HTTP client and the test client used by every server test in this repository —
including the ones that had to be rewritten yesterday when we learned that each request runs on its
own event loop and database pools do not survive that.

---

**What this whole list is for**

**Not one of these tools makes code correct.** Every one of them exists to make being wrong
*survivable* — to turn a mistake into a sentence you can read, at the moment you make it, instead of
a phone call from a friend three weeks later.

**This morning that was thirteen failures, and every one of them said what was wrong.** Twelve were
two stopped services. One was a real defect, in a line that had been quietly wrong since the
database roles were split.

**None of that was found by being careful. It was found by checking.**

### Acknowledgement — Redis and Qdrant, the two memories (s65)

*Read about before being started, because they were about to be switched on for the fourth day in a
row without anyone knowing where they came from.*

---

**Redis — Salvatore Sanfilippo ("antirez")**

> **Redis 8.8.1** runs here — an external server process, unmodified, reached over a socket.
> Tri-licensed from version 8.0: **AGPLv3 · RSALv2 · SSPLv1.**

**He was not building a database.** He was running a small Italian startup, LLOOGG, a real-time web
log analyser, and the database underneath it could not keep up.

**So he wrote his own.** He prototyped it in Tcl, rewrote it in C, and **the first data type he
implemented was the list.** He posted it on Hacker News. The Ruby community picked it up first;
GitHub and Instagram were among the earliest users.

**One person, one problem he actually had.** That is the whole origin, and it is now inside a large
share of the working web.

**What it holds here.** Two memories on `127.0.0.1:6380`. **Database 9 is Shadow's working memory**
— what is true right now, inside a conversation. **Database 10 is reflex memory** — the shapes of
tasks it has done before. When it isn't running, Shadow's short-term memory has nowhere to live,
and ten tests say so plainly.

---

**Qdrant — André Zayarni and Andrey Vasnetsov**

> **Apache-2.0** — a licence with an explicit patent grant. **Version 1.18.3** runs here.

**They needed vector search for something else entirely.** Existing tools — FAISS among them —
didn't have what they needed.

**So Vasnetsov wrote a production vector search engine from scratch, in Rust.** The first public
release drew enough interest that they founded a company around it, in Berlin, in 2021. **The
engine came first; the company followed it.**

**What it holds here.** `127.0.0.1:6335`, collections prefixed `madras_`. **It holds the session-log
RAG** — the searchable memory of every session, which is how a cold start can find out what
happened three weeks ago — **and the vector side of Shadow's memory.**

**What we learnt from it.** That meaning can be a position in space: "the bug in plan.py" finds a
note that said "Fix the bug", because the numbers put them near each other.

---

**Both of these are memory.** One holds what is true now; one holds what can be found again.
**Between them, they are the reason Shadow knew, on the second turn and without being told twice,
that a cousin's dog is called Idli and is afraid of the ceiling fan.**

That was the moment this project stopped being a plan.

### Acknowledgement — three multilingual embedders, read while choosing one (s66)

*Read from each model's own card. None was installed at the time of writing — this records what we
learned while deciding, and what each team made freely available.*

---

**nomic-embed-text-v2-moe — Nomic AI · Apache-2.0**

> ~100 languages · 475M parameters, 305M active · Matryoshka dimensions, 768 down to 256 · trained
> on 1.6 billion multilingual pairs.

**They did it again.** Their card states it plainly: **weights, code *and* training data released.**
The same choice they made with the first model, made a second time.

**And it carries the nesting-doll idea** — Matryoshka embeddings, where the vector can be cut short
and still works. **The thing sitting in this project's own `Ideas.md` as a future optimisation is
built into this model from the start.**

**A mixture-of-experts design** means only 305M of its 475M parameters are working at any moment.
**Cheaper to run, without being smaller in what it knows.**

---

**bge-m3 — Beijing Academy of Artificial Intelligence · MIT**

> **Jianlv Chen · Shitao Xiao · Peitian Zhang · Kun Luo · Defu Lian · Zheng Liu**
> 100+ languages · 8192-token context · 1024 dimensions · 567M parameters.

**The three M's are the point of the name** — Multi-Functionality, Multi-Linguality,
Multi-Granularity. **One model that handles many languages, several kinds of search, and text from
a sentence to a small book.**

**8192 tokens of context** is the largest of the three — a whole conversation can go in at once,
rather than being cut into pieces.

**MIT, and named authors.** Six people, listed.

---

**EmbeddingGemma — Google DeepMind · the Gemma licence**

> 300M parameters · 768 dimensions with Matryoshka truncation · trained on 100+ languages.

**Built deliberately small, to run on the device in front of you** — their card names phones,
laptops and desktops. **The stated goal is that good embeddings should not require someone else's
data centre.**

**For a project whose whole point is that a friend's conversation never leaves the machine, that is
the same instinct**, arrived at by a much larger organisation.

**On the licence, plainly:** this is the **Gemma licence**, not Apache or MIT. It permits commercial
use but carries its own terms and a use policy. **That is a fact to read before shipping, and the
reason it is stated here rather than assumed.**

---

**One thing all three share.** Every one of them uses Matryoshka representation learning — **the
same idea, arrived at independently by three teams**: that the important meaning can be packed
toward the front, so a vector can be shortened without breaking.

**We only need one of them. All three made their work usable by someone who has never met them.**

### Acknowledgement — the ones that turn words into meaning (s65)

*Written because a test stepped aside and said it needed something we had never named. Every fact
below comes from the projects' own repository, model card, or installed package.*

---

**llama.cpp — founded by Georgi Gerganov · MIT**

> Credited by Ollama itself, in its own repository, as the backend it is built on.

**Before this, running a language model meant renting someone else's hardware.** llama.cpp made it
possible to run one on an ordinary computer — **no data centre, no account, no per-token bill.**

**It is the reason everything below exists**, and it is the layer nobody sees.

---

**Ollama — MIT · `ollama` Python client 0.6.2, MIT**

> Its repository names llama.cpp as its backend and credits Gerganov by name. **The project's own
> pages do not name its founders, so this entry does not either.**

**What it is.** It makes running a model locally as simple as running any other program — pull it,
run it, talk to it over `127.0.0.1:11434`.

**Why that matters here, concretely.** When Shadow indexes a session so it can be found again
later, the text is turned into numbers **on this laptop.** It does not go to any company. There is
no API key, no account, no request leaving the machine, and no third party holding a copy of what
was written.

**For a project that will hold friends' conversations, that is not a technical detail.** It is the
difference between "we don't share your data" and **"your data never left the room."**

---

**nomic-embed-text — Nomic AI · Apache-2.0**

> **Zach Nussbaum, John X. Morris, Brandon Duderstadt and Andriy Mulyar** · arXiv 2402.01613,
> February 2024.

**They released the training data. In its entirety.**

**Nomic published the model, the code, *and* the corpus**, in the `contrastors` repository — the
model card states the training data is released in its entirety. **Anyone can check what it learned
from, or rebuild it from nothing.**

**What it does.** It turns a sentence into a list of numbers such that things which *mean* the same
thing sit near each other. That is why asking about "the bug in plan.py" can find a note that said
"Fix the bug" — **different words, same meaning, and the numbers know it.**

**One idea in it, from the model card** — *Matryoshka Representation Learning*, named for the
nesting dolls. **The vector can be cut short and still works.** 768 numbers, or 64 if you need it
smaller, with the important information deliberately packed into the front.

---

**What they gave us today was an education, and that counts.**

Ollama is not installed on this machine, so `tests/test_memory/test_session_log.py` stepped aside
rather than failing — **and that skip is what sent us reading.** In one sitting it taught the
founder what an embedding is, why "the bug in plan.py" can find a note that said "Fix the bug",
what it means for a model to run locally rather than in someone else's building, and that a group
of researchers chose to publish the corpus their model learned from when they did not have to.

**None of that required running a single line of their code.** Work that teaches someone something
true, just by existing and being documented well enough to read, has done its job.

**These three are credited for what they make possible, for the choices behind them, and for that
afternoon.**

### Acknowledgement — TypeScript, and a machine given away for nothing (s65)

*Both traced backwards from this machine rather than forwards from a company name, and both read
from their own package file or the project's own documents.*

---

**TypeScript — Microsoft Corp. · Apache-2.0 · version 5.9.3 installed here**

> Read from `web/node_modules/typescript/package.json`: `"license": "Apache-2.0"`,
> `"author": "Microsoft Corp."`

**188 files.** Every page of the website, every component, the signup form, the age gate, the route
that talks to Shadow — **all of it is written in TypeScript.**

**What it actually gave.** JavaScript will let you write `user.nmae` and say nothing until a
stranger loads the page. TypeScript says so while you type. **For someone who is not a developer,
that is the difference between a mistake found in a second and a mistake found by a friend.**

**The licence is Apache-2.0** — the one carrying an explicit patent grant, read from the package's
own metadata.

---

**Oracle Cloud Infrastructure — the Always Free tier**

> `WORKSPACE_CONTEXT.md` names Oracle as Stage-1 launch hosting. An OCI key has been on this laptop
> since June 2026, and the shell history holds real logins to the instance.

**A machine, at no cost, under a tier they call Always Free.**

**What it did here.** Before base-01 existed, before there was any question of a load balancer or a
certificate, this project needed somewhere to run that cost nothing. Oracle's Always Free tier was
that place, and this project's own documents record it as the Stage-1 launch host.

---

**WebKit and Swift — published openly by Apple**

> From Apple's own open-source pages and WebKit's own project page. **WebKit:** *an open source Web
> content engine for browsers and other applications*, under **BSD-style and LGPL** licences.
> **Swift:** a general-purpose language built around safety, performance and modern design.
> **Container:** a tool for running Linux containers on a Mac.

**Learned about on the same day, and recorded for what they are.**

**WebKit is an engine, not a browser** — its own project page says exactly that, and then invites
anyone to build a browser on it. **A company that ships one of the most-used browsers in the world
publishes the engine underneath it, under licences that let anyone else use it too.**

**Swift was opened up as well** — readable, changeable and runnable by anyone, not only on their
machines.

**These are not used in this repository** — the website is TypeScript and the one browser engine
installed here is Chromium. **They are written down because the work is real and openly given, and
because something does not have to be used by us to be worth knowing about.**

---

**One correction kept on purpose.** LLVM — the compiler infrastructure Rust is built on, and
therefore Qdrant — was nearly written up from memory as belonging here. **LLVM's own site says it
began as a research project at the University of Illinois**, and names no sponsors. **The claim was
dropped rather than guessed at.** That is the standard this file is trying to hold: if it cannot be
shown, it does not go in.

### Acknowledgement — the three that have been here since the first day (s65)

*Nothing below was looked up. All of it was read from this machine: the packages' own metadata, the
source tree, and this repository's own git history.*

**They arrived in commit `68f6a766`, "chore: initial repo scaffold + uv project", on 12 June 2026 —
the day this repository was created.** Fifty-six days later, on the day this was written, every one
of those days has had all three in it. **Nothing else in this file can say that.**

---

**Pydantic 2.13.4 — MIT**

> Twelve people are named in its own metadata: **Samuel Colvin · Eric Jolibois · Hasan Ramezani ·
> Adrian Garcia Badaracco · Terrence Dorsey · David Montague · Serge Matveenko · Marcelo
> Trylesinski · Sydney Runkle · David Hewitt · Alex Hall · Victorien Plot.**

**How far into this project it goes, counted rather than estimated:**

| | |
|---|---|
| files importing it | **93** |
| `BaseModel` classes | **171** |
| files using `Field(...)` | **85** |

**It is written into the project's own law.** `CLAUDE.md` states it as a rule, not a preference:
*"All YAML config validated."* Every agent role, every neighbourhood, every tool bundle goes through
it before anything runs.

**What that means in practice, for someone who is not a developer.** A wrong setting fails **at
startup, with a sentence naming the field** — not at two in the morning, three layers deep, as a
stack trace nobody can read.

**And it fixed a real bug today.** The app generator had been provisioning databases through the
wrong connection ever since the database roles were split. The fix was found by reading a
**Pydantic `Field` description** in `config.py` — a sentence the authors' design encouraged someone
to write next to the value, which then explained the value to a stranger months later:

> *"The OWNER connection — migrations and provisioning only… Everything else uses `postgres_url`,
> which connects as the DDL-less `madras_app` role so RLS policies actually apply to it."*

**Pydantic's `Field` puts the description next to the value it describes**, which is why that
sentence was there to be found.

---

**Starlette 1.3.1 — Tom Christie · BSD-3-Clause**

> Its own summary, from its own metadata: **"The little ASGI library that shines."**

**It is underneath everything and visible in nothing.** Every request the website makes to Shadow
arrives through it.

**99 test files in this repository use its `TestClient`.** The 228 tests that passed in the batch
run immediately before this entry was written — the entire server, client and end-to-end suite —
**all ran through Starlette.**

**Same author as `httpx`**, which 19 files in `src/` depend on. **One person's work sits on both
sides of every network call this project makes**, incoming and outgoing.

---

**FastAPI 0.136.3 — Sebastián Ramírez · MIT**

**The layer that joins the other two**, and it would be strange to credit both ends and skip the
middle. **10 files import it**, and between them they are the whole API surface: chat, tasks,
skills, workspace, billing.

**What it did here.** It takes a Pydantic model and turns it into a validated HTTP endpoint with no
glue code in between. **The request body a friend's browser sends is checked by the same class that
defines it** — one definition, not two that can drift apart.

---

**One fact noticed while reading the metadata.** **Marcelo Trylesinski appears in Pydantic's author
list**, and is the maintainer stewarding `httpx2`, the continuation of Tom Christie's `httpx`.

### Acknowledgement — E2B, the room where the code runs (s65)

> `e2b` 2.29.6 and `e2b-code-interpreter` 2.8.1, both **MIT**, read from their own metadata on this
> machine. **The infrastructure itself — `github.com/e2b-dev/infra` — is Apache-2.0**, with a
> self-hosting guide and Terraform to deploy it. Firecracker and microVMs are named in their own
> repository.

**The problem it solves.** When an agent writes code and runs it, that code has to run *somewhere*.
If it runs on the machine holding everything else, then a mistake — or someone deliberately talking
the agent into something — reaches your files, your database, your keys.

**E2B's answer is a whole small computer, created on demand, that has nothing of yours in it.** Not
a folder with rules; a separate machine.

---

**What they publish.**

**The infrastructure behind the hosted service is Apache-2.0**, at `github.com/e2b-dev/infra`, with
a self-hosting guide and Terraform to deploy it. **It can be run on your own hardware.**

**This project's own code already records that** — a comment in `tools/sandbox.py` names their
self-hostable infra as the path if Madras ever needs Firecracker-level isolation of its own.

---

**What it does here, specifically.**

`tools/sandbox.py` has three backends — `local`, `docker`, `e2b` — and E2B is the strongest.
**It is the `ASI05` mitigation in this project's own security list**: the answer to "what happens
when the agent runs code?"

**And one of its tests deserves naming.** `test_e2b_write_rejects_traversal` checks that a path like
`../../etc/passwd` is refused. **That single test is the difference between a sandbox and a
suggestion.**

**Honest note, s65:** at the time of writing, seven E2B tests skip on this laptop — not because
anything is missing, but because `sandbox_backend` is `local` and no E2B key is set. **Per D47 the
self-hosted Docker sandbox is the production choice, so those skips are correct rather than a gap.**

### Acknowledgement — Docker, from the second day (s65)

> **Docker Engine 29.6.2** on this machine. The engine's open-source upstream is **Moby**,
> **Apache-2.0** — created by Docker, and, in the project's own words, *"Docker is committed to
> using Moby as the upstream for the Docker Product."*

**Docker has been part of this project since its second day.**

The repository was created on 12 June 2026. Commit `0fc120d2` — *"Sandbox ABC + local + docker
backends"* — is dated **13 June**. Before there was a website, a server, a certificate, or a name
for any of it, there was a decision that code an agent writes should run somewhere it cannot cause
harm. **Docker was how that became possible on day two rather than someday.**

**It is the production sandbox here, by decision D47** — chosen over the paid alternative, and
recorded as *"Hardened at s63 and verified on base-01."* Not a starting point that was outgrown.
**The choice.**

---

**They built the ability to take things away, and that is the gift.**

Every one of these is something a person designed, implemented and maintained so that someone
else's mistake — or someone else's user — could not reach further than it should:

| what they built | what it makes impossible |
|---|---|
| `--read-only` | writing to the container's own filesystem |
| `--tmpfs` with `noexec,nosuid` | scratch space being used to launch anything |
| `--cap-drop ALL` | every Linux capability, surrendered |
| `--security-opt no-new-privileges` | anything inside ever gaining more rights |
| `--pids-limit` | a fork bomb reaching past its own container |
| non-root uid | being root even in its own box |

**When Madras asks a container what it is, it answers in Docker's words:** `CapEff
0000000000000000`, `uid 1000`, `NoNewPrivs 1`. **Those guarantees hold because someone made them
hold.**

---

**What they gave away.**

**Moby — the open-source upstream that Docker's own product is built from — is Apache-2.0**, and in
the project's own words, *"Docker is committed to using Moby as the upstream for the Docker
Product."* Outside maintainers are welcomed into it.

**Docker Desktop is free for personal use and small business.** This project has run on it since its
second day and has never paid for it. **A solo founder in Chennai was able to build a governed,
isolated agent runtime because that was true.**

---

**What it shaped, beyond what it does.**

The sandbox interface at the centre of this project — `tools/sandbox.py`, with its `local`, `docker`
and `e2b` backends — **is built around the idea Docker taught: that safety is a list of things you
hand back.** That interface now carries a backend Docker did not write, and it still thinks in
Docker's terms.

**Containers made mistakes survivable and environments repeatable.** That is why this project
exists in the shape it does, and it is worth saying plainly, to the people who made it: **thank
you.**

### Acknowledgement — ssh, openssl, and the pattern matcher (s66)

*Read from the copyright files installed on base-01 and from the `re` module's own source header
on this laptop. Nothing was looked up.*

---

**OpenSSH — `OpenSSH_9.6p1`**

> Copyright, in the order its own file lists them: **1995 Tatu Ylonen**, Espoo, Finland · Markus
> Friedl · Theo de Raadt · Niels Provos · Dug Song · Aaron Campbell · Damien Miller · Kevin
> Steves · Daniel Kouril · Wesley Griffin · Per Allansson · Nils Nordman.
> BSD-3-clause or less restrictive.

**Every single thing done to base-01 today went through this.** Reading the logs, copying four
files, restarting the engine, running the memory test. **We never once typed a password**, because
a key we hold answered a challenge without ever crossing the network.

**The licence carries an unusual condition, and it is a kind one:** a derived version must be
clearly marked as such, and **if it breaks the protocol it may not call itself "ssh" or "Secure
Shell".** They gave the code away and kept only the honesty of the name — so that when you type
`ssh`, you know what you are getting.

---

**OpenSSL — `3.0.13, 30 Jan 2024`**

> Copyright: 1995–2020, The OpenSSL Project Authors · **1995–1998, Eric A. Young and Tim J.
> Hudson** · and named contributors including Akamai, Andy Polyakov, CloudFlare, Cryptography
> Research, **Daniel J. Bernstein**, the EdelKey Project.

**It made this project's certificate request**, and generated the private key still sitting on
base-01 in a file only root can read.

**And it caught the mistake.** When the certificate came back, OpenSSL is what compared the
fingerprints and showed that **`da24a7db…` did not match `06fe0e45…`** — proving the certificate
had been issued against a different key. **Without that check it would have been installed, and
the day after would have gone to wondering why.**

**Two names, from 1995.** Then thirty years of people adding to it, each recorded by name in a file
almost nobody opens.

---

**Python's `re` — "Secret Labs' Regular Expression Engine"**

> From the module's own header: *Copyright (c) 1998–2001 by Secret Labs AB.* Redistributable under
> CNRI's Python 1.6 licence. *"Portions of this engine have been developed in cooperation with
> CNRI. Hewlett-Packard provided funding for 1.6 integration."*

**The line that stops `<retrieved>` reaching a friend is one of these.** So is every search run
today — finding where a setting was read, which files talk to Qdrant, whether a key was hardcoded
anywhere.

**A pattern is a very small language for describing a shape.** Having one means you can ask a
question of a hundred thousand lines and get an answer in a second.

---

**Both OpenSSH and OpenSSL were begun in 1995**, by people who put their names in a file and let
anyone use the result. **Thirty years later a project in Chennai reaches its own server and checks
its own certificate with them, having asked nobody's permission.**

**One thing deliberately left out.** Regular expressions have a longer history than that header —
mathematics from the 1950s, and an implementation that first put them into everyday tools. **None
of that is on these machines, so it is not written here.** It belongs in this file only once a
primary source has actually been read.

### Acknowledgement — the hundred and thirteen (s66)

*Every name below was read from the packages' own `METADATA` on this laptop. The table further
down already lists all 121 with version and licence — that is attribution, and it satisfies what
the licences ask. **This is the other thing.***

**One hundred and thirteen of them had never been named here.** Reading their metadata together
shows something the table cannot: **how few people this actually is.**

---

**The repeat names — one person, several of the things Shadow stands on**

**Julian Berman** — `jsonschema` · `jsonschema-specifications` · `referencing` · `rpds-py`.
**Four.** Every tool schema Shadow validates passes through one person's work.

**Cory Benfield** — `h2` · `hpack` · `hyperframe`. **That is the whole of HTTP/2**, implemented by
one person.

**Adrien Barbaresi** — `trafilatura` · `courlan` · `htmldate`. **The entire way Shadow reads a web
page**: pulling the text out, cleaning the URL, working out when it was written.

**Daniele Varrazzo** — `psycopg` · `psycopg-binary` · `psycopg-pool`. **LangGraph's checkpointer
reaches Postgres through his work**, which is why a conversation survives a restart.

**Andrew Svetlov and the aiohttp team** — `aiohttp` · `aiosignal` · `frozenlist` · `multidict` ·
`propcache` · `yarl`. **Six.**

**Tom Christie** — `uvicorn` and `httpcore`, on top of Starlette and httpx credited above. **The
process running on base-01 at this moment is his.**

**Kenneth Reitz** — `requests` and `certifi`. **`certifi` carries Mozilla's certificate bundle:
every time anything here trusts anything, that file is what was consulted.**

---

**And the ones doing a single job, completely**

`asyncpg` — **MagicStack** · every database call Shadow makes
`PyYAML` — **Kirill Simonov** · every agent config, neighbourhood and tool bundle is read by it
`numpy` — **Travis E. Oliphant et al.** · the vector maths under recall
`cryptography` — **the Python Cryptographic Authority**
`PyJWT` — **Jose Padilla** · every sign-in
`tenacity` — **Julien Danjou** · retries, so a flaky network is not a failure
`rich` — **Will McGugan** · and `Pygments` — **Georg Brandl** · every readable line of output
`attrs` — **Hynek Schlawack** · *"classes without boilerplate"*, in their own words
`packaging` — **Donald Stufft** · how Python knows which version is newer
`tree-sitter` — **Max Brunsfeld** · how this project's own code graph is built
`typer` — **Sebastián Ramírez** · `regex` — **Matthew Barnett** · `websockets` — **Aymeric
Augustin** · `urllib3` — **Andrey Petrov** · `markdown-it-py` — **Chris Sewell**
`idna` — **Kim Davies** · internationalised domain names, **which is why a domain can be written in
a script other than Latin at all**
`pytz` — **Stuart Bishop** · and `tzdata`, the IANA time zone data · and `tzlocal` — **Lennart
Regebro**. **Three separate people so that a timestamp means the same thing in Chennai and
elsewhere.**
`six` — **Benjamin Peterson** · a bridge between two versions of Python, still carried a decade
after the crossing
`typing_extensions` — **Guido van Rossum, Jukka Lehtosalo and others.** Python's creator, in a
package nobody thinks about.

---

**`mcp` — Anthropic, PBC · MIT.** The Model Context Protocol SDK: how every tool Shadow has is
defined and served. **Named here on the same terms as the other 112, because that is what it is.**

---

**Some have no person to thank.** `langgraph` and its five packages list no author; nor does
`orjson`. **The maintainers chose not to put a name there, which is their right** — so the thanks
can only be addressed to the project.

---

**What reading a hundred and thirteen metadata files actually taught us.** Not that the list is
long. **That it is short.** A few dozen people, most maintaining their piece for a decade or more,
their names sitting in files almost nobody opens.

**They are in the table because a licence requires it. They are here because they earned it.**

### Acknowledgement — the protocols nobody owns (s66)

*Read from the RFC Editor's own copies. These documents are published free and permanently by the
people who wrote them, so that anyone could build on the internet without asking permission. That
is why this project is able to exist at all.*

---

**RFC 791 — Internet Protocol · September 1981 · Jon Postel, editor**

> Prepared by the **Information Sciences Institute, University of Southern California**, for the
> **Defense Advanced Research Projects Agency**.

**Read what it refuses to do**, in its own words:

> *"The internet protocol is specifically limited in scope to provide the functions necessary to
> deliver a package of bits from a source to a destination… There are no mechanisms to augment
> end-to-end data reliability, flow control, sequencing, or other services."*

**The protocol carrying the entire internet is defined by what it declines to promise.** It moves
a packet and nothing else. **Everything above it exists because it stayed small.**

---

**RFC 9293 — Transmission Control Protocol · August 2022 · W. Eddy, editor**

**TCP is what makes an unreliable thing reliable** — it notices what went missing and asks again.
Every file copied to base-01 arrived whole because of it.

**One honest observation.** This document replaced RFC 793 from 1981, and **it does not name the
people who wrote the original.** It records that "RFC 793 was released" and moves on. **Not
wrongdoing — a technical consolidation.** But it is exactly why a file like this one exists.

---

**RFC 1035 — Domain Names · November 1987 · P. Mockapetris, ISI**

**`shadow.outkastcode.com` becomes an address because of this document**, and so did every lookup
made while the load balancer was being diagnosed.

**Note where he was: ISI, the same institute as Postel.** Two people, in one building, six years
apart, wrote how the internet addresses and names everything.

---

**RFC 9110 — HTTP Semantics · June 2022 · R. Fielding, M. Nottingham, J. Reschke**

**STD 97**, obsoleting nine earlier specifications. **Every request to Shadow is one of these.**

In its own description: *"a stateless application-level protocol for distributed, collaborative,
hypertext information systems."*

---

**And the part with no document.**

**Someone laid the fibre that carries every request from Chennai to base-01, and someone maintains
it tonight.** An internet service provider routes it, and neither the founder nor anyone reading
this can name one of them.

**They have no RFC and no metadata file.** They are the largest absence in this file, and this
line is all that can honestly be offered.

### Acknowledgement — three companies we pay, and what they actually did (s66)

*A third kind of entry, kept separate on purpose. Everything else in this file is owed to people
who gave their work away. **These three we pay.** Listing them identically would quietly
overstate what we owe the ones who chose to give something for free. What follows is not
gratitude for an invoice — it is the specific things that happened, which are worth recording
whether or not money changed hands.*

---

**E2E Networks — the machine Shadow runs on**

Shadow runs on a server in their Delhi data centre. **What is worth recording is not the hosting;
it is that a person called.**

Twice their support gave an instruction that contradicted our own analysis, and **twice they were
right and we were wrong.** They said to delete the load balancer and recreate it, when we were
still reading configuration. They said to open port 1167, after we had concluded it was never
blocked — **backups had been failing silently for five days, and resumed once it was open.**

**That is the entry.** A support desk that is right about your own system when you are confident
and mistaken is worth more than one that agrees with you.

---

**Sectigo — the certificate authority**

They issue the certificate that makes `shadow.outkastcode.com` a padlock instead of a warning.

**The part worth admiring is the validation design.** To prove you control a domain, you publish a
DNS record only its owner could publish. **No email to trust, no phone call to social-engineer, no
human deciding whether you sound legitimate** — control of the domain is demonstrated by
exercising control of the domain. The proof is the thing itself.

Publicly-trusted certificates are also logged to public **Certificate Transparency** logs, which
means a certificate issued for your domain cannot be issued quietly. **Anyone can check what was
issued in their name, including you.** An industry requirement rather than their invention, but
they participate in it, and the world is better for it existing.

---

**Wix — the DNS**

They hold the zone for `outkastcode.com`, and every record in it: the mail routing, the DKIM keys,
the site, and the validation records for two separate certificate orders.

**Two things earned this entry.** The zone never once stopped answering — across load balancer
rebuilds, a certificate reissue, and repeated record edits, **DNS was never the thing that broke**,
and it would have been easy for it to be. And the panel is usable by someone who is not a network
engineer, which is not a small thing when the person editing production DNS at midnight is a
founder who taught themselves this last year.

---

**No individual is named here, deliberately.** The engineer who called was doing their job well,
and they did not choose to appear in a public file. The company employs them; the company gets
the credit.

### Acknowledgement — four sites we read, and what each one changed here (s66)

*A different kind of debt from everything above. We use no code of theirs. We read their public
pages while assessing our own, took something specific from each, and changed real copy the same
evening — so the debt is recorded here rather than filed as admiration.*

---

**Obsidian — `obsidian.md`**

> *"Obsidian stores notes privately on your device… **No one else can read them, not even us.**"*

**A mechanism, stated as a promise.** Not "we value your privacy" — **the reason it is true, in one
sentence a person can check.**

**What it changed.** Shadow's model moved onto base-01 the same day, which made the same kind of
statement true for the first time. **We had the fact and no sentence for it.** Their line is why
the home page now reads *"0 — conversations sent to a model provider"* and why the closing band
says *"running on our own machine, not anyone else's."*

---

**Plausible — `plausible.io`**

> *"No cookies, no persistent identifiers, no cross-site or cross-device tracking."*
> *"completely independent, self-funded, bootstrapped… no outside investors."*

**Two lessons.** **The absence is the feature** — they lead with what they refuse to collect.
**And the business model is itself a trust signal**: who pays for a thing tells you what it will
become.

---

**Tailscale — `tailscale.com`**

**Concrete outcomes first; technical depth deferred to a second page.**

**What it changed.** Our home page led with the frame — a nine-layer Agent OS, governance
inheritance, capability counts — **before the thing that works.** Their structure is the argument
for moving all of that behind the first screen.

---

**Anthropic — `anthropic.com`**

**Credited for what we learned by noticing what is NOT there.**

**Their home page states no product limitations at all** — and neither do the other three. **So
saying "here is what Shadow cannot do yet" is not a convention we were failing to follow.** It is
an unusual choice, made deliberately.

**That is worth knowing before making it**, and we only knew it because we looked.

---

**Recorded because it is checkable.** The commits of 2026-08-11 removed seven claims and rewrote
the calls to action, and each change traces to one of these four pages. **Reading someone's work
and changing your own because of it is a debt, even when no code moves.**

---

No AGPL-3.0 / GPL (strong) / SSPL / BSL / Commons-Clause licenses are present in this tree.

| Package | Version | License |
|---|---|---|
| aiohappyeyeballs | 2.6.2 | Python Software Foundation License |
| aiohttp | 3.14.1 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache Software License |
| annotated-doc | 0.0.4 | MIT |
| annotated-types | 0.7.0 | MIT License |
| anyio | 4.13.0 | MIT |
| asyncpg | 0.31.0 | Apache-2.0 |
| attrs | 26.1.0 | MIT |
| babel | 2.18.0 | BSD License |
| bracex | 2.6 | MIT |
| certifi | 2026.5.20 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.0.0 | MIT |
| charset-normalizer | 3.4.7 | MIT |
| click | 8.4.1 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| courlan | 1.4.0 | Apache-2.0 |
| cryptography | 48.0.1 | Apache-2.0 OR BSD-3-Clause |
| datasets | 5.0.0 | Apache Software License |
| dateparser | 1.4.1 | BSD-3-Clause |
| dill | 0.4.1 | BSD License |
| dockerfile-parse | 2.0.1 | BSD License |
| e2b | 2.29.6 | MIT License |
| e2b-code-interpreter | 2.8.1 | MIT License |
| fastapi | 0.136.3 | MIT |
| filelock | 3.20.0 | Unlicense |
| frozenlist | 1.8.0 | Apache-2.0 |
| fsspec | 2026.4.0 | BSD-3-Clause |
| googleapis-common-protos | 1.75.0 | Apache Software License |
| greenlet | 3.5.1 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT License |
| h2 | 4.3.0 | MIT License |
| hf-xet | 1.5.1 | Apache-2.0 |
| hpack | 4.2.0 | MIT |
| htmldate | 1.10.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| httpx-sse | 0.4.0 | MIT |
| huggingface_hub | 1.19.0 | Apache Software License |
| hyperframe | 6.1.0 | MIT License |
| idna | 3.18 | BSD-3-Clause |
| jsonpatch | 1.33 | BSD License |
| jsonpointer | 3.1.1 | BSD License |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| jusText | 3.0.2 | BSD License |
| langchain-core | 1.4.6 | MIT License |
| langchain-protocol | 0.0.16 | MIT License |
| langgraph | 1.2.4 | MIT |
| langgraph-checkpoint | 4.1.1 | MIT |
| langgraph-checkpoint-postgres | 3.1.0 | MIT |
| langgraph-prebuilt | 1.1.0 | MIT |
| langgraph-sdk | 0.4.2 | MIT |
| langsmith | 0.8.15 | MIT |
| lxml | 6.1.1 | BSD-3-Clause |
| lxml_html_clean | 0.4.5 | BSD-3-Clause |
| markdown-it-py | 4.2.0 | MIT License |
| mcp | 1.27.2 | MIT License |
| mdurl | 0.1.2 | MIT License |
| multidict | 6.7.1 | Apache License 2.0 |
| multiprocess | 0.70.19 | BSD License |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| opentelemetry-api | 1.42.1 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-common | 1.42.1 | Apache-2.0 |
| opentelemetry-exporter-otlp-proto-http | 1.42.1 | Apache-2.0 |
| opentelemetry-proto | 1.42.1 | Apache-2.0 |
| opentelemetry-sdk | 1.42.1 | Apache-2.0 |
| opentelemetry-semantic-conventions | 0.63b1 | Apache-2.0 |
| orjson | 3.11.9 | MPL-2.0 AND (Apache-2.0 OR MIT) |
| ormsgpack | 1.12.2 | Apache-2.0 OR MIT |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pandas | 3.0.3 | BSD License |
| playwright | 1.60.0 | Apache-2.0 |
| propcache | 0.5.2 | Apache Software License |
| protobuf | 6.33.6 | 3-Clause BSD License |
| psycopg | 3.3.4 | LGPL-3.0-only |
| psycopg-binary | 3.3.4 | LGPL-3.0-only |
| psycopg-pool | 3.3.1 | LGPL-3.0-only |
| pyarrow | 24.0.0 | Apache-2.0 |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic-settings | 2.14.1 | MIT |
| pydantic_core | 2.46.4 | MIT |
| pyee | 13.0.1 | MIT License |
| Pygments | 2.20.0 | BSD-2-Clause |
| PyJWT | 2.13.0 | MIT |
| python-dateutil | 2.9.0.post0 | Apache Software License; BSD License |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| pytz | 2026.2 | MIT License |
| pywin32 | 312 | Python Software Foundation License |
| PyYAML | 6.0.3 | MIT License |
| redis | 8.0.0 | MIT |
| referencing | 0.37.0 | MIT |
| regex | 2026.5.9 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache Software License |
| requests-toolbelt | 1.0.0 | Apache Software License |
| rich | 15.0.0 | MIT License |
| rpds-py | 2026.5.1 | MIT |
| shellingham | 1.5.4 | ISC License (ISCL) |
| six | 1.17.0 | MIT License |
| sse-starlette | 3.4.4 | BSD-3-Clause |
| starlette | 1.3.1 | BSD-3-Clause |
| tenacity | 9.1.4 | Apache Software License |
| tld | 0.13.2 | MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later |
| tqdm | 4.68.2 | MPL-2.0 AND MIT |
| trafilatura | 2.1.0 | Apache-2.0 |
| tree-sitter | 0.25.2 | MIT License |
| tree-sitter-language-pack | 1.8.1 | MIT |
| typer | 0.25.1 | MIT |
| typing-inspection | 0.4.2 | MIT |
| typing_extensions | 4.15.0 | PSF-2.0 |
| tzdata | 2026.2 | Apache-2.0 |
| tzlocal | 5.4.3 | MIT |
| urllib3 | 2.7.0 | MIT |
| uuid_utils | 0.16.0 | BSD-3-Clause |
| uvicorn | 0.49.0 | BSD-3-Clause |
| wcmatch | 10.1 | MIT |
| websockets | 15.0.1 | BSD License |
| xxhash | 3.7.0 | BSD License |
| yarl | 1.24.2 | Apache-2.0 |
| zstandard | 0.25.0 | BSD-3-Clause |

---

*To everyone named in this file, and to everyone who is not:*

> I have understood the power of ordinary broken people who overcome all odds in their lives for
> others and I don't want a soul that thinks about other souls to feel bad, I may not know all the
> names but I value your existence and I am grateful that you all existed and hope you can find joy
> in doing what you like to do wherever you are
