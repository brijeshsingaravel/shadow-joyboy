# Shadow — skills (frozen-contract)

The 5 active skills resolve from the canonical **`skills/<name>/SKILL.md`** registry (single source — the
agent directory references them rather than duplicating). Declared in `../agent.yaml` under `skills:`.

| Skill | Canonical SKILL.md |
|---|---|
| `competitor-radar` | `skills/competitor-radar/SKILL.md` |
| `draft-launch-email` | `skills/draft-launch-email/SKILL.md` |
| `research-and-summarize` | `skills/research-and-summarize/SKILL.md` |
| `session-retro` | `skills/session-retro/SKILL.md` |
| `tech-radar-scan` | `skills/tech-radar-scan/SKILL.md` |

The **self-evolving skill library** (auto-propose / refine from `session-retro`) is **W5**. At **W10** the
loader resolves these into the directory at conformance.
