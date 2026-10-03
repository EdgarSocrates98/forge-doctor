"""`forge-doctor-data quality` - declared data-quality estate inspection (spec 222)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.analyzers.quality_model import quality_model
from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext

quality_app = typer.Typer(
    name="quality",
    help="Data quality: Deequ/GX/SodaCL/dbt suites, coverage, gate wiring.",
)
app.add_typer(quality_app, name="quality")


@quality_app.command(name="inspect")
def quality_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print declared suites, coverage map, and gate wiring."""
    ctx = ProjectContext(root=path.resolve())
    model = quality_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]data quality[/bold]")
    if not model.has_evidence:
        console.print("  no data-quality evidence detected")
        return

    if model.suites:
        console.print("\n[bold]Expectation suites[/bold]")
        for s in model.suites:
            bits = [s.engine]
            if s.table:
                bits.append(f"table={s.table}")
            bits.append(f"checks={len(s.expectations) or len(s.columns)}")
            bits.append("wired" if s.wired else "unwired")
            console.print(f"  {s.name}  {'  '.join(bits)}")
            for c in s.columns[:6]:
                console.print(f"      column: {c}")

    covered = model.covered_tables()
    if covered:
        console.print("\n[bold]Coverage[/bold]")
        for t in sorted(covered):
            engines = sorted({s.engine for s in model.suites if s.table.lower() == t})
            console.print(f"  {t}  engines={','.join(engines)}")

    if model.gates:
        console.print("\n[bold]Gates[/bold]")
        for g in model.gates:
            refs = f" refs={','.join(g.suite_refs)}" if g.suite_refs else ""
            console.print(f"  {g.kind}  {g.engine}  {g.name}  ({g.file}){refs}")

    if model.observed_runs:
        console.print("\n[bold]Observed runs[/bold]")
        for r in model.observed_runs:
            ok = "ok" if r.success else ("failed" if r.success is False else "?")
            console.print(f"  {r.engine}  {r.suite}  {ok}  ({r.file})")

    if model.unparsed:
        console.print("\n[bold]Unparsed[/bold]")
        for f in model.unparsed:
            console.print(f"  {f}")
    console.print()
