"""`forge-doctor what-if|migrate` - deterministic simulation + planning."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr
from forge_doctor.cli.compatibility import migrate_app  # shared `migrate` group
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.migration import plan_migrations
from forge_doctor.core.whatif import evaluate_change, parse_change

whatif_app = typer.Typer(
    name="what-if", help="Evaluate a hypothetical change without executing it."
)
app.add_typer(whatif_app, name="what-if")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


@whatif_app.callback(invoke_without_command=True)
def whatif_run(
    ctx: typer.Context,
    path: _PathOpt = Path("."),
    change: Annotated[
        list[str] | None,
        typer.Option("--change", help="target=value, e.g. glue-version=5.1"),
    ] = None,
    assume: Annotated[
        list[str] | None,
        typer.Option("--assume", help="extra assumption fact recorded on the report"),
    ] = None,
) -> None:
    """Evaluate --change target=value specs against the project."""
    if not change:
        _stderr.print("usage: forge-doctor what-if --change glue-version=5.1 .")
        raise typer.Exit(2)
    pctx = ProjectContext(root=path.resolve())
    console = Console()
    exit_code = 0
    for spec in change:
        try:
            ch = parse_change(spec)
            if assume:
                from dataclasses import replace

                ch = replace(ch, assumptions=tuple(sorted(assume)))
        except ValueError as exc:
            _stderr.print(f"error: {exc}")
            raise typer.Exit(2) from exc
        report = evaluate_change(pctx, ch)
        console.print()
        console.print(
            f"[bold]What-if[/bold] {report.change.target}.{report.change.property}: "
            f"{report.change.from_ or 'unobserved'} → {report.change.to}"
        )
        if report.affected_entities:
            console.print(f"  affected entities ({len(report.affected_entities)}):")
            for e in report.affected_entities[:12]:
                console.print(f"    {e}")
            if len(report.affected_entities) > 12:
                console.print(f"    … +{len(report.affected_entities) - 12} more")
        else:
            console.print("  affected entities: none detected")
        for i in report.impacts:
            color = {"blocker": "red", "warn": "yellow", "info": "cyan"}.get(i.severity, "white")
            console.print(f"  [{color}]{i.severity}[/{color}] {i.detail}")
            for e in i.evidence:
                console.print(f"      {e}")
        if report.unsupported_now:
            console.print(f"  [red]lost capabilities[/red]: {', '.join(report.unsupported_now)}")
        if report.supported_now:
            console.print(
                f"  [green]gained capabilities[/green]: {', '.join(report.supported_now)}"
            )
        if report.unknown:
            for u in report.unknown:
                console.print(f"  [dim]unknown[/dim] {u}")
        if report.has_blockers:
            exit_code = 1
    console.print()
    raise typer.Exit(exit_code)


@migrate_app.command(name="plan")
def migrate_plan(path: _PathOpt = Path(".")) -> None:
    """Enumerate applicable migration plans for the project."""
    ctx = ProjectContext(root=path.resolve())
    plans = plan_migrations(ctx)
    console = Console()
    console.print()
    console.print("[bold]Migration Plans[/bold]")
    if not plans:
        console.print("  no applicable migration paths detected")
        return
    for p in plans:
        console.print(
            f"\n[bold]{p.path_id}[/bold]  {p.source_environment} → {p.target_environment}"
        )
        if p.affected_entities:
            console.print(f"  affected ({len(p.affected_entities)}):")
            for e in p.affected_entities[:8]:
                console.print(f"    {e}")
            if len(p.affected_entities) > 8:
                console.print(f"    … +{len(p.affected_entities) - 8} more")
        for section, items, color in (
            ("blockers", p.blockers, "red"),
            ("warnings", p.warnings, "yellow"),
            ("required changes", p.required_changes, "cyan"),
            ("validation", p.validation_steps, "cyan"),
            ("rollback", p.rollback, "dim"),
        ):
            if items:
                console.print(f"  [{color}]{section}[/{color}]:")
                for item in items:
                    console.print(f"    - {item}")
    console.print()
