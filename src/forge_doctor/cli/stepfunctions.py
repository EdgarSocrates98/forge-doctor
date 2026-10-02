"""`forge-doctor stepfunctions` - model-driven ASL inspection."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.analyzers.stepfunctions_model import (
    StepFunctionsModel,
    stepfunctions_model,
)
from forge_doctor.checks.stepfunctions import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr, render_findings
from forge_doctor.core.context import ProjectContext

sfn_app = typer.Typer(name="stepfunctions", help="Step Functions intelligence.")
app.add_typer(sfn_app, name="stepfunctions")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


def _model_at(path: Path) -> tuple[ProjectContext, StepFunctionsModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, stepfunctions_model(ctx)


@sfn_app.command(name="inspect")
def sfn_inspect(
    path: _PathOpt = Path("."),
) -> None:
    """Summarize Step Functions definitions from the semantic model."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Step Functions[/bold]")

    if not model.has_machines:
        console.print("  no state machines detected")
        return

    for m in model.machines:
        mtype = m.type.upper() or "unknown"
        loc = f"{m.file}:{m.line}" if m.line else f"{m.file}"
        qlang = m.query_language or "JSONPath"
        console.print(f"\n  [bold]{m.name}[/bold]  {mtype}  {qlang}  [dim]{loc}[/dim]")
        by_type = Counter(s.type for s in m.states)
        console.print("    states: " + ", ".join(f"{k}x{n}" for k, n in sorted(by_type.items())))
        integ = sorted({s.integration for s in m.states if s.integration})
        if integ:
            console.print(f"    integrations: {', '.join(integ)}")
        retries = sum(s.retry_count for s in m.states)
        catches = sum(s.catch_count for s in m.states)
        attempts = sum(s.retry_max_attempts for s in m.states)
        console.print(
            f"    retry blocks: {retries} (max_attempts={attempts})  catch blocks: {catches}"
        )
        maps = [s for s in m.states if s.map_mode]
        if maps:
            console.print(
                "    maps: "
                + ", ".join(
                    f"{s.name}[{s.map_mode} max_concurrency={s.max_concurrency or '-'} "
                    f"tolerated={s.tolerated_failure}%]"
                    for s in maps
                )
            )
        payload = sorted({k for s in m.states for k in s.payload_keys})
        if payload:
            console.print(f"    payload keys: {', '.join(payload)}")
        for n in m.nested:
            console.print(f"    nested {n.name}: {len(n.states)} state(s)")

    if model.iac_refs:
        console.print(f"\n[bold]IaC references[/bold]  {', '.join(model.iac_refs)}")

    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@sfn_app.callback(invoke_without_command=True)
def _sfn_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor stepfunctions inspect`")
        raise typer.Exit(2)
