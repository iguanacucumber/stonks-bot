"""Static human dashboard for the paper live book and research evidence.

The package is read-only.  It never starts the engine, fetches data, writes state or
uses an external asset.  Missing/corrupt inputs become explicit warnings so a
dashboard can still be rendered after a partial failure.
"""

from .collect import (  # noqa: F401
    BookSnapshot,
    DashboardData,
    EvalSummary,
    Finding,
    Gate,
    collect_dashboard,
    demo_dashboard,
)
from .render import render_html, render_markdown  # noqa: F401

__all__ = [
    "BookSnapshot",
    "DashboardData",
    "EvalSummary",
    "Finding",
    "Gate",
    "collect_dashboard",
    "demo_dashboard",
    "render_html",
    "render_markdown",
]
