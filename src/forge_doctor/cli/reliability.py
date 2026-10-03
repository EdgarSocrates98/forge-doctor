"""``forge-doctor reliability`` — whole-path SLO & critical-path analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from forge_doctor.cli.app import app

if TYPE_CHECKING:
    from forge_doctor.core.execution_model import QueryExecution
    from forge_doctor.core.platform_graph import DataPlatformGraph

reliability_app = typer.Typer(
    name="reliability", help="End-to-end SLO & critical-path intelligence."
)
app.add_typer(reliability_app, name="reliability")


def _graph_and_executions(
    path: Path,
) -> tuple[DataPlatformGraph, list[QueryExecution]]:
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.context import ProjectContext

    ctx = ProjectContext(root=path.resolve())
    graph = build_platform_graph(ctx)
    executions = []
    runtime_dir = path / "runtime"
    if runtime_dir.is_dir():
        from forge_doctor.analyzers.execution_adapters import ingest_executions

        for artifact in sorted(runtime_dir.rglob("*")):
            if artifact.is_file():
                try:
                    _, exs = ingest_executions(artifact)
                except (OSError, ValueError):
                    continue
                executions.extend(exs)
    return graph, executions


@reliability_app.command(name="path")
def reliability_path(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
    as_json: Annotated[bool, typer.Option("--format", "-f")] = False,
) -> None:
    """Critical paths over data-flow edges with per-segment coverage."""
    from forge_doctor.core.critical_path import critical_paths

    graph, executions = _graph_and_executions(path)
    paths = critical_paths(graph, executions)
    if as_json:
        typer.echo(json.dumps([p.to_dict() for p in paths], indent=2))
        return
    console = Console()
    console.print()
    console.print(f"[bold]Critical paths[/bold]  paths={len(paths)}")
    for p in paths:
        console.print(f"  {p.source} -> {p.destination}  ({p.coverage()})")
        if p.total_latency is not None:
            console.print(f"    total latency: {p.total_latency:.0f}ms")
        if p.bottleneck:
            console.print(f"    bottleneck: {p.bottleneck}")
        for u in p.unknown_segments:
            console.print(f"    [dim]unknown segment: {u}[/dim]")
    if not paths:
        console.print("  no data-flow paths found")
    console.print()


@reliability_app.command(name="slo")
def reliability_slo(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
    as_json: Annotated[bool, typer.Option("--format", "-f")] = False,
) -> None:
    """SLO budgets + SLO001-006 findings over critical paths."""
    from forge_doctor.core.critical_path import (
        critical_paths,
        slo_budgets,
        slo_findings,
    )
    from forge_doctor.core.reliability import extract_objectives

    graph, executions = _graph_and_executions(path)
    paths = critical_paths(graph, executions)
    objectives = extract_objectives(graph)
    budgets = slo_budgets(objectives, paths)
    findings = slo_findings(paths, budgets, graph)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "budgets": [b.to_dict() for b in budgets],
                    "findings": [f.to_dict() for f in findings],
                },
                indent=2,
            )
        )
        return
    console = Console()
    console.print()
    console.print(
        f"[bold]SLO budgets[/bold]  objectives={len(objectives)} "
        f"budgets={len(budgets)} findings={len(findings)}"
    )
    for b in budgets:
        state = "exhausted" if b.exhausted else "within"
        consumed = f"{b.consumed:.0f}" if b.consumed is not None else "unknown"
        console.print(
            f"  {b.path}  [{state}] consumed={consumed} "
            f"budget={b.total_budget} ({b.known_segments} known / "
            f"{b.unknown_segments} unknown)"
        )
        for v in b.violating_segments[:3]:
            console.print(f"    top consumer: {v}")
    for f in findings:
        console.print(f"  [{f.severity}] {f.check_id} {f.message}")
    if not budgets and not findings:
        console.print("  no objectives or path evidence — nothing to budget")
    console.print()
