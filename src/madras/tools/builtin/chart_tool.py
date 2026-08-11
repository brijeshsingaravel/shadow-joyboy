"""generate_chart -- static-artifact charting (row pygwalker).

PyGWalker (Kanaries, Apache-2.0, ~15.8k stars) is a human-interactive Tableau-style
UI (Jupyter/Streamlit widget) -- the wrong shape for an agent tool-call contract
(same category problem as Locust: built for a human clicking a UI, not a clean
input->artifact return). Madras had ZERO agent-callable charting capability
anywhere (grepped, confirmed). A ~50-line native matplotlib wrapper fits the
file.write-adjacent, single-shot contract cleanly; matplotlib cited PyGWalker's
static-export mode as prior art, not as code to fork.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, cast

from madras.models.agent_config import Rank
from madras.tools.registry import ToolResult, tool

_CHART_TYPES = ("line", "bar", "scatter")


def _workspace_root() -> Path:
    from madras.config import settings

    root = (
        Path(settings.madras_workspace)
        if settings.madras_workspace
        else Path(__file__).resolve().parents[4] / "workspace"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def render_chart(
    data: list[dict[str, Any]],
    *,
    chart_type: str,
    x: str,
    y: str,
    title: str = "",
    out_path: Path,
) -> None:
    """Pure-ish render step (I/O is writing the file) -- kept separate from the tool
    wrapper so it's directly unit-testable without a ToolResult round-trip."""
    import matplotlib as _matplotlib  # type: ignore[reportMissingTypeStubs]

    matplotlib: Any = _matplotlib
    matplotlib.use("Agg")  # headless -- no display server needed
    import matplotlib.pyplot as _plt  # type: ignore[reportMissingTypeStubs]

    plt: Any = _plt

    xs = [row.get(x) for row in data]
    ys = [row.get(y) for row in data]

    fig, ax = plt.subplots()
    if chart_type == "bar":
        ax.bar(xs, ys)
    elif chart_type == "scatter":
        ax.scatter(xs, ys)
    else:
        ax.plot(xs, ys)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    if title:
        ax.set_title(title)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)


@tool(
    name="generate_chart",
    toolset="file",
    rank_required=Rank.INTERN,
    description=(
        "Render a static chart (line/bar/scatter) from tabular data and save it as a "
        "PNG. Returns the file path. Use for a quick data-visualization artifact, not "
        "an interactive dashboard."
    ),
    parameters={
        "type": "object",
        "properties": {
            "data": {
                "type": "array",
                "items": {"type": "object"},
                "description": "rows, each a flat object of field->value",
            },
            "chart_type": {
                "type": "string",
                "description": "line | bar | scatter",
                "default": "line",
            },
            "x": {"type": "string", "description": "field name for the x axis"},
            "y": {"type": "string", "description": "field name for the y axis"},
            "title": {"type": "string", "description": "optional chart title"},
        },
        "required": ["data", "x", "y"],
    },
)
async def generate_chart(args: dict[str, Any]) -> ToolResult:
    raw_data = args.get("data")
    if not isinstance(raw_data, list) or not raw_data:
        return ToolResult(ok=False, error="data is required (a non-empty list of rows)")
    data = cast("list[dict[str, Any]]", raw_data)
    x = str(args.get("x", "")).strip()
    y = str(args.get("y", "")).strip()
    if not x or not y:
        return ToolResult(ok=False, error="x and y field names are required")
    chart_type = str(args.get("chart_type", "line")).strip().lower()
    if chart_type not in _CHART_TYPES:
        return ToolResult(ok=False, error=f"chart_type must be one of {', '.join(_CHART_TYPES)}")

    out_path = _workspace_root() / "charts" / f"chart-{uuid.uuid4().hex[:8]}.png"
    try:
        render_chart(
            data,
            chart_type=chart_type,
            x=x,
            y=y,
            title=str(args.get("title", "") or ""),
            out_path=out_path,
        )
    except Exception as exc:
        return ToolResult(ok=False, error=f"chart render failed: {type(exc).__name__}: {exc}")

    return ToolResult(
        ok=True,
        content=f"Chart saved to {out_path}",
        extras={"path": str(out_path), "chart_type": chart_type, "rows": len(data)},
    )
