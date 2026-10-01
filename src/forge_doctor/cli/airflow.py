"""`forge-doctor airflow` - model-driven Airflow inspection commands."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.analyzers.airflow_model import AirflowModel, airflow_model
from forge_doctor.checks.airflow import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr, render_findings
from forge_doctor.core.context import ProjectContext

airflow_app = typer.Typer(
    name="airflow", help="Airflow intelligence: inspect DAGs, tasks, sensors."
)
app.add_typer(airflow_app, name="airflow")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


def _model_at(path: Path) -> tuple[ProjectContext, AirflowModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, airflow_model(ctx)


@airflow_app.command(name="inspect")
def airflow_inspect(
    path: _PathOpt = Path("."),
) -> None:
    """Summarize the project's Airflow surface from the semantic model."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Airflow environment[/bold]")

    if not model.has_airflow:
        console.print("  no Airflow evidence detected")
        return

    if model.dags:
        console.print("\n[bold]DAGs[/bold]")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold")
        grid.add_column()
        grid.add_column(style="dim")
        for dag in model.dags:
            sched = dag.schedule or "(none)"
            grid.add_row(
                dag.dag_id or dag.var,
                sched,
                f"{dag.task_count} tasks",
            )
        console.print(grid)

    if model.tasks:
        console.print()
        ops = Table(
            title="Tasks by operator",
            title_justify="left",
            show_edge=False,
            pad_edge=False,
        )
        ops.add_column("operator", style="bold", min_width=24)
        ops.add_column("count", justify="right")
        for name, count in sorted(Counter(t.operator for t in model.tasks).items()):
            ops.add_row(name, str(count))
        console.print(ops)

        sensors = [t for t in model.tasks if t.is_sensor]
        if sensors:
            deferrable = sum(1 for t in sensors if t.deferrable)
            console.print(
                f"  sensors: {len(sensors)} ({deferrable} deferrable, "
                f"{len(sensors) - deferrable} poke mode)"
            )

    if model.edges:
        console.print(f"\n[bold]Dependencies[/bold]  {len(model.edges)} edges")
        for e in model.edges[:12]:
            console.print(f"  {e.src} -> {e.dst}")
        if len(model.edges) > 12:
            console.print(f"  ... +{len(model.edges) - 12} more")

    if model.providers:
        console.print(f"\n[bold]Providers[/bold]  {', '.join(sorted(model.providers))}")

    if model.parse_calls or model.variable_gets:
        console.print("\n[bold]Parse-time calls[/bold]")
        for root, file, line in model.parse_calls:
            console.print(f"  {root}()  {file.as_posix()}:{line}")
        for file, line in model.variable_gets:
            console.print(f"  Variable.get  {file.as_posix()}:{line}")

    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@airflow_app.callback(invoke_without_command=True)
def _airflow_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor airflow inspect`")
        raise typer.Exit(2)
