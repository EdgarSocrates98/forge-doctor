"""`forge-doctor analytical` - real-time OLAP engine inspection (spec 219)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.analyzers.analytical_model import analytical_model
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext

analytical_app = typer.Typer(
    name="analytical",
    help="Real-time OLAP engines: ClickHouse / Pinot / Druid inspection.",
)
app.add_typer(analytical_app, name="analytical")


@analytical_app.command(name="inspect")
def analytical_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print the analytical-engine model: tables, engines, schemas, observed."""
    ctx = ProjectContext(root=path.resolve())
    model = analytical_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]analytical engines[/bold]")
    if not model.has_evidence:
        console.print("  no analytical-engine evidence detected")
        return

    for engine in ("clickhouse", "pinot", "druid"):
        tables = model.tables_of(engine)
        schemas = model.pinot_schemas if engine == "pinot" else []
        if not tables and not schemas:
            continue
        console.print(f"\n[bold]{engine}[/bold]")
        for t in tables:
            bits = []
            if t.table_engine:
                bits.append(f"engine={t.table_engine}")
            if t.table_type:
                bits.append(f"type={t.table_type}")
            if t.order_by:
                bits.append(f"order_by={t.order_by}")
            if t.partition_by:
                bits.append(f"partition_by={t.partition_by}")
            if t.kind != "table":
                bits.append(f"kind={t.kind}")
            console.print(f"  {t.name}  {'  '.join(bits) or '-'}")
        for s in schemas:
            console.print(f"  schema {s.name}  dims={len(s.dims)}  metrics={len(s.metrics)}")
    if model.keeper_files:
        console.print(f"\n[bold]Keeper config[/bold]  {len(model.keeper_files)} file(s)")
    if model.observed:
        by_engine: dict[str, int] = {}
        for r in model.observed:
            by_engine[r.engine] = by_engine.get(r.engine, 0) + 1
        console.print(
            "\n[bold]Observed[/bold]  "
            + ", ".join(f"{e}: {n} rows" for e, n in sorted(by_engine.items()))
        )
    if model.unparsed:
        console.print("\n[bold]Unparsed[/bold]")
        for f in model.unparsed:
            console.print(f"  {f}")
    console.print()
