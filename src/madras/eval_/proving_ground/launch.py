"""Plan 3 Task 2 — launch wrapper wiring the real 5-model judge into a run.

`launch_run` builds a `gateway_for(name)` factory (each judge routed by model
name through a LiteLLM-backed `LLMGateway` — the model is selected per
`LLMRequest`, so one proxy gateway serves every judge), constructs the real
`judge_call` via `make_judge_call`, and runs `run_proving_ground` over the
bundled scenario bank. No secrets are hardcoded: the per-model gateway reads its
proxy credentials from `settings` and is only built on the production path
(tests monkeypatch `make_judge_call`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from madras.eval_.proving_ground.judge_runner import make_judge_call
from madras.eval_.proving_ground.run import run_proving_ground
from madras.llm.gateway import LLMGateway

_BANK_DIR = Path(__file__).resolve().parent / "scenarios"


def _gateway_for_factory() -> Any:
    """Return `gateway_for(name)` building a LiteLLM-routed gateway per judge model.

    The proxy credentials come from `settings` (master vault); never hardcoded.
    Built lazily so importing this module never requires a configured vault.
    """
    from madras.config import settings
    from madras.llm.litellm import LiteLLMBackend

    def gateway_for(name: str) -> LLMGateway:
        return LLMGateway(
            backend=LiteLLMBackend(
                api_key=settings.litellm_master_key, base_url=settings.litellm_base_url
            )
        )

    return gateway_for


async def launch_run(
    *,
    store: Any,
    gateway: Any,
    judges: list[str] | None = None,
    run_id: str,
    head_sha: str = "",
) -> dict[str, Any]:
    gateway_for = _gateway_for_factory()
    judge_call = make_judge_call(gateway_for)
    return await run_proving_ground(
        bank_dir=_BANK_DIR,
        gateway=gateway,
        store=store,
        judge_call=judge_call,
        run_id=run_id,
        head_sha=head_sha,
        judges=judges,
    )
