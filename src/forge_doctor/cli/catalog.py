"""`forge-doctor catalog` - declared metadata estate inspection (spec 221)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.analyzers.metadata_model import metadata_model
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext

catalog_app = typer.Typer(
    name="catalog",
    help="Metadata catalogs: DataHub/OpenMetadata/Glue/Unity declared estate.",
)
app.add_typer(catalog_app, name="catalog")


@catalog_app.command(name="inspect")
def catalog_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print the declared catalog: datasets, owners, lineage, recipes."""
    ctx = ProjectContext(root=path.resolve())
    model = metadata_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]metadata estate[/bold]")
    if not model.has_evidence:
        console.print("  no metadata-catalog evidence detected")
        return

    if model.datasets:
        console.print("\n[bold]Cataloged datasets[/bold]")
        for d in model.datasets:
            bits = [d.vendor]
            if d.platform:
                bits.append(f"platform={d.platform}")
            if d.environment:
                bits.append(f"env={d.environment}")
            bits.append(f"owners={len(d.owners)}")
            bits.append(f"tags={len(d.tags)}")
            if d.upstreams:
                bits.append(f"upstreams={len(d.upstreams)}")
            console.print(f"  {d.qualified or d.urn}  {'  '.join(bits)}")
    if model.recipes:
        console.print("\n[bold]Ingestion recipes[/bold]  (connector types only)")
        for r in model.recipes:
            console.print(f"  {r.file}  source={r.source_type}  sink={r.sink_type or '-'}")
    if model.unparsed:
        console.print("\n[bold]Unparsed[/bold]")
        for f in model.unparsed:
            console.print(f"  {f}")
    console.print()
