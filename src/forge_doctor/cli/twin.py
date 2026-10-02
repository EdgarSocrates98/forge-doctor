"""``forge-doctor twin`` - the formal digital twin: validated platform snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.cli.app import app
from forge_doctor.cli.common import PathArg, _build_registry, _stderr
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.runner import CheckRunner
from forge_doctor.core.twin import Twin, build_twin, twin_snapshot

if TYPE_CHECKING:
    from forge_doctor.core.twin_states import TwinFact

twin_app = typer.Typer(name="twin", help="Formal digital twin: validated platform snapshot.")
app.add_typer(twin_app, name="twin")


def _twin_at(path: Path) -> tuple[ProjectContext, Twin]:
    """Build graph + scan report, assemble the twin."""
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    ctx = ProjectContext(root=path.resolve())
    graph = build_platform_graph(ctx)
    registry, _ = _build_registry(config=ctx.config)
    report = CheckRunner(registry).run(ctx)
    return ctx, build_twin(graph, report, ctx.root)


@twin_app.command(name="inspect")
def twin_inspect(
    path: PathArg = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Twin summary + invariant report. Exit 1 on hard violations."""
    _, twin = _twin_at(path)
    rep = twin.report
    if fmt == "json":
        typer.echo(json.dumps(rep.to_dict(), indent=2, sort_keys=True))
    else:
        console = Console()
        console.print()
        console.print("[bold]Digital twin[/bold]")
        console.print(
            f"  {rep.entity_count} entities, {rep.relationship_count} relationships, "
            f"{twin.scan_findings} findings"
        )
        if rep.entities_by_kind:
            table = Table("kind", "entities", title="by kind", title_justify="left")
            for kind, count in rep.entities_by_kind:
                table.add_row(kind, str(count))
            console.print(table)
        if rep.entities_by_domain:
            table = Table("domain", "entities", title="by domain", title_justify="left")
            for domain, count in rep.entities_by_domain:
                table.add_row(domain, str(count))
            console.print(table)
        if rep.violations:
            console.print(f"[red]{len(rep.violations)} invariant violation(s)[/red]")
            for v in rep.violations:
                console.print(f"  [red]{v.invariant}[/red] {v.detail}")
        else:
            console.print("[green]invariants hold[/green]")
        if rep.attr_gaps:
            table = Table(
                "domain",
                "missing name",
                "missing file",
                "missing line",
                title="attr gaps (informational)",
                title_justify="left",
            )
            for g in rep.attr_gaps:
                table.add_row(
                    g.domain,
                    str(g.missing_name),
                    str(g.missing_file),
                    str(g.missing_line),
                )
            console.print(table)
        console.print()
    if not rep.ok:
        raise typer.Exit(1)


@twin_app.command(name="export")
def twin_export(
    path: PathArg = Path("."),
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write to file (default: stdout).")
    ] = None,
) -> None:
    """Emit the deterministic twin snapshot artifact."""
    _, twin = _twin_at(path)
    text = json.dumps(twin_snapshot(twin), indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(text)
    if not twin.report.ok:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Five-state twin (spec 232)
# ---------------------------------------------------------------------------


def _facts_at(path: Path) -> tuple[ProjectContext, Twin, tuple[TwinFact, ...]]:
    from forge_doctor.core.twin_states import collect_twin_facts

    ctx, twin = _twin_at(path)
    contract = ctx.contract
    facts = collect_twin_facts(twin.graph, contract)
    return ctx, twin, facts


@twin_app.command(name="facts")
def twin_facts(
    path: PathArg = Path("."),
    state: Annotated[
        str | None,
        typer.Option("--state", help="Filter: desired|declared|implemented|observed|hypothetical."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List state-tagged facts the twin collected."""
    _, _, facts = _facts_at(path)
    if state:
        facts = tuple(f for f in facts if f.state.value == state)
    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "entity": f.entity,
                        "property": f.property,
                        "value": f.value,
                        "state": f.state.value,
                        "source": f.source,
                        "confidence": f.confidence,
                    }
                    for f in facts
                ],
                indent=2,
            )
        )
        return
    console = Console()
    table = Table("entity", "property", "value", "state", "source", title_justify="left")
    for f in facts:
        table.add_row(f.entity, f.property, f.value, f.state.value, f.source)
    console.print(table)
    console.print(f"[dim]{len(facts)} facts[/dim]")


@twin_app.command(name="reconcile")
def twin_reconcile(
    path: PathArg = Path("."),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Reconcile the five states: every (entity, property) divergence."""
    from forge_doctor.core.twin_states import drift_summary, reconcile

    _, _, facts = _facts_at(path)
    recs = reconcile(facts)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "reconciliations": [
                        {
                            "entity": r.entity,
                            "property": r.property,
                            "states": [s.value for s in r.states_compared],
                            "expected": r.expected,
                            "actual": r.actual,
                            "drift_type": r.drift_type.value,
                            "confidence": r.confidence,
                        }
                        for r in recs
                    ],
                    "drift_summary": drift_summary(recs),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    console = Console()
    if not recs:
        console.print("[green]no state divergences[/green]")
        return
    table = Table(
        "entity", "property", "states", "expected", "actual", "drift", title_justify="left"
    )
    for r in recs:
        states = " vs ".join(s.value for s in r.states_compared)
        table.add_row(r.entity, r.property, states, r.expected, r.actual, r.drift_type.value)
    console.print(table)
    summary = drift_summary(recs)
    console.print("[dim]" + ", ".join(f"{k}={v}" for k, v in summary.items()) + "[/dim]")


@twin_app.command(name="record")
def twin_record(
    path: PathArg = Path("."),
    name: Annotated[str | None, typer.Option("--name", help="Snapshot name.")] = None,
) -> None:
    """Persist a five-state twin snapshot into the project history dir."""
    import datetime

    from forge_doctor.core.twin_states import record_twin_snapshot, twin_state_snapshot

    ctx, twin, facts = _facts_at(path)
    ts = name or datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    snap = twin_state_snapshot(twin.graph, facts, ts)
    written = record_twin_snapshot(ctx.root, snap, ts)
    Console().print(f"[green]twin snapshot recorded:[/green] {written}")


@twin_app.command(name="diff")
def twin_diff(
    a: Annotated[str, typer.Argument(help="Older snapshot (path or recorded name).")],
    b: Annotated[str, typer.Argument(help="Newer snapshot (path or recorded name).")],
    path: PathArg = Path("."),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Diff two twin-state snapshots: entities, rels, capabilities, drift."""
    from forge_doctor.core.twin_states import diff_twin_snapshots, load_twin_snapshot

    root = path.resolve()
    try:
        snap_a = load_twin_snapshot(root, a)
        snap_b = load_twin_snapshot(root, b)
    except Exception as exc:
        _stderr.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    diff = diff_twin_snapshots(snap_a, snap_b)
    if as_json:
        typer.echo(json.dumps(diff.to_dict(), indent=2, sort_keys=True))
        return
    console = Console()
    console.print("[bold]Twin diff[/bold]")
    for label, rows in diff.to_dict().items():
        console.print(f"  [bold]{label}[/bold]: {len(rows)}")
        for row in rows:
            console.print(f"    {row}")


@twin_app.command(name="explain")
def twin_explain(
    entity: Annotated[str, typer.Argument(help="Entity key to explain across states.")],
    path: PathArg = Path("."),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show one entity across all five states."""
    from forge_doctor.core.twin_states import facts_for_entity

    _, _, facts = _facts_at(path)
    grouped = facts_for_entity(facts, entity)
    if not grouped:
        _stderr.print(f"[red]no twin facts for entity:[/red] {entity}")
        raise typer.Exit(1)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    state: {
                        prop: [
                            {"value": f.value, "source": f.source, "confidence": f.confidence}
                            for f in fs
                        ]
                        for prop, fs in props.items()
                    }
                    for state, props in grouped.items()
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    console = Console()
    console.print(f"[bold]{entity}[/bold] across states")
    for state, props in grouped.items():
        console.print(f"  [bold]{state}[/bold]")
        for prop, fs in props.items():
            for f in fs:
                console.print(f"    {prop} = {f.value}  [dim]({f.source})[/dim]")
