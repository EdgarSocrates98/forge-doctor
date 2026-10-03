"""Self-contained HTML report via Rich export - shareable, zero deps."""

from __future__ import annotations

import html as _html
import re

from rich.console import Console

from forge_doctor_data.core.models import ScanReport
from forge_doctor_data.output.console import ConsoleRenderer


def render_html(report: ScanReport, check_count: int = 0) -> str:
    """Render the console report as a standalone HTML document."""
    console = Console(
        record=True,
        force_terminal=True,
        width=100,
        color_system="truecolor",
    )
    ConsoleRenderer(console=console).render(report, check_count=check_count)
    rendered = console.export_html(inline_styles=True)
    title = _html.escape(f"Forge Doctor Data - {report.project.name}")
    return re.sub(r"<title>.*?</title>", f"<title>{title}</title>", rendered, count=1)
