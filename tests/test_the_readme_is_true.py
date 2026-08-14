"""Every checkable claim in the README, checked.

A README is the only thing most people will read. It is also the file most likely to drift,
because nothing breaks when it goes stale -- the code keeps working and the sentence quietly stops
being true. These tests make the two fail together.

Scope: only claims a machine can settle. "It is early and things will be rough" is honest and
untestable. "Out of the box you get 14 tools" is a number, and a number can be wrong.

None of this needs a database, a model or a network.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

import madras.tools.builtin  # noqa: F401 — registers every built-in tool
from madras.tools.registry import REGISTRY, Rank

REPO = Path(__file__).resolve().parent.parent
README = (REPO / "README.md").read_text(encoding="utf-8")


class TestEveryFileTheReadmeMentionsExists:
    def test_the_files_it_points_you_at(self) -> None:
        # The README names some of these as commands rather than filenames -- it says
        # `docker compose up -d`, not `docker-compose.yml`. Check the file exists either way,
        # and only require a literal mention for the ones it links by name.
        for name in ("LICENSE", "THIRD-PARTY-NOTICES.md", ".env.example",
                     "docker-compose.yml", "pyproject.toml"):
            assert (REPO / name).exists(), (
                f"README's setup depends on `{name}` and it is not here. This is the first kind "
                "of broken a stranger meets."
            )
        for linked in ("LICENSE", "THIRD-PARTY-NOTICES.md", ".env.example"):
            assert linked in README, f"README no longer mentions {linked} — deliberate?"
    def test_the_paths_in_the_install_block(self) -> None:
        assert (REPO / "packages/madras-capabilities").is_dir(), (
            "the README's first install command installs from packages/madras-capabilities"
        )

    def test_the_file_it_calls_the_boundary(self) -> None:
        """`src/madras/tools/builtin/__init__.py` is named as the thing to read first."""
        assert (REPO / "src/madras/tools/builtin/__init__.py").exists()


class TestNothingIsStillAPlaceholder:
    """The first command in the README is a `git clone`. A placeholder there is not a cosmetic
    problem: it is the very first thing a stranger runs, and it fails before they have read a
    word of the rest. This repo shipped `<your-fork-url>` right up until someone read the file
    line by line and noticed."""

    def test_no_angle_bracket_placeholders_in_command_blocks(self) -> None:
        import re as _re

        fence = chr(96) * 3
        blocks = _re.findall(fence + r"(?:bash|sh|yaml)?(.*?)" + fence, README, _re.S)
        offenders = [
            ln.strip()
            for b in blocks
            for ln in b.splitlines()
            if _re.search(r"<[a-z][a-z0-9 _-]*>", ln)
        ]
        assert not offenders, (
            f"placeholders left in commands a person is meant to run: {offenders}"
        )


class TestTheNumbersAreRight:
    def test_the_default_tool_count_matches_the_claim(self) -> None:
        """The README says a number. If the number moves, one of the two must change."""
        m = re.search(r"you get (\d+) tools", README)
        assert m, "the README no longer states a default tool count — put it back or drop the test"
        claimed = int(m.group(1))

        role = yaml.safe_load((REPO / "agents/roles/shadow.yaml").read_text(encoding="utf-8"))
        actual = len(REGISTRY.allowed(agent_rank=Rank.INTERN, toolsets=role.get("toolsets") or []))
        assert actual == claimed, (
            f"README says {claimed} tools out of the box; the config resolves to {actual}."
        )

    def test_every_toolset_the_readme_offers_actually_exists(self) -> None:
        """The 'Available:' line is a promise that each of these is one line from working."""
        line = next((ln for ln in README.splitlines() if ln.startswith("Available:")), "")
        assert line, "the README no longer lists the switchable toolsets"
        offered = set(re.findall(r"`([a-z_]+)`", line))
        missing = offered - REGISTRY.toolsets()
        assert not missing, (
            f"README offers toolsets that do not exist: {sorted(missing)}. Someone would add one "
            "to their config and get nothing, with no error to explain it."
        )


class TestTheCommandsItTellsYouToRun:
    def test_the_cli_entry_point_exists(self) -> None:
        assert "python -m madras.cli chat" in README
        r = subprocess.run(
            [sys.executable, "-m", "madras.cli", "--help"],
            capture_output=True, text=True, cwd=str(REPO),
            # Inherit the environment. Setting PATH="" broke Windows DLL resolution and made
            # `import asyncio` fail, which looked exactly like a broken CLI and was not.
            env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        )
        assert r.returncode == 0, f"`python -m madras.cli --help` failed:\n{r.stderr[-600:]}"
        assert "chat" in r.stdout, "the README's `chat` subcommand is not in the CLI"

    def test_the_console_script_is_declared(self) -> None:
        pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        assert pyproject["project"]["scripts"]["shadow"] == "madras.cli:main"

    def test_docker_compose_defines_what_the_readme_says_it_starts(self) -> None:
        """The README calls this the thing that starts memory's databases."""
        compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
        services = set(compose.get("services") or {})
        assert {"postgres", "qdrant"} <= services, f"compose is missing services: {services}"


class TestTheEnvExampleMatchesTheCode:
    """A variable named in `.env.example` that the code never reads is a person's wasted hour."""

    def test_every_documented_variable_is_one_the_code_looks_for(self) -> None:
        from madras.config import Settings

        documented = {
            ln.split("=", 1)[0].strip()
            for ln in (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
            if "=" in ln and not ln.strip().startswith("#")
        }
        known = set()
        for name, field in Settings.model_fields.items():
            alias = getattr(field, "validation_alias", None)
            known.add(str(alias) if alias else f"MADRAS_{name.upper()}")
            known.add(f"MADRAS_{name.upper()}")

        unread = {v for v in documented if v not in known}
        assert not unread, (
            f".env.example documents variables the code never reads: {sorted(unread)}. Someone "
            "would set them, see no effect, and have no way to find out why."
        )

    def test_it_contains_no_actual_secrets(self) -> None:
        """The file exists to name variables, never to hold values."""
        for ln in (REPO / ".env.example").read_text(encoding="utf-8").splitlines():
            if "=" not in ln or ln.strip().startswith("#"):
                continue
            key, _, value = ln.partition("=")
            if any(w in key.upper() for w in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
                assert not value.strip(), (
                    f"`{key.strip()}` has a value in .env.example. That file is names only."
                )


class TestWhatItPromisesItCannotDo:
    """The README's refusals, restated here so the two files cannot drift apart."""

    def test_it_still_says_it_cannot_send_messages(self) -> None:
        assert "cannot send messages" in README.lower() or "It cannot send messages" in README

    def test_and_that_is_still_true(self) -> None:
        assert "messaging" not in REGISTRY.toolsets()
