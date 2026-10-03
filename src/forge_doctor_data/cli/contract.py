"""``forge-doctor-data contract`` and ``forge-doctor-data architecture`` groups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.analyzers.runtime_evidence import ingest_artifact
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _stderr
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.contract import (
    ArchitectureDrift,
    PlatformContract,
    detect_drift,
    load_contract,
)

contract_app = typer.Typer(name="contract", help="Platform contract files.")
app.add_typer(contract_app, name="contract")

arch_app = typer.Typer(name="architecture", help="Architecture drift detection.")
app.add_typer(arch_app, name="architecture")

console = Console()


@contract_app.command(name="validate")
def contract_validate(
    contract: Annotated[Path, typer.Argument(help="platform-contract.yml path.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Validate a platform contract's structure and schema version."""
    c = load_contract(contract)
    if as_json:
        typer.echo(json.dumps(_contract_dict(c), indent=2))
        raise typer.Exit(0 if c.ok else 1)
    console.print()
    console.print(f"[bold]Contract[/bold] {contract}")
    console.print(f"  contract_version: {c.contract_version}")
    console.print(f"  pipelines: {len(c.pipelines)}")
    for p in c.pipelines:
        bits = [
            f"compute={p.compute_platform or '-'}@{p.compute_version or '-'}",
            f"storage={p.storage_format or '-'}",
            f"orch={p.orchestrator or '-'}",
        ]
        if p.sla_seconds is not None:
            bits.append(f"sla={p.sla_seconds / 60:g}m")
        if p.idempotent is not None:
            bits.append(f"idempotent={p.idempotent}")
        if p.owner:
            bits.append(f"owner={p.owner}")
        console.print(f"    {p.name}: {'; '.join(bits)}")
    if c.allowed_dependencies:
        console.print(f"  allowed_dependencies: {sorted(c.allowed_dependencies)}")
    if c.issues:
        for issue in c.issues:
            console.print(f"  [red]issue:[/red] {issue}")
    else:
        console.print("  [green]valid[/green]")
    raise typer.Exit(0 if c.ok else 1)


@arch_app.command(name="drift")
def architecture_drift(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
    contract: Annotated[
        Path | None, typer.Option("--contract", help="Contract file (default: auto-detect).")
    ] = None,
    runtime: Annotated[
        list[Path], typer.Option("--runtime", help="Exported runtime artifact(s).")
    ] = [],
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Compare the platform contract against code, IaC, and runtime."""
    ctx = ProjectContext(root=path.resolve())
    c = load_contract(contract) if contract else ctx.contract
    if c is None:
        _stderr.print("[yellow]no platform-contract.yml found[/yellow]")
        raise typer.Exit(0)
    if not c.ok:
        _stderr.print(f"[red]contract invalid:[/red] {c.issues[0]}")
        raise typer.Exit(2)
    models = [ingest_artifact(p) for p in runtime]
    drift = detect_drift(ctx, c, models)

    if as_json:
        typer.echo(json.dumps([_drift_dict(d) for d in drift], indent=2))
        return
    console.print()
    console.print("[bold]Architecture Drift[/bold]")
    if not drift:
        console.print("  no drift detected")
        return
    for d in drift:
        color = "red" if d.severity.value == "error" else "yellow"
        console.print(f"  [{color}]{d.check_id}[/{color}] {d.drift_type} on {d.entity}")
        console.print(f"      expected: {d.expected}")
        console.print(f"      observed: {d.observed}  ({d.source})")
        console.print(f"      {d.message}")
    console.print()


def _contract_dict(c: PlatformContract) -> dict[str, object]:
    return {
        "contract_version": c.contract_version,
        "path": c.path.as_posix(),
        "ok": c.ok,
        "issues": list(c.issues),
        "declared_platforms": sorted(c.declared_platforms),
        "allowed_dependencies": sorted(c.allowed_dependencies),
        "owners": list(c.owners),
        "pipelines": [
            {
                "name": p.name,
                "compute_platform": p.compute_platform,
                "compute_version": p.compute_version,
                "storage_format": p.storage_format,
                "orchestrator": p.orchestrator,
                "sla_seconds": p.sla_seconds,
                "idempotent": p.idempotent,
                "owner": p.owner,
                "approved_capabilities": list(p.approved_capabilities),
            }
            for p in c.pipelines
        ],
    }


def _drift_dict(d: ArchitectureDrift) -> dict[str, object]:
    return {
        "check_id": d.check_id,
        "drift_id": d.id,
        "drift_type": d.drift_type,
        "entity": d.entity,
        "expected": d.expected,
        "observed": d.observed,
        "source": d.source,
        "severity": d.severity.value,
        "evidence_kind": d.evidence_kind,
        "message": d.message,
    }
