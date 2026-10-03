"""`forge-doctor-data trino` - federated-SQL cluster inspection (spec 218)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.analyzers.trino_model import trino_model
from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext

trino_app = typer.Typer(
    name="trino", help="Trino intelligence: inspect catalogs, coordinator, lineage."
)
app.add_typer(trino_app, name="trino")


@trino_app.command(name="inspect")
def trino_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print the Trino model: catalogs, coordinator flags, SQL refs."""
    ctx = ProjectContext(root=path.resolve())
    model = trino_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]trino environment[/bold]")
    if not model.has_evidence:
        console.print("  no trino evidence detected")
        return

    if model.catalogs:
        console.print("\n[bold]Catalogs[/bold]")
        for c in model.catalogs:
            console.print(f"  {c.name}  connector={c.connector}  ({len(c.props)} keys)")
    if model.coordinator_props or model.worker_props:
        console.print("\n[bold]Cluster[/bold]")
        role = "coordinator" if model.is_coordinator else "worker"
        console.print(f"  role: {role}  ({model.config_file})")
        console.print(f"  spill-to-disk: {'yes' if model.has_spill_config() else 'no'}")
        console.print(f"  resource groups: {'yes' if model.has_resource_groups() else 'no'}")
        if model.node_props:
            console.print(f"  node env: {model.node_props.get('node.environment', '-')}")
        if model.jvm_flags:
            console.print(f"  jvm flags: {len(model.jvm_flags)}")
    if model.refs:
        known = {c.name.lower() for c in model.catalogs}
        console.print("\n[bold]Three-part SQL refs[/bold]")
        seen: set[str] = set()
        for r in model.refs:
            mark = "" if r.catalog.lower() in known else "  [red]?[/red]"
            if r.name not in seen:
                seen.add(r.name)
                console.print(f"  {r.name}{mark}")
    if model.observed:
        console.print(f"\n[bold]Observed[/bold]  {len(model.observed)} cluster rows")
    if model.unparsed:
        console.print("\n[bold]Unparsed[/bold]")
        for f in model.unparsed:
            console.print(f"  {f}")
    console.print()
