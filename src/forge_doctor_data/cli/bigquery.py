"""`forge-doctor-data bigquery` - vendor model inspection (spec 214)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.analyzers.bigquery_model import bigquery_model
from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext

bigquery_app = typer.Typer(name="bigquery", help="BigQuery intelligence: inspect the vendor model.")
app.add_typer(bigquery_app, name="bigquery")


@bigquery_app.command(name="inspect")
def bigquery_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print the BigQuery model: datasets, relations, slots, exports."""
    ctx = ProjectContext(root=path.resolve())
    model = bigquery_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]BigQuery environment[/bold]")
    if not model.has_evidence:
        console.print("  no BigQuery evidence detected")
        return

    if model.datasets:
        console.print("\n[bold]Datasets[/bold]")
        for ds in model.datasets:
            loc = ds.attr("location") or "-"
            console.print(f"  {ds.name}  location={loc} ({ds.source})")
    if model.tables or model.views:
        console.print("\n[bold]Relations[/bold]")
        for t in model.tables:
            tag = "external" if t.kind == "external_table" else "table"
            part = t.attr("partition_by") or t.attr("partitioning") or "-"
            clus = t.attr("cluster_by") or t.attr("clustering") or "-"
            console.print(f"  {tag} {t.name}  partition={part} cluster={clus} ({t.source})")
        for v in model.views:
            tag = "materialized view" if v.kind == "materialized_view" else "view"
            console.print(f"  {tag} {v.name} ({v.source})")
    if model.objects:
        console.print("\n[bold]Vendor objects[/bold]")
        for kind, count in sorted(Counter(o.kind for o in model.objects).items()):
            names = ", ".join(o.name for o in model.by_kind(kind))
            console.print(f"  {kind} x{count}: {names}")
    if model.queries:
        console.print("\n[bold]Queries[/bold]")
        authored = sum(1 for q in model.queries if q.source == "file")
        jobs = sum(1 for q in model.queries if q.source == "job")
        console.print(f"  {authored} authored, {jobs} from observed job history")
    if model.observed:
        console.print("\n[bold]Observed exports[/bold]")
        for shape, count in sorted(Counter(r.shape for r in model.observed).items()):
            console.print(f"  {shape}: {count} row(s)")
    if model.unparsed:
        console.print("\n[bold]Unparsed exports[/bold]")
        for f in model.unparsed:
            console.print(f"  {f}")
    console.print()
