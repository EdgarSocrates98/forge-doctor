"""`forge-doctor-data lab` - Forge Lab scenario runner (ground truth vs engine)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _stderr
from forge_doctor_data.core.lab import (
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


def _pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else "-"


@lab_app.command(name="metrics")
def lab_metrics(labs: _LabsOpt = Path("labs"), as_json: _JsonOpt = False) -> None:
    """Precision/recall/FP rates and coverage per domain + total."""
    from forge_doctor_data.core.metrics import compute_metrics, forbidden_declarations

    root = labs if labs != Path("labs") else _default_labs()
    report = run_lab(root)
    rows = compute_metrics(report, forbidden_declarations(report))
    if as_json:
        typer.echo(
            json.dumps(
                {
                    r.name: {
                        "scenarios": r.scenarios,
                        "expected_findings": r.expected_findings,
                        "detected_correct": r.detected_correct,
                        "missed": r.missed,
                        "fp_candidates": r.fp_candidates,
                        "forbidden_hits": r.forbidden_hits,
                        "precision": r.precision,
                        "recall": r.recall,
                        "fpr": r.fpr,
                        "parser_coverage": r.parser_coverage,
                        "graph_recall": r.graph_recall,
                        "capability_accuracy": r.capability_accuracy,
                        "root_cause_recall": r.root_cause_recall,
                    }
                    for r in rows
                },
                indent=2,
            )
        )
        return
    console = Console()
    console.print()
    console.print(f"[bold]Forge Lab metrics[/bold]  {root}")
    for r in rows:
        console.print(f"\n  [bold]{r.name}[/bold]  ({r.scenarios} scenario(s))")
        console.print(
            f"    expected={r.expected_findings} detected={r.detected_correct} "
            f"missed={r.missed} fp_candidates={r.fp_candidates} "
            f"forbidden={r.forbidden_hits}/{r.forbidden_declared}"
        )
        console.print(
            f"    precision={_pct(r.precision)} recall={_pct(r.recall)} "
            f"fpr={_pct(r.fpr)} parser_cov={_pct(r.parser_coverage)}"
        )
        if r.expected_edges or r.expected_caps or r.expected_causes:
            console.print(
                f"    graph_recall={_pct(r.graph_recall)} "
                f"capability_acc={_pct(r.capability_accuracy)} "
                f"root_cause_recall={_pct(r.root_cause_recall)}"
            )
    console.print()


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


@lab_app.command(name="experiment")
def lab_experiment(
    scenario: Annotated[str, typer.Argument(help="Scenario name or fixture directory.")],
    hypothesis: Annotated[
        str | None, typer.Option("--hypothesis", help="Named transform to apply.")
    ] = None,
    labs: _LabsOpt = Path("labs"),
    before: Annotated[
        Path | None,
        typer.Option("--before", help="Baseline artifact bundle dir."),
    ] = None,
    after: Annotated[
        Path | None,
        typer.Option("--after", help="Changed artifact bundle dir."),
    ] = None,
    expect: Annotated[
        list[str] | None,
        typer.Option("--expect", help="Expected effect, e.g. 'scan_bytes:decrease:0.5'."),
    ] = None,
    protect: Annotated[
        list[str] | None,
        typer.Option("--protect", help="Protected constraint, e.g. 'freshness_seconds:<=:60'."),
    ] = None,
    as_json: _JsonOpt = False,
) -> None:
    """Apply a named hypothesis to a scenario copy and compare findings.

    With --before/--after, compares two exported artifact bundles on
    declared metrics instead (ExperimentPlan v2: verdicts SUPPORTED /
    NOT_SUPPORTED / INCONCLUSIVE / CONSTRAINT_VIOLATED).

    Never mutates the fixture: the scenario is copied to a temp dir,
    transformed, rescanned hermetically, and reported as
    improved | regressed | neutral with reasons.
    """
    if (before is not None) != (after is not None):
        _stderr.print("[red]--before and --after must be given together[/red]")
        raise typer.Exit(2)
    if before is not None and after is not None:
        _bundle_experiment(scenario, before, after, expect or [], protect or [], as_json)
        return
    if hypothesis is None:
        _stderr.print("[red]--hypothesis is required without --before/--after[/red]")
        raise typer.Exit(2)

    from forge_doctor_data.core.experiments import HYPOTHESES, run_experiment

    path = Path(scenario)
    if not path.is_dir():
        root = labs if labs != Path("labs") else _default_labs()
        matches = [d for d in discover_scenarios(root) if d.name == scenario]
        if not matches:
            _stderr.print(f"[red]unknown scenario[/red] {scenario} under {root}")
            raise typer.Exit(1)
        path = matches[0]
    try:
        result = run_experiment(path, hypothesis)
    except KeyError as exc:
        _stderr.print(f"[red]{exc.args[0]}[/red]")
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(json.dumps(result.to_dict(), indent=2))
        return
    console = Console()
    color = {"improved": "green", "regressed": "red"}.get(result.verdict, "yellow")
    console.print()
    console.print(
        f"[bold]Experiment[/bold]  {result.scenario} + {result.hypothesis} "
        f"-> [{color}]{result.verdict.upper()}[/{color}]"
    )
    for reason in result.reasons:
        console.print(f"  [dim]{reason}[/dim]")
    if result.changed_files:
        console.print(f"  changed: {', '.join(result.changed_files)}")
    for row in result.resolved_findings:
        console.print(f"  [green]resolved[/green] {row['check_id']} {row.get('file') or ''}")
    for row in result.introduced_findings:
        console.print(f"  [red]introduced[/red] {row['check_id']} {row.get('file') or ''}")
    known = ", ".join(sorted(HYPOTHESES))
    console.print(f"  [dim]hypotheses: {known}[/dim]")


def _bundle_experiment(
    name: str,
    before: Path,
    after: Path,
    expect: list[str],
    protect: list[str],
    as_json: bool,
) -> None:
    """Compare two evidence bundles against declared expectations."""
    from forge_doctor_data.core.experiments_v2 import (
        ExperimentPlanV2,
        compare_bundles,
    )

    plan = ExperimentPlanV2(
        hypothesis=name,
        expected_effects=tuple(expect),
        protected_constraints=tuple(protect),
    )
    report = compare_bundles(plan, before, after)
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
        return
    console = Console()
    color = {
        "supported": "green",
        "constraint_violated": "red",
        "not_supported": "red",
    }.get(report.verdict.value, "yellow")
    console.print()
    console.print(
        f"[bold]Experiment[/bold]  {name} -> [{color}]{report.verdict.value.upper()}[/{color}]"
    )
    for c in report.comparisons:
        mark = "ok" if c.direction_ok else ("n/a" if c.direction_ok is None else "MISS")
        tag = "green" if c.direction_ok else "dim"
        console.print(f"  [{tag}]{c.metric}[/{tag}] before={c.before} after={c.after} {mark}")
    for r in report.constraint_results:
        console.print(f"  constraint {r['spec']}: {r['status']} observed={r.get('observed')}")
    for reason in report.reasons:
        console.print(f"  [dim]{reason}[/dim]")
    console.print()
