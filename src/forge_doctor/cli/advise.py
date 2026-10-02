"""``forge-doctor advise`` - ranked, cited action list (decision intelligence)."""

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


@app.command(name="advise")
def advise_cmd(
    path: PathArg = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
    top: Annotated[int | None, typer.Option("--top", help="Show only the top N.")] = None,
) -> None:
    """Rank findings into a cited action list.

    Score = severity + confidence + cluster + fix-safety + plan + policy
    + blast radius; every row cites fingerprints and entity ids.
    Advisory only - apply via ``fix``/``remediate`` commands.
    """
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.decisions import advise
    from forge_doctor.core.diagnosis import cluster_findings
    from forge_doctor.core.fixes import plan_fixes
    from forge_doctor.core.remediation import plan_remediation

    ctx = ProjectContext(root=path.resolve())
    registry, _ = _build_registry(config=ctx.config)
    report = CheckRunner(registry).run(ctx)
    graph = build_platform_graph(ctx)
    clusters = cluster_findings(report.results, [])
    plans = plan_remediation(report.results, clusters)
    fixes = plan_fixes(report.results, registry.select(), ctx)

    rows = advise(report.results, clusters, plans, fixes, graph)
    if top is not None:
        rows = rows[:top]

    if fmt == "json":
        typer.echo(json.dumps([a.to_dict() for a in rows], indent=2, sort_keys=True))
        return

    console = Console()
    console.print()
    console.print("[bold]Advised actions[/bold] [dim](ranked; score breakdown shown)[/dim]")
    if not rows:
        console.print("  no actionable findings")
        return
    table = Table("score", "check", "action", "fix", "cites")
    for a in rows:
        cites = f"{a.count} finding(s)"
        if a.entities:
            cites += f", {len(a.entities)} entit{'y' if len(a.entities) == 1 else 'ies'}"
        table.add_row(
            str(a.score),
            a.check_id,
            a.action[:80],
            a.fix_class or "-",
            cites,
        )
        for key, val in a.breakdown.items():
            table.add_row("", "", f"[dim]+{val} {key}[/dim]", "", "")
    console.print(table)
    console.print()
