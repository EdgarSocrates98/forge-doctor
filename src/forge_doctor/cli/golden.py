"""`forge-doctor golden` - full-output snapshot regression suites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr
from forge_doctor.core.golden import (
    GOLDEN_ROOT,
    discover_golden,
    run_all_golden,
    update_golden,
)

golden_app = typer.Typer(name="golden", help="Golden repositories - snapshot regression suites.")
app.add_typer(golden_app, name="golden")

_RootOpt = Annotated[Path, typer.Option("--root", help="Golden corpus root.")]
_JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


@golden_app.command(name="list")
def golden_list(root: _RootOpt = Path(GOLDEN_ROOT)) -> None:
    """List golden repositories."""
    repos = discover_golden(root)
    if not repos:
        _stderr.print(f"[yellow]no golden repos under[/yellow] {root}")
        raise typer.Exit(1)
    console = Console()
    console.print(f"[bold]Golden repos[/bold]  {root}")
    for r in repos:
        snap = "snapshot" if (r.parent / "expected").is_dir() else "no snapshot"
        console.print(f"  {r.parent.name}  [dim]{snap}[/dim]")


@golden_app.command(name="run")
def golden_run(
    name: Annotated[str | None, typer.Argument(help="Repo name (default: all).")] = None,
    root: _RootOpt = Path(GOLDEN_ROOT),
    as_json: _JsonOpt = False,
) -> None:
    """Diff current engine output against stored snapshots."""
    reports = run_all_golden(root, name)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    r.name: {
                        "passed": r.passed,
                        "missing_snapshot": r.missing_snapshot,
                        "errors": r.errors,
                        "diffs": [
                            {
                                "artifact": d.artifact,
                                "added": list(d.added),
                                "removed": list(d.removed),
                            }
                            for d in r.diffs
                        ],
                    }
                    for r in reports
                },
                indent=2,
            )
        )
    else:
        console = Console()
        console.print()
        console.print(f"[bold]Golden repos[/bold]  {root}")
        for r in reports:
            status = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
            console.print(f"  {status} {r.name}")
            for m in r.missing_snapshot:
                console.print(f"      [yellow]no snapshot:[/yellow] {m}")
            for e in r.errors:
                console.print(f"      [red]error:[/red] {e}")
            for d in r.diffs:
                console.print(f"      [red]{d.artifact}:[/red] +{len(d.added)} -{len(d.removed)}")
                for row in d.added[:3]:
                    console.print(f"        + {row}")
                for row in d.removed[:3]:
                    console.print(f"        - {row}")
        passed = sum(1 for r in reports if r.passed)
        console.print(f"\n  {passed} passed, {len(reports) - passed} failed")
    if not reports or any(not r.passed for r in reports):
        raise typer.Exit(1)


@golden_app.command(name="update")
def golden_update(
    name: Annotated[str | None, typer.Argument(help="Repo name (default: all).")] = None,
    root: _RootOpt = Path(GOLDEN_ROOT),
) -> None:
    """Regenerate snapshots — review the diff before committing."""
    repos = discover_golden(root)
    updated = 0
    for repo_dir in repos:
        if name and repo_dir.parent.name != name:
            continue
        update_golden(repo_dir)
        updated += 1
    console = Console()
    console.print(f"[bold]golden update[/bold] — regenerated {updated} snapshot(s)")
    if not updated:
        _stderr.print(f"[yellow]no golden repos under[/yellow] {root}")
        raise typer.Exit(1)
