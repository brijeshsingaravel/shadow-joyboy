"""Direct-GGUF backend via `llama-cpp-python` — GBNF-constrained decoding (RFC-0002 §6.4/§9, T4).

Ollama's API exposes only JSON-schema structured output, not arbitrary CFG grammars
(`ollama/ollama#6237`) — not viable for constraining a model to Kural's actual `.tamil` syntax. This
backend loads a local GGUF directly and, when a grammar is supplied
(`req.metadata["gbnf_grammar"]` or `req.metadata["gbnf_path"]`), constrains every generated token to
it — the mechanism T4 proves. Absent a grammar, it behaves like an ordinary chat completion.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from madras.llm.gateway import LLMBackend, LLMRequest, LLMResponse

if TYPE_CHECKING:
    from llama_cpp import CreateChatCompletionResponse


class LlamaCppBackend(LLMBackend):
    def __init__(
        self,
        *,
        model_path: str,
        n_ctx: int = 4096,
        verbose: bool = False,
    ) -> None:
        # Deferred import: llama_cpp pulls in a compiled extension; keep it optional at
        # module-load time so a plain `import madras.llm` never requires the wheel to be
        # installed (matches LiteLLMBackend's __init__-time-never-required-key discipline).
        from llama_cpp import Llama

        self._model_path = model_path
        self._llm = Llama(model_path=model_path, n_ctx=n_ctx, verbose=verbose)

    async def complete(self, req: LLMRequest) -> LLMResponse:
        from llama_cpp import LlamaGrammar

        start = time.perf_counter()
        grammar = None
        grammar_text = req.metadata.get("gbnf_grammar")
        grammar_path = req.metadata.get("gbnf_path")
        if grammar_text:
            grammar = LlamaGrammar.from_string(grammar_text)
        elif grammar_path:
            grammar = LlamaGrammar.from_file(str(Path(grammar_path)))

        kwargs: dict[str, Any] = {
            "messages": req.messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if grammar is not None:
            kwargs["grammar"] = grammar
        if req.seed is not None:
            kwargs["seed"] = req.seed

        # Never streamed (no `stream=True` in kwargs), so this is always the plain response,
        # not the Iterator[...] streaming variant `create_chat_completion`'s signature allows.
        result = cast("CreateChatCompletionResponse", self._llm.create_chat_completion(**kwargs))
        latency_ms = (time.perf_counter() - start) * 1000.0

        choice = result["choices"][0]
        text = choice["message"].get("content") or ""
        usage = result.get("usage") or {}

        return LLMResponse(
            text=text,
            model=req.model or self._model_path,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            cost_usd=0.0,  # local inference, no metered cost
            latency_ms=latency_ms,
            raw=dict(result),
        )
