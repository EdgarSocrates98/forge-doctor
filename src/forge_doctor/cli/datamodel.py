"""`forge-doctor data-model` - cross-domain access-style inspection.

Reports the *shape* of observed access operations (key lookups, bounded
queries, scans, multi-hop traversals) as percentages. Facts only - the
advisor never prescribes a platform.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr
from forge_doctor.core.context import ProjectContext

datamodel_app = typer.Typer(name="data-model", help="Cross-domain access-style inspection.")
app.add_typer(datamodel_app, name="data-model")


def _style_counts(ctx: ProjectContext) -> dict[str, int]:
    """Access-style histogram over all detected data-plane operations."""
    from forge_doctor.analyzers.dynamodb_model import dynamodb_model
    from forge_doctor.analyzers.graph_model import graph_model
    from forge_doctor.analyzers.sql_ast import analyze_sql

    counts: dict[str, int] = {}
    for a in dynamodb_model(ctx).accesses:
        style = {
            "get_item": "key lookup",
            "batch_get_item": "key lookup",
            "query": "bounded query",
            "scan": "scan",
        }.get(a.op, "write")
        if a.op in {"put_item", "update_item", "delete_item"}:
            style = "keyed write"
        counts[style] = counts.get(style, 0) + 1
    for q in graph_model(ctx).traversals:
        if not q.parsed:
            continue
        style = (
            "multi-hop traversal"
            if q.hop_count >= 2
            else ("graph write" if q.writes else "single-hop traversal")
        )
        counts[style] = counts.get(style, 0) + 1
    for stmt in analyze_sql(ctx).statements:
        style = (
            "relational write"
            if stmt.kind in {"INSERT", "UPDATE", "DELETE"}
            else "relational query"
        )
        counts[style] = counts.get(style, 0) + 1
    return counts


@datamodel_app.command(name="inspect")
def datamodel_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Breakdown of observed access styles across data domains."""
    ctx = ProjectContext(root=path.resolve())
    counts = _style_counts(ctx)
    console = Console()
    console.print()
    console.print("[bold]Access styles[/bold]")
    total = sum(counts.values())
    if total == 0:
        console.print("  no data access operations detected")
        return
    for style, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        console.print(f"  {style:<22} {count:>4}  {100.0 * count / total:5.1f}%")
    multi = counts.get("multi-hop traversal", 0)
    if multi:
        console.print()
        console.print(
            f"  graph-oriented access pattern detected: {multi} multi-hop "
            "traversal(s) are first-class in this project - worth "
            "evaluating whether the backing store serves this shape."
        )
    console.print("  [dim]facts only - no platform recommendation[/dim]")
    console.print()


@datamodel_app.callback(invoke_without_command=True)
def _datamodel_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor data-model inspect`")
        raise typer.Exit(2)
