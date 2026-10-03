"""``forge-doctor-data remediate`` - deterministic remediation plans.

Describes what to change, where, why, and how to validate. Never edits
code, applies patches, runs Terraform, or deploys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _build_registry
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.diagnosis import cluster_findings
from forge_doctor_data.core.remediation import (
    RemediationPlan,
    plan_remediation,
    plan_to_dict,
)
from forge_doctor_data.core.runner import CheckRunner

console = Console()


@app.command(name="remediate")
def remediate(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
    root_cause: Annotated[
        str | None,
        typer.Option("--root-cause", help="Cluster id (prefix ok), e.g. RC_STREAM_COMMITS."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Print deterministic remediation plans for findings / root causes."""
    ctx = ProjectContext(root=path.resolve())
    registry, _ = _build_registry(config=ctx.config)
    report = CheckRunner(registry).run(ctx)
    clusters = cluster_findings(report.results, [])
    plans = plan_remediation(report.results, clusters, root_cause=root_cause)

    if as_json:
        typer.echo(json.dumps([plan_to_dict(p) for p in plans], indent=2))
        return

    console.print()
    console.print("[bold]Remediation Plans[/bold]")
    if not plans:
        console.print("  no remediation plans matched current findings")
        return
    for plan in plans:
        _print_plan(plan)
    console.print()
    console.print(
        "  [dim]plans describe what/where/why/how-to-validate only -"
        " nothing is applied automatically[/dim]"
    )


def _print_plan(plan: RemediationPlan) -> None:
    console.print(f"\n  [bold]{plan.id}[/bold] {plan.problem}")
    if plan.targets:
        console.print(f"    targets: {', '.join(plan.targets)}")
    if plan.prerequisites:
        for p in plan.prerequisites:
            console.print(f"    prerequisite: {p}")
    console.print("    actions:")
    for act in plan.actions:
        dep = f" (after {', '.join(act.depends_on)})" if act.depends_on else ""
        console.print(f"      {act.id}. {act.description}{dep}")
        console.print(f"         why: {act.rationale}")
        console.print(f"         effect: {act.expected_effect}")
        console.print(f"         validate: {act.validation}")
    if plan.dependencies:
        for d in plan.dependencies:
            console.print(f"    depends on: {d}")
    for r in plan.risks:
        console.print(f"    [yellow]risk:[/yellow] {r}")
    for v in plan.validation_steps:
        console.print(f"    validate: {v}")
    for rb in plan.rollback_notes:
        console.print(f"    rollback: {rb}")
