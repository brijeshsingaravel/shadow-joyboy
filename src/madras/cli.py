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
from madras.llm.reply_text import explain_empty_reply
from madras.mindpalace.ledger import MindPalaceLedger
from madras.security.crisis import CrisisSupport, strip_contacts
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


async def _migrate(_: argparse.Namespace) -> int:
    """Create the database tables. Run this once, after the databases are up.

    WHY THIS EXISTS. Nothing in this codebase creates its own tables: `MemoryFabric` has no
    `CREATE TABLE` and no `setup()`. Without this step a fresh Postgres has no schema, and the
    first thing you tell Shadow fails with `relation "madras_memory" does not exist`.

    That was the state this repository shipped in until CI caught it on the first push. Every
    test before that ran against a database which had been carrying these tables for months, so
    the one experience that was never exercised was the only one a stranger has.
    """
    import asyncpg

    migrations = sorted((Path(__file__).resolve().parents[2] / "infra/migrations").glob("*.sql"))
    if not migrations:
        print("No migrations found — expected them in infra/migrations/")
        return 1

    # admin_url falls back to postgres_url, which is what a `docker compose up -d` install has:
    # one role that owns everything. The parent project separates them so the app role cannot
    # run DDL, and that separation is worth keeping if you ever deploy this for other people.
    print(f"Applying {len(migrations)} migrations to {settings.admin_url.split('@')[-1]}")
    conn = await asyncpg.connect(settings.admin_url)
    try:
        for f in migrations:
            try:
                await conn.execute(f.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  FAILED on {f.name}: {type(e).__name__}: {e}")
                return 1
        # Verify the artifact, not the exit code: the tables must actually be there afterwards.
        missing = [
            t for t in ("madras_memory", "madras_turn_log", "madras_audit_log")
            if not await conn.fetchval("select to_regclass($1) is not null", f"public.{t}")
        ]
        if missing:
            print(f"  migrations ran but these tables are absent: {missing}")
            return 1
    finally:
        await conn.close()
    print("Done. The tables exist and Shadow can remember.")
    return 0


async def _chat(args: argparse.Namespace) -> int:
    from langchain_core.messages import AIMessage, HumanMessage

    gateway = _gateway(backend=args.backend, timeout=args.timeout)
    agent = spawn_agent(agents_dir=AGENTS_DIR, role_name="shadow")

    ledger = None
    if not args.no_memory:
        try:
            ledger = MindPalaceLedger(postgres_url=settings.postgres_url)
        except Exception as e:
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
        # A reply can arrive empty. The model is a reasoning model: given a hard question it can
        # spend its whole token budget thinking and be cut off before writing an answer, and the
        # transport calls that a success -- HTTP 200, valid JSON, finish_reason "length", content
        # "". Found by benchmarking, where one answer in eight came back blank. Printed raw, a
        # person sees nothing and cannot tell that from a crash, so say what happened instead.
        said = explain_empty_reply(
            str(reply.content), finish_reason=result.get("finish_reason")
        )
        # THE FOURTH DOOR. Everything above this is how a person actually talks to Shadow when
        # they have cloned it and run it themselves -- there is no server in this repo, so this
        # function IS the product. Without these three lines a clone answers someone who says
        # they want to die however a small model happens to answer, and the module sitting in
        # security/crisis.py is never called by anything.
        #
        # Upstream, the same seam is wired into all three HTTP endpoints. It was wired into only
        # one of them for a while, which is exactly how a safety layer becomes decoration: it is
        # worth the number of doors it is actually attached to.
        #
        # MADRAS_CRISIS_HELP_URL is empty by default and that is the safe failure. A clone in
        # another country gets the sentence that is true everywhere -- tell someone you trust,
        # tonight -- and no link, rather than an Indian helpline that cannot help them.
        verdict = CrisisSupport(help_url=settings.crisis_help_url).inspect(text)
        if verdict.detected:
            stripped = strip_contacts(said)
            said = "\n\n".join(p for p in (verdict.message, stripped) if p)
        history.append(HumanMessage(content=text))
        history.append(AIMessage(content=said))
        print(f"\n{agent.config.display_name}: {said}\n")

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
    # Defaults to MADRAS_LLM_BACKEND from your .env, so the file the README tells you to
    # edit actually decides this. The flag still overrides it for a one-off.
    c.add_argument("--backend", default=settings.llm_backend,
                   choices=["ollama", "openrouter"])
    c.add_argument("--timeout", type=float, default=120.0)
    c.add_argument("--session-id", default="cli")
    c.add_argument("--project", default="shadow-cli", help="which Mind Palace to remember into")
    c.add_argument("--no-memory", action="store_true", help="don't persist this conversation")
    c.set_defaults(fn=lambda a: asyncio.run(_chat(a)))

    t = sub.add_parser("tools", help="list every tool this build has")
    t.set_defaults(fn=cmd_tools)

    m = sub.add_parser(
        "migrate", help="create the database tables (run once, after the DBs are up)"
    )
    m.set_defaults(fn=lambda a: asyncio.run(_migrate(a)))

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
