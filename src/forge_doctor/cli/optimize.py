"""``forge-doctor optimize`` - ranked optimization candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.cli.app import app
from forge_doctor.cli.common import PathArg, _build_registry
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.runner import CheckRunner


@app.command(name="optimize")
def optimize_cmd(
    path: PathArg = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
    top: Annotated[int | None, typer.Option("--top", help="Show only the top N.")] = None,
) -> None:
    """Enumerate optimization candidates the platform is eligible for.

    Each candidate cites its evidence, gives a static cost-proxy
    estimate, and prints the exact ``lab experiment`` / ``what-if``
    command that validates it. Suggests - never applies.
    """
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.optimize import optimize

    ctx = ProjectContext(root=path.resolve())
    registry, _ = _build_registry(config=ctx.config)
    report = CheckRunner(registry).run(ctx)
    graph = build_platform_graph(ctx)

    candidates = optimize(report, graph, ctx.root)
    if top is not None:
        candidates = candidates[:top]

    if fmt == "json":
        typer.echo(json.dumps([c.to_dict() for c in candidates], indent=2, sort_keys=True))
        return

    console = Console()
    console.print()
    console.print(
        "[bold]Optimization candidates[/bold] "
        "[dim](ranked; static cost proxy, not live estimates)[/dim]"
    )
    if not candidates:
        console.print("  no eligible optimizations found")
        return
    table = Table("conf", "optimization", "reason", "cost~", "entities", "validate")
    for c in candidates:
        table.add_row(
            c.confidence,
            c.optimization,
            c.reason[:80],
            str(c.cost_proxy),
            str(len(c.entities)),
            f"[dim]{c.validate_command}[/dim]",
        )
    console.print(table)
    console.print()
