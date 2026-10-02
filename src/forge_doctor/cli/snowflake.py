"""`forge-doctor snowflake` - vendor model inspection (spec 213)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.analyzers.snowflake_model import snowflake_model
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext

snowflake_app = typer.Typer(
    name="snowflake", help="Snowflake intelligence: inspect the vendor model."
)
app.add_typer(snowflake_app, name="snowflake")


@snowflake_app.command(name="inspect")
def snowflake_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print the Snowflake model: warehouses, objects, exports, copies."""
    ctx = ProjectContext(root=path.resolve())
    model = snowflake_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Snowflake environment[/bold]")
    if not model.has_evidence:
        console.print("  no Snowflake evidence detected")
        return

    if model.warehouses:
        console.print("\n[bold]Warehouses[/bold]")
        for w in model.warehouses:
            size = w.attr("warehouse_size") or w.attr("size") or "-"
            suspend = w.attr("auto_suspend") or "-"
            resume = w.attr("auto_resume") or "-"
            console.print(
                f"  {w.name}  size={size} auto_suspend={suspend} auto_resume={resume} ({w.source})"
            )
    if model.namespaces:
        console.print("\n[bold]Namespaces[/bold]")
        for ns in model.namespaces:
            console.print(f"  {ns.kind} {ns.name} ({ns.source})")
    if model.tables or model.views:
        console.print("\n[bold]Relations[/bold]")
        for t in model.tables:
            tag = "external" if t.kind == "external_table" else "table"
            console.print(f"  {tag} {t.name} ({t.source})")
        for v in model.views:
            tag = "materialized view" if v.kind == "materialized_view" else "view"
            console.print(f"  {tag} {v.name} ({v.source})")
    if model.objects:
        console.print("\n[bold]Vendor objects[/bold]")
        for kind, count in sorted(Counter(o.kind for o in model.objects).items()):
            names = ", ".join(o.name for o in model.by_kind(kind))
            console.print(f"  {kind} x{count}: {names}")
    if model.copies:
        console.print("\n[bold]COPY INTO[/bold]")
        for c in model.copies:
            console.print(f"  -> {c.target or '?'} from {c.stage_ref or 'literal URI'}")
    if model.observed:
        console.print("\n[bold]Observed exports[/bold]")
        for shape, count in sorted(Counter(r.shape for r in model.observed).items()):
            console.print(f"  {shape}: {count} row(s)")
    if model.unparsed:
        console.print("\n[bold]Unparsed exports[/bold]")
        for f in model.unparsed:
            console.print(f"  {f}")
    console.print()
