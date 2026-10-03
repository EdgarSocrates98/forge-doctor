"""``forge-doctor-data optimize`` - ranked optimization candidates + v2 opportunities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import PathArg, _build_registry, _stderr
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.runner import CheckRunner

optimize_app = typer.Typer(help="Optimization candidates + multi-objective opportunities.")


def _list_candidates(path: Path, fmt: str, top: int | None) -> None:
    from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor_data.core.optimize import optimize

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


def _v2_opportunities(path: Path, artifact: Path | None, adapter: str | None) -> list[Any]:
    """Assemble the v2 inputs: graph evidence + optional runtime artifact."""
    from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor_data.core.optimize_v2 import opportunities
    from forge_doctor_data.core.performance import (
        PerfPolicy,
        extract_signals,
        perf_findings,
    )
    from forge_doctor_data.core.physical_design import extract_designs
    from forge_doctor_data.core.reliability import (
        extract_objectives,
        extract_reliability,
    )

    ctx = ProjectContext(root=path.resolve())
    graph = build_platform_graph(ctx)
    designs = extract_designs(graph)
    models = extract_reliability(graph)
    objectives = extract_objectives(graph)
    signals: list[Any] = []
    perf: list[Any] = []
    drivers: list[Any] = []
    cost: list[Any] = []
    if artifact is not None:
        from forge_doctor_data.analyzers.execution_adapters import ingest_executions
        from forge_doctor_data.core.cost_drivers import (
            CostPolicy,
            cost_findings,
            detect_transfers,
            extract_drivers,
        )

        _, executions = ingest_executions(artifact, adapter=adapter)
        signals = extract_signals(executions)
        perf = perf_findings(executions, signals, PerfPolicy.defaults())
        drivers = extract_drivers(executions, graph)
        cost = cost_findings(
            drivers, detect_transfers(executions, graph), CostPolicy.defaults(), executions
        )
    registry, _ = _build_registry(config=ctx.config)
    return opportunities(
        signals=signals,
        perf=perf,
        drivers=drivers,
        cost=cost,
        models=models,
        objectives=objectives,
        designs=designs,
        graph=graph,
        registry=registry,
    )


@optimize_app.callback(invoke_without_command=True)
def _optimize_default(
    ctx: typer.Context,
    path: Annotated[Path, typer.Option("--path", help="Project directory to scan.")] = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
    top: Annotated[int | None, typer.Option("--top", help="Show only the top N.")] = None,
) -> None:
    """Enumerate optimization candidates (v1) when no subcommand given."""
    if ctx.invoked_subcommand is None:
        _list_candidates(path, fmt, top)


@optimize_app.command(name="inspect")
def optimize_inspect(
    path: PathArg = Path("."),
    artifact: Annotated[
        Path | None,
        typer.Option("--artifact", help="Exported runtime artifact for perf/cost evidence."),
    ] = None,
    adapter: Annotated[str | None, typer.Option("--adapter")] = None,
    fmt: Annotated[str, typer.Option("--format", "-f")] = "text",
) -> None:
    """Multi-objective opportunities with guardrails + tradeoffs."""
    opps = _v2_opportunities(path, artifact, adapter)
    if fmt == "json":
        typer.echo(json.dumps([o.to_dict() for o in opps], indent=2, sort_keys=True))
        return
    console = Console()
    console.print()
    console.print(f"[bold]Optimization opportunities[/bold] ({len(opps)})")
    if not opps:
        console.print("  no opportunities from available evidence")
        return
    for o in opps:
        console.print(
            f"  {o.id} [{o.guardrail_status.value}] {o.family.value} -> {o.target} "
            f"({o.objective.value}, {o.confidence.value})"
        )
        console.print(f"    expected: {'; '.join(o.expected_effects)}")
        console.print(f"    tradeoffs: {'; '.join(o.negative_tradeoffs)}")
        if o.protected_constraints:
            console.print(f"    protected: {'; '.join(o.protected_constraints)}")
        if o.unknowns:
            console.print(f"    [dim]unknowns: {'; '.join(o.unknowns)}[/dim]")
    console.print()


@optimize_app.command(name="explain")
def optimize_explain(
    opp_id: Annotated[str, typer.Argument(help="Opportunity id (OPP-####).")],
    path: PathArg = Path("."),
    artifact: Annotated[
        Path | None,
        typer.Option("--artifact", help="Exported runtime artifact for perf/cost evidence."),
    ] = None,
    adapter: Annotated[str | None, typer.Option("--adapter")] = None,
) -> None:
    """Full detail for one opportunity: effects, tradeoffs, guardrails."""
    opps = _v2_opportunities(path, artifact, adapter)
    match = [o for o in opps if o.id == opp_id.upper() or o.id == opp_id]
    if not match:
        _stderr.print(f"no opportunity {opp_id} — run `optimize inspect` to list")
        raise typer.Exit(1)
    typer.echo(json.dumps(match[0].to_dict(), indent=2, sort_keys=True))


app.add_typer(optimize_app, name="optimize")
