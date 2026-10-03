"""`forge-doctor-data platform` - canonical platform graph inspection."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.analyzers.platform_graph_builder import (
    build_platform_graph,
    impact_reachable,
)
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _stderr
from forge_doctor_data.core.context import ProjectContext

platform_app = typer.Typer(name="platform", help="Canonical platform graph.")
app.add_typer(platform_app, name="platform")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


@platform_app.command(name="graph")
def platform_graph(
    path: _PathOpt = Path("."),
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Entity/relationship census of the canonical platform graph."""
    ctx = ProjectContext(root=path.resolve())
    graph = build_platform_graph(ctx)
    if as_json:
        typer.echo(json.dumps(graph.to_dict(), indent=2))
        return

    console = Console()
    console.print()
    console.print("[bold]Platform Graph[/bold]")
    if not graph.entities():
        console.print("  no platform entities detected")
        return

    by_kind = Counter(e.kind.value for e in graph.entities())
    rel_kinds = Counter(r.kind.value for r in graph.relationships())
    console.print(f"\n  {len(graph.entities())} entities")
    for kind, count in sorted(by_kind.items()):
        console.print(f"    {kind:<24} {count}")
    console.print(f"\n  {len(graph.relationships())} relationships")
    for kind, count in sorted(rel_kinds.items()):
        console.print(f"    {kind:<24} {count}")
    console.print()


@platform_app.command(name="blast-radius")
def platform_blast_radius(
    query: Annotated[str, typer.Argument(help="Entity id or substring.")],
    path: _PathOpt = Path("."),
) -> None:
    """Entities impacted by a change to ``query`` (semantic direction)."""
    ctx = ProjectContext(root=path.resolve())
    graph = build_platform_graph(ctx)
    console = Console()
    console.print()

    matches = [e for e in graph.entities() if query in e.id or query == e.name]
    if not matches:
        console.print(f"  no entity matches {query!r}")
        raise typer.Exit(1)

    for entity in matches:
        console.print(f"[bold]{entity.id}[/bold]")
        reached = impact_reachable(graph, entity.id)
        if not reached:
            console.print("  (nothing downstream)")
        for rid in sorted(reached):
            target = graph.entity(rid)
            label = (target.name or target.identifier) if target else ""
            console.print(f"  -> {rid} [dim]{label}[/dim]")
        console.print()


@platform_app.command(name="findings")
def platform_findings(
    path: _PathOpt = Path("."),
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Cross-domain platform findings (PLAT### rules)."""
    from forge_doctor_data.checks.platform_rules import CHECKS
    from forge_doctor_data.cli.common import render_findings

    ctx = ProjectContext(root=path.resolve())
    results = [r for check in CHECKS for r in check.run(ctx)]
    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "id": r.check_id,
                        "severity": r.severity.value,
                        "message": r.message,
                        "file": r.file.as_posix() if r.file else None,
                        "line": r.line,
                        "confidence": r.confidence.value if r.confidence else None,
                        "evidence_kind": (r.evidence_kind.value if r.evidence_kind else None),
                    }
                    for r in results
                ],
                indent=2,
            )
        )
        return
    console = Console()
    render_findings(console, ctx, CHECKS, title="Platform Findings")


@platform_app.callback(invoke_without_command=True)
def _platform_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor-data platform graph|blast-radius`")
        raise typer.Exit(2)
