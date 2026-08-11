"""Chat with Shadow from the terminal.

    python -m madras.cli chat                 # interactive
    python -m madras.cli chat "who are you?"  # one-shot
    python -m madras.cli tools                # what this build can do

Defaults to a local model through Ollama, because that is the configuration where nothing you
type leaves your machine. Everything else is opt-in.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from madras import settings
from madras.factory.spawn import spawn_agent
from madras.graph.build import build_llm_graph
from madras.llm.gateway import LLMGateway
from madras.llm.litellm import LiteLLMBackend
from madras.llm.openrouter import OpenRouterBackend
from madras.mindpalace.ledger import MindPalaceLedger
from madras.tools.registry import REGISTRY

AGENTS_DIR = Path(__file__).resolve().parents[2] / "agents"


def _gateway(*, backend: str, timeout: float) -> LLMGateway:
    """Ollama by default; OpenRouter only if asked for and configured."""
    if backend == "openrouter":
        key = settings.openrouter_api_key
        if not key:
            raise SystemExit(
                "--backend openrouter needs MADRAS_OPENROUTER_API_KEY in your .env.\n"
                "Or drop the flag and run a local model through Ollama instead."
            )
        return LLMGateway(backend=OpenRouterBackend(api_key=key))

    # Ollama speaks the OpenAI protocol at /v1, so the same client works unchanged.
    return LLMGateway(
        backend=LiteLLMBackend(
            api_key="ollama",  # Ollama ignores it; the client requires something non-empty
            base_url=settings.ollama_url.rstrip("/").removesuffix("/v1") + "/v1",
            timeout=timeout,
        )
    )


def cmd_tools(_: argparse.Namespace) -> int:
    """Print exactly what this build can do. No surprises later."""
    import madras.tools.builtin  # noqa: F401  (registers them)

    tools = sorted(REGISTRY.all(), key=lambda t: (t.toolset, t.name))
    current = None
    for t in tools:
        if t.toolset != current:
            current = t.toolset
            print(f"\n{current}")
        print(f"    {t.name:<28} {t.description.splitlines()[0][:70]}")
    print(f"\n{len(tools)} tools across {len(REGISTRY.toolsets())} toolsets.")
    print("This list is fixed by src/madras/tools/builtin/__init__.py — nothing else can appear.")
    return 0


async def _chat(args: argparse.Namespace) -> int:
    from langchain_core.messages import AIMessage, HumanMessage

    gateway = _gateway(backend=args.backend, timeout=args.timeout)
    agent = spawn_agent(agents_dir=AGENTS_DIR, role_name="shadow")

    ledger = None
    if not args.no_memory:
        try:
            ledger = MindPalaceLedger(postgres_url=settings.postgres_url)
        except Exception as e:  # noqa: BLE001 — a missing database must not be a crash
            print(f"! memory is off: {type(e).__name__}: {e}")
            print("  Shadow will answer, but will not remember this conversation.")
            print("  Start Postgres (see README) or pass --no-memory to silence this.\n")

    graph = build_llm_graph(
        agent, gateway=gateway, model=args.model, ledger=ledger, project=args.project
    )
    print(f"{agent.config.display_name} · {args.model} via {args.backend}"
          f"{'' if ledger else ' · no memory'}")

    history: list = []

    async def turn(text: str) -> None:
        result = await graph.ainvoke({
            "agent_name": agent.config.name,
            "session_id": args.session_id,
            "user_input": text,
            "messages": list(history),
        })
        reply = result["messages"][-1]
        history.append(HumanMessage(content=text))
        history.append(AIMessage(content=str(reply.content)))
        print(f"\n{agent.config.display_name}: {reply.content}\n")

    try:
        if args.prompt:
            await turn(args.prompt)
            return 0
        print("Type 'exit' or press Ctrl-C to leave.\n")
        while True:
            try:
                text = input("you: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye.")
                return 0
            if text.lower() in {"exit", "quit"}:
                print("bye.")
                return 0
            if text:
                await turn(text)
    finally:
        if ledger is not None:
            await ledger.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="madras", description="Chat with Shadow.")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("chat", help="talk to Shadow")
    c.add_argument("prompt", nargs="?", default=None, help="one-shot prompt; omit for a REPL")
    c.add_argument("--model", default="qwen3.5:4b", help="model id (default: qwen3.5:4b)")
    c.add_argument("--backend", default="ollama", choices=["ollama", "openrouter"])
    c.add_argument("--timeout", type=float, default=120.0)
    c.add_argument("--session-id", default="cli")
    c.add_argument("--project", default="shadow-cli", help="which Mind Palace to remember into")
    c.add_argument("--no-memory", action="store_true", help="don't persist this conversation")
    c.set_defaults(fn=lambda a: asyncio.run(_chat(a)))

    t = sub.add_parser("tools", help="list every tool this build has")
    t.set_defaults(fn=cmd_tools)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
