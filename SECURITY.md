# Reporting a security problem

**Email `brijeshsingaravel@gmail.com`. Please don't open a public issue first.**

That's the whole process. There's no bounty, no form, and no security team — there is one person,
and he'd rather hear from you privately than read about it somewhere else.

---

## What to expect, honestly

**A reply within about a week.** This is maintained by one person with a job and a family, so it
may take a few days. If a week passes with nothing, assume the email went astray and chase it —
that's not rudeness, it's help.

**No bounty.** There's no money behind this project. If that makes reporting not worth your time,
that's completely fair, and no hard feelings.

**Credit if you want it, silence if you prefer.** Your call, and you'll be asked before your name
appears anywhere.

**If it's serious and I can't fix it quickly, it will be said out loud** — in the README, as a
plain warning, while it's still broken. Nobody should be running something dangerous because the
person who knew kept quiet to avoid embarrassment.

## What is worth reporting

**Anything that gets past the boundary this repo claims to have.** The README says Shadow cannot
send messages, cannot spawn sub-agents, and cannot touch your machine until you switch a toolset
on. If you find a way around any of that, it's a real finding — that's the promise the whole
project rests on.

**Anything that leaks one person's memories to another.** Memory is namespaced by tenant. A way to
read across that line is the most serious thing you could find here.

**Anything that turns retrieved text into an instruction.** The agent reads web pages, files and
its own memories. Content it retrieves is wrapped in `<retrieved>` fences and is supposed to be
treated as data. A way to make it act on something it merely read is a genuine bug, and a
well-known class of one.

**Anything that escapes the permission check.** Actions that cannot be undone are supposed to stop
and ask. A path that runs one without asking is worth an email.

## What is already known, and is not a vulnerability

**The switchable toolsets do what they say.** Turn `shell` on and the agent can run commands. Turn
`file_write` on and it can edit your files. **Eighteen tools can touch your machine once you
enable their toolset.** That's not a flaw — it's the documented behaviour, it's why they ship
switched off, and it's why turning one on should be a decision you make deliberately.

**Sandboxing exists but is not the default.** `src/madras/tools/sandbox.py` can run code in a
container. On a plain local install, code runs where you run it. Treat "I gave it shell access and
it ran a shell command" as working as intended.

**Secrets in your own `.env` are yours to protect.** This project reads configuration from that
file and nowhere else. It has no telemetry and sends nothing anywhere you did not configure.

## If you're not sure

**Send it anyway.** A wrong report costs one reply. An unsent one can cost somebody their inbox,
their files, or their private conversations.
