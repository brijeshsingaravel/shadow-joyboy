"""Custom commands as markdown — the opencode `.opencode/command/*.md` slash-command format.

A command is a markdown file: YAML frontmatter (name · description · …) + a template body that
expands three ways — `$ARGUMENTS` (the invocation args), `` !`cmd` `` (inject a shell command's
output), and `@path` (inline a file's contents). The shell + file resolvers are **injectable**,
so the expander is pure + deterministic and the governance/confinement (sandbox, command scanner,
path-confinement) lives at the call site. **Default = no resolvers = no side effects** (the
placeholders are left untouched) — safe by construction. Sibling of [[Agent-as-Markdown]].
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

import yaml

_SHELL = re.compile(r"!`([^`]+)`")
_FILE = re.compile(r"@([A-Za-z0-9_./\-]+)")


@dataclass
class CommandDoc:
    name: str = ""
    description: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict[str, Any])
    template: str = ""


def parse_command_md(text: str) -> CommandDoc:
    """Parse a command markdown file into frontmatter + the template body."""
    s = text.lstrip()
    fm: dict[str, Any] = {}
    body = text.strip()
    if s.startswith("---"):
        end = s.find("\n---", 3)
        if end != -1:
            loaded: Any = yaml.safe_load(s[3:end]) or {}
            fm = cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else {}
            body = s[end + 4 :].lstrip("\n").strip()
    return CommandDoc(
        name=str(fm.get("name", "")),
        description=str(fm.get("description", "")),
        frontmatter=fm,
        template=body,
    )


def expand_command(
    template: str,
    *,
    arguments: str = "",
    file_reader: Callable[[str], str] | None = None,
    shell_runner: Callable[[str], str] | None = None,
) -> str:
    """Expand a command template. `$ARGUMENTS` → args; `` !`cmd` `` → shell_runner(cmd);
    `@path` → file_reader(path). A resolver left as None leaves its placeholder untouched
    (no side effect)."""
    out = template.replace("$ARGUMENTS", arguments)

    if shell_runner is not None:
        out = _SHELL.sub(lambda m: shell_runner(m.group(1).strip()), out)
    if file_reader is not None:
        out = _FILE.sub(lambda m: file_reader(m.group(1)), out)
    return out


def expand_command_doc(
    doc: CommandDoc,
    *,
    arguments: str = "",
    file_reader: Callable[[str], str] | None = None,
    shell_runner: Callable[[str], str] | None = None,
) -> str:
    return expand_command(
        doc.template, arguments=arguments, file_reader=file_reader, shell_runner=shell_runner
    )
