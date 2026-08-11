"""AgentState — what LangGraph carries through the graph."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages  # type: ignore[import-untyped]


class AgentState(TypedDict, total=False):
    """Shared state for every turn. Every field must be JSON-serializable
    (BaseMessage subclasses serialize via LangChain's built-in handlers)."""

    agent_name: str
    session_id: str
    user_input: str
    messages: Annotated[list[BaseMessage], add_messages]
    eval_signals: dict[str, Any]
    working_memory_ref: str | None
    reflex_map_ref: str | None
    supervisor_verdict: dict[str, Any] | None
