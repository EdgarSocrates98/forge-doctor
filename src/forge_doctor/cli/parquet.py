"""`forge-doctor parquet` - model-driven Parquet inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.analyzers.parquet_model import ParquetProjectModel, parquet_model
from forge_doctor.checks.parquet import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr, render_findings
from forge_doctor.core.context import ProjectContext

parquet_app = typer.Typer(name="parquet", help="Parquet intelligence: inspect.")
app.add_typer(parquet_app, name="parquet")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def _model_at(path: Path) -> tuple[ProjectContext, ParquetProjectModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, parquet_model(ctx)


@parquet_app.command(name="inspect")
def parquet_inspect(
    path: _PathOpt = Path("."),
) -> None:
    """Summarize the project's Parquet surface from the semantic model."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Parquet surface[/bold]")

    if not model.has_parquet:
        console.print("  no Parquet evidence detected")
        return

    if model.file_count:
        console.print("\n[bold]Dataset[/bold]")
        console.print(f"  Files            {model.file_count}")
        console.print(f"  Total size       {_mb(model.total_bytes)}")
        console.print(f"  Median file size {_mb(model.median_bytes)}")
        console.print(f"  P95              {_mb(model.p95_bytes)}")

    if model.compression_values:
        console.print("\n[bold]Compression[/bold]")
        for v in sorted(model.compression_values):
            console.print(f"  {v}")

    if model.writers or model.readers:
        console.print("\n[bold]Code[/bold]")
        console.print(f"  writers: {len(model.writers)}  readers: {len(model.readers)}")

    render_findings(console, ctx, CHECKS, title="Potential risks")
    console.print()


@parquet_app.callback(invoke_without_command=True)
def _parquet_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor parquet inspect`")
        raise typer.Exit(2)
