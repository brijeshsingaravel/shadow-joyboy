"""compiler/compile.py — end-to-end Quick-compile -> spawn (E1 Task B5).

Orchestrates B2 (intent) -> B3 (clarify) -> B4 (emit) -> the REAL factory.loader/spawn
path. No bypass: the emitted role is written to a real YAML file and loaded through
load_agent_config exactly like a hand-authored agent, inheriting governance/memory/eval
by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from madras_capabilities.catalog import Catalog

from madras.compiler.clarify import needs_clarification
from madras.compiler.emit import emit_role
from madras.compiler.intent import compile_intent
from madras.factory.dynamic import AuthContext
from madras.factory.spawn import AgentRecord, spawn_agent, spawn_agent_preview
from madras.llm.gateway import LLMGateway

_MAX_NAME_SUFFIX = 50


class RoleNameCollision(ValueError):
    """The compiled agent's name collides with an existing hand-authored role file."""


@dataclass
class CompileResult:
    mode: str  # "draft" | "clarify"
    agent: AgentRecord | None = None
    questions: list[str] | None = None


def target_role_path(agents_dir: Path, name: str) -> tuple[Path, str]:
    """The writable path+name a Compiler-generated role is written to -- agents_dir/compiled/,
    never agents_dir/roles/ (deliberately read-only in the live container to protect
    hand-authored files from container-drift corruption; E1 Task E2 live-drive finding).

    Two independently-worded outcomes can legitimately derive the same role name (e.g.
    "triage_scribe" for two differently-phrased support-ticket outcomes) -- that's an
    expected collision, not a caller error, so on collision with another *compiled*
    agent this appends a numeric suffix (_2, _3, ...) and retries. Colliding with a
    hand-authored roles/ file (e.g. "shadow") is different: that's the Compiler
    stepping on a permanent flagship agent's name, which should be surfaced, not
    silently renamed -- so that case still raises. Suffix uses "_" (not "-") because
    AgentConfig.name must be snake_case."""
    roles_dir = Path(agents_dir) / "roles"
    compiled_dir = Path(agents_dir) / "compiled"
    compiled_dir.mkdir(parents=True, exist_ok=True)

    if (roles_dir / f"{name}.yaml").exists():
        raise RoleNameCollision(f"a role named {name!r} already exists")

    candidate = name
    for suffix in range(2, _MAX_NAME_SUFFIX + 1):
        if not (compiled_dir / f"{candidate}.yaml").exists():
            return compiled_dir / f"{candidate}.yaml", candidate
        candidate = f"{name}_{suffix}"

    raise RuntimeError(f"could not find a free role name derived from {name!r}")


async def compile_agent(
    *,
    outcome: str,
    gateway: LLMGateway,
    model: str,
    agents_dir: Path,
    catalog: Catalog,
    auth: AuthContext,
    clarify_threshold: float = 0.5,
    preview: bool = False,
) -> CompileResult:
    """preview=True is the guarded /build preview (E1 § A1/B2, "no execution"): the spec
    is compiled and validated exactly as for a real compile, but nothing is written to
    agents_dir/compiled/ and spawn_agent never touches disk -- zero side effects, so a
    tourist-tier preview can never collide with, or leave behind, a permanent role file."""
    spec = await compile_intent(
        outcome=outcome, gateway=gateway, model=model, catalog=catalog, auth=auth
    )

    assessment = await needs_clarification(spec, gateway, model, threshold=clarify_threshold)
    if assessment.action == "ask":
        return CompileResult(mode="clarify", questions=[assessment.question])

    role_data = emit_role(spec)

    if preview:
        record = spawn_agent_preview(
            agents_dir=agents_dir, role_name=spec.name, role_data=role_data
        )
        return CompileResult(mode="draft", agent=record)

    role_path, role_name = target_role_path(agents_dir, spec.name)
    role_data["name"] = role_name  # keep the persisted config.name in sync with its file slug
    role_path.write_text(yaml.safe_dump(role_data), encoding="utf-8")
    record = spawn_agent(agents_dir=agents_dir, role_name=role_name)  # validates internally
    return CompileResult(mode="draft", agent=record)
