"""`forge-doctor redshift` - vendor model inspection (spec 215)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.analyzers.redshift_model import redshift_model
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext

redshift_app = typer.Typer(name="redshift", help="Redshift intelligence: inspect the vendor model.")
app.add_typer(redshift_app, name="redshift")


@redshift_app.command(name="inspect")
def redshift_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print the Redshift model: compute, relations, WLM, exports."""
    ctx = ProjectContext(root=path.resolve())
    model = redshift_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Redshift environment[/bold]")
    if not model.has_evidence:
        console.print("  no Redshift evidence detected")
        return

    if model.compute:
        console.print("\n[bold]Compute[/bold]")
        for c in model.compute:
            node = c.attr("node_type") or c.attr("base_capacity") or "-"
            public = c.attr("publicly_accessible") or "-"
            enc = c.attr("encrypted") or "-"
            console.print(
                f"  {c.kind} {c.name}  node={node} public={public} encrypted={enc} ({c.source})"
            )
    if model.namespaces:
        console.print("\n[bold]Namespaces[/bold]")
        for ns in model.namespaces:
            console.print(f"  {ns.kind} {ns.name} ({ns.source})")
    if model.tables or model.views:
        console.print("\n[bold]Relations[/bold]")
        for t in model.tables:
            tag = "external" if t.kind == "external_table" else "table"
            dist = t.attr("diststyle") or ("KEY " + t.attr("distkey") if t.attr("distkey") else "-")
            sort = t.attr("sortkey") or "-"
            console.print(f"  {tag} {t.name}  dist={dist} sortkey={sort} ({t.source})")
        for v in model.views:
            tag = "materialized view" if v.kind == "materialized_view" else "view"
            console.print(f"  {tag} {v.name} ({v.source})")
    if model.objects:
        console.print("\n[bold]Vendor objects[/bold]")
        for kind, count in sorted(Counter(o.kind for o in model.objects).items()):
            names = ", ".join(o.name for o in model.by_kind(kind))
            console.print(f"  {kind} x{count}: {names}")
    if model.queries:
        console.print("\n[bold]Queries/maintenance[/bold]")
        for kind, count in sorted(Counter(q.kind for q in model.queries).items()):
            console.print(f"  {kind}: {count}")
    if model.observed:
        console.print("\n[bold]Observed exports[/bold]")
        for shape, count in sorted(Counter(r.shape for r in model.observed).items()):
            console.print(f"  {shape}: {count} row(s)")
    if model.unparsed:
        console.print("\n[bold]Unparsed exports[/bold]")
        for f in model.unparsed:
            console.print(f"  {f}")
    console.print()
