"""`forge-doctor capabilities` - inspect the capability registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr
from forge_doctor.core.capabilities import capability_registry
from forge_doctor.core.context import ProjectContext

capabilities_app = typer.Typer(name="capabilities", help="Platform capability registry.")
app.add_typer(capabilities_app, name="capabilities")

_JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


@capabilities_app.command(name="list")
def capabilities_list(
    as_json: _JsonOpt = False,
    provenance: Annotated[
        bool,
        typer.Option("--provenance", help="Emit rows with provenance objects."),
    ] = False,
) -> None:
    """List every capability fact by platform with its headline status.

    ``--json`` emits ``{platform: {capability: status}}``; with
    ``--provenance`` each row becomes ``{"status", "provenance"}``
    carrying the deciding pack entry, matched when-clause, and source.
    """
    registry = capability_registry()
    console = Console()
    if as_json:
        if provenance:
            payload: dict[str, dict[str, object]] = {
                platform: {
                    cap: _provenance_row(registry.explain(platform, cap))
                    for cap in registry.capabilities_for(platform)
                }
                for platform in registry.platforms()
            }
        else:
            payload = {
                platform: {
                    cap: registry.explain(platform, cap).status.value
                    for cap in registry.capabilities_for(platform)
                }
                for platform in registry.platforms()
            }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    table = Table("platform", "capability", "status")
    for platform in registry.platforms():
        for cap in registry.capabilities_for(platform):
            table.add_row(platform, cap, registry.explain(platform, cap).status.value)
    console.print(table)
    for issue in registry.validation_issues:
        _stderr.print(f"[yellow]{issue}[/yellow]")


def _provenance_row(result: object) -> dict[str, object]:
    from forge_doctor.core.capabilities import CapabilityResult

    assert isinstance(result, CapabilityResult)
    return {
        "status": result.status.value,
        "provenance": {
            "entry_id": result.entry_id,
            "matched_when": [list(w) for w in result.matched_when],
            "missing_evidence": list(result.missing_evidence),
            "pack": result.pack,
            "pack_version": result.pack_version,
            "source": result.source,
            "verified_at": result.verified_at,
        },
    }


@capabilities_app.command(name="explain")
def capabilities_explain(
    platform: Annotated[str, typer.Argument(help="Platform key (dynamodb, neptune...).")],
    capability: Annotated[str, typer.Argument(help="Capability id (DYNAMODB_STREAMS...).")],
    version: Annotated[str | None, typer.Option("--version", help="Platform version.")] = None,
    variant: Annotated[
        str | None, typer.Option("--variant", help="Mode variant (MREC|MRSC...).")
    ] = None,
    attribute: Annotated[
        list[str] | None, typer.Option("--attr", help="Extra fact as key=value.")
    ] = None,
    as_json: _JsonOpt = False,
) -> None:
    """Evaluate one capability in context and show status + provenance."""
    registry = capability_registry()
    attrs: dict[str, str] = {}
    for pair in attribute or []:
        key, _, value = pair.partition("=")
        if value:
            attrs[key] = value
    result = registry.explain(platform, capability, version=version, variant=variant, **attrs)
    console = Console()
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "platform": result.platform,
                    "capability": result.capability,
                    "status": result.status.value,
                    "reason": result.reason,
                    "limitations": list(result.limitations),
                    "conditions": list(result.conditions),
                    "source": result.source,
                    "pack": result.pack,
                    "pack_version": result.pack_version,
                    "verified_at": result.verified_at,
                    "entry_id": result.entry_id,
                    "matched_when": [list(w) for w in result.matched_when],
                    "missing_evidence": list(result.missing_evidence),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    console.print(f"[bold]{result.platform}[/bold] {result.capability}")
    console.print(f"  status: {result.status.value}")
    if result.reason:
        console.print(f"  reason: {result.reason}")
    for cond in result.conditions:
        console.print(f"  unmet condition: {cond}")
    for lim in result.limitations:
        console.print(f"  limitation: {lim}")
    for miss in result.missing_evidence:
        console.print(f"  missing evidence: {miss}")
    if result.source:
        console.print(f"  source: {result.source}")
    if result.pack:
        console.print(f"  provenance: {result.pack} v{result.pack_version} ({result.verified_at})")


@capabilities_app.command(name="graph")
def capabilities_graph(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
    as_json: _JsonOpt = False,
) -> None:
    """Render the capability → evidence subgraph for a project.

    Capabilities observed in the project evaluate against the versions
    its entities declare; each edge carries the deciding pack entry,
    matched when-clause, and (for unknowns) the missing evidence.
    """
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.capability_graph import capability_subgraph

    graph = build_platform_graph(ProjectContext(root=path.resolve()))
    sub = capability_subgraph(graph)
    console = Console()
    if as_json:
        typer.echo(json.dumps(sub.to_dict(), indent=2, sort_keys=True))
        return
    console.print()
    console.print("[bold]Capability graph[/bold]")
    caps = sub.entities(kind=None)
    cap_entities = [e for e in caps if e.kind.value == "capability"]
    if not cap_entities:
        console.print("  no capability facts for the platforms in this project")
        return
    for ent in cap_entities:
        edges = [r for r in sub.relationships() if r.src == ent.id]
        pack_edge = next((r for r in edges if ":knowledge:" in r.dst), None)
        prov = f"  [dim]<- {pack_edge.dst}[/dim]" if pack_edge else ""
        console.print(f"  [bold]{ent.name}[/bold]  {ent.attr('status')}{prov}")
        for r in edges:
            if r is pack_edge:
                continue
            console.print(f"      [dim]version evidence: {r.dst}[/dim]")
    n_e = len(sub.entities())
    n_r = len(sub.relationships())
    console.print(f"\n  [dim]{n_e} nodes, {n_r} edges[/dim]")


@capabilities_app.callback(invoke_without_command=True)
def _capabilities_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor capabilities list`")
        raise typer.Exit(2)
