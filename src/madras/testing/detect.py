"""Detect the right test runner from a workspace file listing.

Returns (runner_name, command). The agent no longer has to know the toolchain —
run_tests can auto-pick pytest / jest / go test / cargo test from marker files.
"""

from __future__ import annotations

# Order matters: the first marker that matches wins. (runner, marker-substrings, cmd)
_RUNNERS: list[tuple[str, tuple[str, ...], str]] = [
    (
        "pytest",
        ("pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "conftest.py"),
        "uv run pytest -q",
    ),
    ("jest", ("package.json",), "npx jest --silent"),
    ("go", ("go.mod",), "go test ./..."),
    ("cargo", ("Cargo.toml",), "cargo test -q"),
]

_DEFAULT = ("pytest", "uv run pytest -q")


def detect_runner(files: list[str]) -> tuple[str, str]:
    """Pick (runner, command) from a flat list of workspace file names/paths."""
    lowered = [f.lower().replace("\\", "/") for f in files]
    has_py = any(f.endswith(".py") for f in lowered)
    for runner, markers, cmd in _RUNNERS:
        for marker in markers:
            if any(marker in f for f in lowered):
                # package.json alone with .py files present → still prefer pytest
                if (
                    runner == "jest"
                    and has_py
                    and not any("package.json" in f for f in lowered if "/node" not in f)
                ):
                    continue
                return runner, cmd
    return _DEFAULT
