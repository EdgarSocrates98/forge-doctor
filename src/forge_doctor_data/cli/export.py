"""``forge-doctor-data export`` - portable interop bundles for Forge tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import PathArg, _build_registry, _stderr
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.handoff import build_handoff_bundle
from forge_doctor_data.core.runner import CheckRunner


@app.command(name="export")
def export(
    path: PathArg = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="Bundle format: handoff")] = "handoff",
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write to file (default: stdout).")
    ] = None,
) -> None:
    """Emit a portable handoff bundle: findings + graph + capabilities + plans."""
    if fmt != "handoff":
        _stderr.print(f"[red]unknown format[/red] {fmt} (only 'handoff' for now)")
        raise typer.Exit(1)
    ctx = ProjectContext(root=path.resolve())
    registry, _ = _build_registry(config=ctx.config)
    report = CheckRunner(registry).run(ctx)
    bundle = build_handoff_bundle(report, ctx)
    text = json.dumps(bundle, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(text)
