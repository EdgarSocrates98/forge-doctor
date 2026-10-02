"""`forge-doctor dbt` - transformation-layer inspection (spec 216)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.analyzers.dbt_model import dbt_model
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext

dbt_app = typer.Typer(name="dbt", help="dbt intelligence: inspect the transformation model.")
app.add_typer(dbt_app, name="dbt")


@dbt_app.command(name="inspect")
def dbt_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print the dbt model: models, sources, tests, manifest coverage."""
    ctx = ProjectContext(root=path.resolve())
    model = dbt_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]dbt environment[/bold]")
    if not model.has_evidence:
        console.print("  no dbt evidence detected")
        return

    console.print(f"  project: {model.project_name or '?'}  profile: {model.profile or '-'}")
    if model.profile_names:
        resolved = (
            "resolves" if model.profile in model.profile_names else "not found in profiles.yml"
        )
        console.print(
            f"  profiles.yml: {', '.join(sorted(set(model.profile_names)))}  ({resolved})"
        )
    console.print(f"  model dirs: {', '.join(model.model_dirs)}")
    if model.models:
        console.print("\n[bold]Models[/bold]")
        for m in model.models:
            tests = f"{len(m.tests)} tests" if m.tests else "no tests"
            extra = f" unique_key={m.unique_key}" if m.unique_key else ""
            console.print(
                f"  {m.name}  materialized={m.materialized or '?'} "
                f"refs={len(m.refs)} sources={len(m.sources_used)} {tests}{extra}"
            )
    if model.sources:
        console.print("\n[bold]Sources[/bold]")
        for s in model.sources:
            fresh = "freshness" if s.has_freshness else "no-freshness"
            console.print(f"  {s.name}  {fresh}")
    if model.seeds or model.snapshots or model.singular_tests or model.exposures:
        console.print("\n[bold]Artifacts[/bold]")
        if model.seeds:
            console.print(f"  seeds: {', '.join(model.seeds)}")
        if model.snapshots:
            console.print(f"  snapshots: {', '.join(model.snapshots)}")
        if model.singular_tests:
            console.print(f"  singular tests: {', '.join(model.singular_tests)}")
        for e in model.exposures:
            console.print(f"  exposure: {e.name} ({e.type or '?'})")
    if model.macros_defined:
        console.print("\n[bold]Macros defined[/bold]")
        console.print(f"  {', '.join(model.macros_defined)}")
    if model.manifest_nodes or model.run_results:
        console.print("\n[bold]Observed[/bold]")
        if model.manifest_nodes:
            console.print(f"  manifest: {model.manifest_nodes} nodes")
        if model.run_results:
            failed = sum(1 for _, s, _ in model.run_results if s != "success")
            console.print(f"  run_results: {len(model.run_results)} rows, {failed} non-success")
    if model.unparsed:
        console.print("\n[bold]Unparsed[/bold]")
        for f in model.unparsed:
            console.print(f"  {f}")
    console.print()
