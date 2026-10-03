"""`forge-doctor-data cloud` - vendor-neutral abstraction view (spec 223)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.analyzers.abstractions import abstractions_model
from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext

cloud_app = typer.Typer(
    name="cloud",
    help="Multi-cloud abstractions: vendor-neutral view over detected services.",
)
app.add_typer(cloud_app, name="cloud")


@cloud_app.command(name="inspect")
def cloud_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print the abstraction view: services by kind, cloud, parity gaps."""
    ctx = ProjectContext(root=path.resolve())
    model = abstractions_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]cloud abstractions[/bold]")
    if not model.has_evidence:
        console.print("  no cloud-platform evidence detected")
        return

    kind_clouds = model.kind_clouds()
    for kind in sorted(kind_clouds):
        console.print(f"\n[bold]{kind}[/bold]")
        for s in model.services:
            if s.abstraction != kind:
                continue
            bits = [s.cloud, s.service]
            if s.region:
                bits.append(f"region={s.region}")
            if s.encrypted:
                bits.append(f"encrypted={s.encrypted}")
            if s.public:
                bits.append(f"public={s.public}")
            if s.linked:
                bits.append("linked")
            console.print(f"  {s.name}  {'  '.join(bits)}")

    clouds = model.clouds() - {"vendor"}
    if len(clouds) > 1:
        console.print(f"\n[bold]clouds in estate:[/bold] {', '.join(sorted(clouds))}")

    if model.unmapped:
        console.print("\n[bold]Unmapped (no abstraction)[/bold]")
        for u in model.unmapped[:10]:
            console.print(f"  {u.domain}/{u.kind}  {u.name}")
    console.print()
