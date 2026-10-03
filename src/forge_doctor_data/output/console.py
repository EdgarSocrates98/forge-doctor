"""Rich console renderer. Presentation only - no analysis here."""

from __future__ import annotations

import sys
from collections import Counter

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from forge_doctor_data.core.models import CheckResult, Confidence, ScanReport, Severity

_UNICODE_ICONS = {
    Severity.PASS: "[green]✓[/green]",
    Severity.INFO: "[blue]ℹ[/blue]",
    Severity.WARNING: "[yellow]⚠[/yellow]",
    Severity.ERROR: "[red]✗[/red]",
}
_ASCII_ICONS = {
    Severity.PASS: "[green]+[/green]",
    Severity.INFO: "[blue]i[/blue]",
    Severity.WARNING: "[yellow]![/yellow]",
    Severity.ERROR: "[red]x[/red]",
}
_STYLES = {
    Severity.PASS: "green",
    Severity.INFO: "blue",
    Severity.WARNING: "yellow",
    Severity.ERROR: "red",
}
_SEV_RANK = {
    Severity.ERROR: 3,
    Severity.WARNING: 2,
    Severity.INFO: 1,
    Severity.PASS: 0,
}


def _unicode_output() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in encoding


def _icons() -> dict[Severity, str]:
    """Unicode icons when the output stream can encode them, ASCII otherwise."""
    return _UNICODE_ICONS if _unicode_output() else _ASCII_ICONS


_CATEGORY_LABELS = {
    "aws": "AWS",
    "ci": "CI",
    "iac": "IaC",
    "glue": "Glue",
    "sql": "SQL",
    "airflow": "Airflow",
    "controlm": "Control-M",
    "dynamodb": "DynamoDB",
    "iceberg": "Iceberg",
    "kafka": "Kafka",
    "kinesis": "Kinesis",
    "flink": "Flink",
    "lakeformation": "Lake Formation",
    "neptune": "Neptune",
    "parquet": "Parquet",
    "spark": "Spark",
    "stepfunctions": "Step Functions",
    "terraform": "Terraform",
    "emr": "EMR",
    "databricks": "Databricks",
    "athena": "Athena",
    "serverless": "Serverless",
    "repository": "Repository",
    "dependencies": "Dependencies",
    "python": "Python",
    "docker": "Docker",
    "git": "Git",
    "streaming": "Streaming",
    "streaming-bus": "Streaming Bus",
    "architecture": "Architecture",
    "platform": "Platform",
    "platforms": "Platforms",
    "policy": "Policy",
    "graph": "Graph",
    "data-model": "Data Model",
    "schema": "Schema",
    "runtime": "Runtime",
}


def _category_label(category: str) -> str:
    """Human-facing section header for a check category."""
    return _CATEGORY_LABELS.get(category, category.replace("-", " ").title())


class ConsoleRenderer:
    def __init__(self, console: Console | None = None, quiet: bool = False) -> None:
        self._console = console or Console()
        self._quiet = quiet

    def render(self, report: ScanReport, check_count: int = 0) -> None:
        self._console.print()
        self._console.print(self._header(report, check_count))
        self._console.print()

        by_category: dict[str, list[CheckResult]] = {}
        for result in sorted(
            report.results,
            key=lambda r: (r.check_id, str(r.file or ""), r.line or 0),
        ):
            by_category.setdefault(result.category, []).append(result)

        # Worst-first: categories containing errors/warnings surface at the
        # top; ties fall back to alphabetical for determinism.
        ordered = sorted(
            by_category,
            key=lambda c: (
                -max(_SEV_RANK[r.severity] for r in by_category[c]),
                c,
            ),
        )
        for category in ordered:
            group = by_category[category]
            if self._quiet and all(r.severity in (Severity.PASS, Severity.INFO) for r in group):
                continue
            header = f"[bold underline]{_category_label(category)}[/bold underline]"
            counts = Counter(r.severity for r in group)
            badges = ", ".join(
                f"{n} {label}"
                for sev, label in (
                    (Severity.ERROR, "error"),
                    (Severity.WARNING, "warning"),
                    (Severity.INFO, "info"),
                )
                if (n := counts.get(sev)) and not (self._quiet and sev is Severity.INFO)
            )
            if badges:
                header += f"  [dim]{badges}[/dim]"
            self._console.print(header)
            for result in group:
                self._render_result(result)
            self._console.print()

        self._console.print(self._summary_panel(report))
        self._render_tip(report)

    def _header(self, report: ScanReport, check_count: int) -> Panel:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold", justify="right")
        grid.add_column()
        grid.add_row("Project", str(report.project))
        grid.add_row("Version", report.version)
        if check_count:
            grid.add_row("Checks", str(check_count))
        return Panel(
            grid,
            title="[bold]Forge Doctor Data[/bold]",
            title_align="left",
            border_style="cyan",
            expand=False,
        )

    def _render_result(self, result: CheckResult) -> None:
        if self._quiet and result.severity in (Severity.PASS, Severity.INFO):
            return
        icon = _icons()[result.severity]
        location = ""
        if result.file is not None and result.file.name != result.title:
            location = (
                f" [dim]{result.file.as_posix()}{f':{result.line}' if result.line else ''}[/dim]"
            )
        new = " [magenta bold]NEW[/magenta bold]" if result.is_new else ""
        conf = ""
        if result.confidence in (Confidence.MEDIUM, Confidence.LOW):
            conf = f" [dim]({result.confidence.value} confidence)[/dim]"
        self._console.print(
            f"  {icon} [dim]{result.check_id}[/dim] {result.title}{location}{new}{conf}"
        )
        if result.message and result.severity is not Severity.PASS:
            self._console.print(Padding(Text(result.message, style="dim"), pad=(0, 0, 0, 6)))
        if result.evidence and result.severity is not Severity.PASS:
            self._console.print(
                Padding(Text(f"> {result.evidence}", style="dim italic"), pad=(0, 0, 0, 6))
            )
        if result.recommendation and result.severity in (
            Severity.WARNING,
            Severity.ERROR,
        ):
            arrow = "→" if _unicode_output() else "->"
            self._console.print(
                Padding(
                    Text(f"{arrow} {result.recommendation}", style="dim"),
                    pad=(0, 0, 0, 6),
                )
            )

    def _render_tip(self, report: ScanReport) -> None:
        """Discoverability: point at `explain` for the worst finding."""
        worst = min(
            (r for r in report.results if r.severity in (Severity.WARNING, Severity.ERROR)),
            key=lambda r: (-_SEV_RANK[r.severity], r.check_id),
            default=None,
        )
        if worst is None or self._quiet:
            return
        arrow = "→" if _unicode_output() else "->"
        self._console.print(
            Padding(
                Text(
                    f"{arrow} `forge-doctor-data explain {worst.check_id}` "
                    "explains the worst finding (why / when-ok / fix)",
                    style="dim",
                ),
                pad=(0, 0, 0, 2),
            )
        )

    def _summary_panel(self, report: ScanReport) -> Panel:
        summary = report.summary
        if summary.errors:
            verdict, style = f"{summary.errors} error(s) found", "red"
        elif summary.warnings:
            verdict, style = f"{summary.warnings} warning(s)", "yellow"
        else:
            verdict, style = "No problems found", "green"

        counts = Text()
        for severity, label, value in (
            (Severity.PASS, "Passed", summary.passed),
            (Severity.INFO, "Info", summary.info),
            (Severity.WARNING, "Warnings", summary.warnings),
            (Severity.ERROR, "Errors", summary.errors),
        ):
            counts.append(f"{label} ", style="dim")
            counts.append(f"{value}", style=f"bold {_STYLES[severity]}")
            counts.append("   ")

        lines = Text.assemble(counts, "\n")
        lines.append(f"{verdict}", style=f"bold {style}")

        if report.baseline is not None:
            lines.append("\n")
            lines.append(
                Text.from_markup(
                    f"baseline: [magenta]+{report.baseline.new} new[/magenta], "
                    f"[green]-{report.baseline.fixed} fixed[/green], "
                    f"{report.baseline.existing} pre-existing"
                )
            )
        return Panel(lines, title="[bold]Summary[/bold]", border_style=style, expand=False)
