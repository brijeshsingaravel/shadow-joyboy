"""Built-in governed tools — importing this package self-registers them.

Every submodule below is imported purely for its @tool-decorator registration side
effect, never referenced by name here.

This list IS the boundary of what this repo can do. A toolset that is not imported
here cannot be enabled by configuration, only by editing this file -- which is the
point. `messaging` is absent deliberately and permanently: send_message is on the
irreversible-actions list, and nobody's first experiment with a new agent should be
able to send email as them.
"""

# pyright: reportUnusedImport=false

from madras.tools.builtin import (  # noqa: F401
    background_tools,
    browser,
    chart_tool,
    clarify,
    dangerous,
    files,
    gitleaks_tool,
    intel,
    mcp_tools,
    memory_fabric_tools,
    memory_tools,
    notes,
    relationship_tools,
    research_tool,
    review_tool,
    search,
    session_tools,
    stack_tools,
    tool_discovery,
    turn_tools,
    user_model_tools,
    web,
)
