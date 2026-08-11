"""Multi-API model adapters — translate one unified chat request to/from each vendor's wire
format (Anthropic, OpenAI/Codex, Gemini, Bedrock). Lets the IDE-native [[ACP]] surface (and the
agent) speak to any backend behind one shape. Pure translation — no network here.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

Msg = dict[str, str]  # {"role": "...", "content": "..."}


@runtime_checkable
class ModelApiAdapter(Protocol):
    name: str

    def format_request(self, messages: list[Msg], model: str, **kw: Any) -> dict[str, Any]: ...

    def extract_text(self, response: dict[str, Any]) -> str: ...


def _split_system(messages: list[Msg]) -> tuple[str, list[Msg]]:
    system = " ".join(m["content"] for m in messages if m.get("role") == "system")
    rest = [m for m in messages if m.get("role") != "system"]
    return system, rest


class AnthropicAdapter:
    name = "anthropic"

    def format_request(
        self, messages: list[Msg], model: str, *, max_tokens: int = 1024, **kw: Any
    ) -> dict[str, Any]:
        system, rest = _split_system(messages)
        req: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": m["role"], "content": m["content"]} for m in rest],
        }
        if system:
            req["system"] = system
        return req

    def extract_text(self, response: dict[str, Any]) -> str:
        blocks: list[dict[str, Any]] = response.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


class OpenAIAdapter:
    name = "openai"  # OpenAI / Codex chat-completions shape

    def format_request(self, messages: list[Msg], model: str, **kw: Any) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        }

    def extract_text(self, response: dict[str, Any]) -> str:
        choices: list[dict[str, Any]] = response.get("choices", [])
        return choices[0]["message"]["content"] if choices else ""


class GeminiAdapter:
    name = "gemini"

    def format_request(self, messages: list[Msg], model: str, **kw: Any) -> dict[str, Any]:
        system, rest = _split_system(messages)
        role_map = {"assistant": "model", "user": "user"}
        req: dict[str, Any] = {
            "model": model,
            "contents": [
                {"role": role_map.get(m["role"], "user"), "parts": [{"text": m["content"]}]}
                for m in rest
            ],
        }
        if system:
            req["systemInstruction"] = {"parts": [{"text": system}]}
        return req

    def extract_text(self, response: dict[str, Any]) -> str:
        cands: list[dict[str, Any]] = response.get("candidates", [])
        if not cands:
            return ""
        parts: list[dict[str, Any]] = cands[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)


class BedrockAdapter:
    name = "bedrock"  # Anthropic-on-Bedrock shape (no model field; version key)

    def format_request(
        self, messages: list[Msg], model: str, *, max_tokens: int = 1024, **kw: Any
    ) -> dict[str, Any]:
        system, rest = _split_system(messages)
        req: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": m["role"], "content": m["content"]} for m in rest],
        }
        if system:
            req["system"] = system
        return req

    def extract_text(self, response: dict[str, Any]) -> str:
        blocks: list[dict[str, Any]] = response.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


ADAPTERS: dict[str, ModelApiAdapter] = {
    a.name: a for a in (AnthropicAdapter(), OpenAIAdapter(), GeminiAdapter(), BedrockAdapter())
}


def get_adapter(name: str) -> ModelApiAdapter:
    try:
        return ADAPTERS[name.lower()]
    except KeyError:
        raise ValueError(f"unknown model API '{name}' (have: {sorted(ADAPTERS)})") from None
