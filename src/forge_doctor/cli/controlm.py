"""`forge-doctor controlm` - model-driven Control-M inspection commands."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.analyzers.controlm_model import ControlMModel, controlm_model
from forge_doctor.checks.controlm import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr, render_findings
from forge_doctor.core.context import ProjectContext

controlm_app = typer.Typer(
    name="controlm", help="Control-M intelligence: inspect workflows-as-code."
)
app.add_typer(controlm_app, name="controlm")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


def _model_at(path: Path) -> tuple[ProjectContext, ControlMModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, controlm_model(ctx)


def _job_types_table(model: ControlMModel) -> Table:
    table = Table(title="Jobs by type", title_justify="left", show_edge=False, pad_edge=False)
    table.add_column("type", style="bold", min_width=18)
    table.add_column("count", justify="right")
    for name, count in sorted(Counter(j.job_type for j in model.jobs).items()):
        table.add_row(name, str(count))
    return table


def _events_block(model: ControlMModel, console: Console) -> None:
    produced = model.events_produced
    consumed = model.events_consumed
    if not produced and not consumed:
        return
    unmatched = sorted(set(consumed) - set(produced))
    console.print("\n[bold]Events[/bold]")
    if produced:
        console.print(f"  produced   {', '.join(sorted(produced))}")
    if consumed:
        console.print(f"  consumed   {', '.join(sorted(consumed))}")
    if unmatched:
        console.print(f"  [yellow]unmatched  {', '.join(unmatched)} (never produced)[/yellow]")


@controlm_app.command(name="inspect")
def controlm_inspect(
    path: _PathOpt = Path("."),
) -> None:
    """Summarize the project's Control-M surface from the semantic model."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Control-M environment[/bold]")

    if not model.has_controlm:
        console.print("  no Control-M evidence detected")
        return

    if model.files:
        console.print(f"\n[bold]Definitions[/bold]  {len(model.files)} files")
        for f in model.files:
            console.print(f"  {f.as_posix()}")

    if model.folders:
        console.print("\n[bold]Folders[/bold]")
        for folder in model.folders:
            console.print(f"  {folder.name}")

    if model.jobs:
        console.print()
        console.print(_job_types_table(model))
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold")
        grid.add_column()
        grid.add_column(style="dim")
        for job in model.jobs:
            target = job.host or job.hostgroup or "-"
            sched = "scheduled" if job.has_schedule else ("event" if job.wait_events else "manual")
            name = f"{job.folder}/{job.name}" if job.folder else job.name
            grid.add_row(name, target, sched)
        console.print(grid)

    _events_block(model, console)

    if model.calendars or model.calendar_refs:
        defined = sorted(model.calendars)
        missing = sorted(set(model.calendar_refs) - set(model.calendars))
        console.print("\n[bold]Calendars[/bold]")
        if defined:
            console.print(f"  defined      {', '.join(defined)}")
        if missing:
            console.print(f"  [yellow]undefined    {', '.join(missing)}[/yellow]")

    if model.site_standards:
        console.print("\n[bold]Site standards[/bold]")
        for name, file, line in model.site_standards:
            console.print(f"  {name}  [dim]{file.as_posix()}:{line}[/dim]")

    if model.cli_refs or model.api_refs or model.deploy_descriptors:
        console.print("\n[bold]References[/bold]")
        for file, line in model.cli_refs:
            console.print(f"  ctm CLI          {file.as_posix()}:{line}")
        for file, line in model.api_refs:
            console.print(f"  automation-api   {file.as_posix()}:{line}")
        for f in model.deploy_descriptors:
            console.print(f"  deploy descriptor  {f.as_posix()}")

    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@controlm_app.callback(invoke_without_command=True)
def _controlm_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor controlm inspect`")
        raise typer.Exit(2)
