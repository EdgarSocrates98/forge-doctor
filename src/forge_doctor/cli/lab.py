"""`forge-doctor lab` - Forge Lab scenario runner (ground truth vs engine)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr
from forge_doctor.core.lab import (
    LabReport,
    ScenarioReport,
    discover_scenarios,
    run_lab,
    run_scenario,
)

lab_app = typer.Typer(name="lab", help="Forge Lab - reproducible scenarios with ground truth.")
app.add_typer(lab_app, name="lab")

_LabsOpt = Annotated[Path, typer.Option("--labs", help="Labs root directory.")]
_JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


def _default_labs() -> Path:
    return Path.cwd() / "labs"


def _report_dict(report: LabReport) -> dict[str, object]:
    return {
        "root": report.root.as_posix(),
        "passed": report.passed,
        "failed": report.failed,
        "scenarios": [
            {
                "name": r.scenario,
                "path": r.path.as_posix(),
                "passed": r.passed,
                "errors": r.errors,
                "categories": {
                    name: {
                        "expected": cat.expected,
                        "actual": cat.actual,
                        "missed": cat.missed,
                        "forbidden_hit": cat.forbidden_hit,
                        "extra": cat.extra,
                    }
                    for name, cat in r.categories
                },
            }
            for r in report.reports
        ],
    }


def _print_scenario(console: Console, r: ScenarioReport) -> None:
    status = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
    console.print(f"  {status} {r.scenario} [dim]{r.path}[/dim]")
    for err in r.errors:
        console.print(f"      [red]error:[/red] {err}")
    for name, cat in r.categories:
        for m in cat.missed:
            console.print(f"      [red]missed {name}:[/red] {m}")
        for f in cat.forbidden_hit:
            console.print(f"      [red]forbidden {name} present:[/red] {f}")
        if cat.extra and name == "findings":
            console.print(f"      [dim]extra findings:[/dim] {', '.join(cat.extra)}")


@lab_app.command(name="list")
def lab_list(labs: _LabsOpt = Path("labs")) -> None:
    """List discovered scenarios."""
    root = labs if labs != Path("labs") else _default_labs()
    scenarios = discover_scenarios(root)
    if not scenarios:
        _stderr.print(f"[yellow]no scenarios under[/yellow] {root}")
        raise typer.Exit(1)
    console = Console()
    console.print(f"[bold]Forge Lab[/bold]  {root}")
    for s in scenarios:
        console.print(f"  {s.parent.name}/{s.name}")
    console.print(f"  {len(scenarios)} scenario(s)")


@lab_app.command(name="run")
def lab_run(
    scenario: Annotated[str | None, typer.Argument(help="Scenario name (default: all).")] = None,
    labs: _LabsOpt = Path("labs"),
    as_json: _JsonOpt = False,
) -> None:
    """Run scenario(s) and compare engine output to ground truth."""
    root = labs if labs != Path("labs") else _default_labs()
    # Allow `lab run <dir>` for ad-hoc scenarios outside labs/.
    direct = Path(scenario) if scenario and Path(scenario).is_dir() else None
    if direct is not None:
        report = LabReport(root=direct.parent, reports=[run_scenario(direct)])
    else:
        report = run_lab(root, scenario)
    if as_json:
        typer.echo(json.dumps(_report_dict(report), indent=2))
    else:
        console = Console()
        console.print()
        console.print(f"[bold]Forge Lab[/bold]  {report.root}")
        if not report.reports:
            console.print("  [dim]no scenarios matched[/dim]")
        for r in report.reports:
            _print_scenario(console, r)
        console.print(f"\n  {report.passed} passed, {report.failed} failed")
    if report.failed or not report.reports:
        raise typer.Exit(1)


@lab_app.command(name="report")
def lab_report(labs: _LabsOpt = Path("labs"), as_json: _JsonOpt = False) -> None:
    """Alias for `lab run` over every scenario (summary view)."""
    root = labs if labs != Path("labs") else _default_labs()
    report = run_lab(root)
    if as_json:
        typer.echo(json.dumps(_report_dict(report), indent=2))
        return
    console = Console()
    console.print()
    console.print(f"[bold]Forge Lab report[/bold]  {report.root}")
    for r in report.reports:
        status = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        misses = sum(len(c.missed) + len(c.forbidden_hit) for _, c in r.categories)
        detail = f" ({misses} mismatch(es))" if misses else ""
        console.print(f"  {status} {r.scenario}{detail}")
    console.print(f"\n  {report.passed} passed, {report.failed} failed")
    if report.failed:
        raise typer.Exit(1)
