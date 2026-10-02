"""`forge-doctor capabilities` - inspect the capability registry."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr
from forge_doctor.core.capabilities import capability_registry

capabilities_app = typer.Typer(name="capabilities", help="Platform capability registry.")
app.add_typer(capabilities_app, name="capabilities")

_JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


@capabilities_app.command(name="list")
def capabilities_list(as_json: _JsonOpt = False) -> None:
    """List every capability fact by platform with its headline status."""
    registry = capability_registry()
    console = Console()
    if as_json:
        payload = {
            platform: {
                cap: registry.explain(platform, cap).status.value
                for cap in registry.capabilities_for(platform)
            }
            for platform in registry.platforms()
        }
        console.print(json.dumps(payload, indent=2, sort_keys=True))
        return
    table = Table("platform", "capability", "status")
    for platform in registry.platforms():
        for cap in registry.capabilities_for(platform):
            table.add_row(platform, cap, registry.explain(platform, cap).status.value)
    console.print(table)
    for issue in registry.validation_issues:
        _stderr.print(f"[yellow]{issue}[/yellow]")


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
        console.print(
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
    if result.source:
        console.print(f"  source: {result.source}")
    if result.pack:
        console.print(f"  provenance: {result.pack} v{result.pack_version} ({result.verified_at})")


@capabilities_app.callback(invoke_without_command=True)
def _capabilities_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor capabilities list`")
        raise typer.Exit(2)
