"""``forge-doctor-data incident`` — explainable incident windows over history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.runtime import build_series_from_samples

if TYPE_CHECKING:
    from forge_doctor_data.core.change_correlation import ChangeEvent
    from forge_doctor_data.core.execution_history import ExecutionSeries
    from forge_doctor_data.core.platform_graph import DataPlatformGraph

incident_app = typer.Typer(
    name="incident", help="Dependency-aware incident intelligence (offline)."
)
app.add_typer(incident_app, name="incident")


def _load(
    root: Path, events: Path | None
) -> tuple[dict[str, ExecutionSeries], tuple[ChangeEvent, ...], DataPlatformGraph | None]:
    """Series + changes + graph for incident analysis."""
    from forge_doctor_data.core.execution_history import iter_samples

    series = build_series_from_samples(list(iter_samples(root)))
    changes: tuple[ChangeEvent, ...] = ()
    if events is not None:
        from forge_doctor_data.core.change_correlation import change_events_from_json

        changes = change_events_from_json(events)
    graph = None
    try:
        from forge_doctor_data.analyzers.platform_graph_builder import (
            build_platform_graph,
        )
        from forge_doctor_data.core.context import ProjectContext

        graph = build_platform_graph(ProjectContext(root=root.resolve()))
    except Exception:
        graph = None
    return series, changes, graph


@incident_app.command(name="inspect")
def incident_inspect(
    events: Annotated[
        Path | None,
        typer.Option("--events", help="JSON file of change events."),
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Project root holding .forge-doctor-data/.")
    ] = Path("."),
    window_minutes: Annotated[
        int, typer.Option("--window", help="Incident grouping/correlation window.")
    ] = 120,
    as_json: Annotated[bool, typer.Option("--format", "-f", help="json output")] = False,
) -> None:
    """Group co-occurring regression episodes into incident windows."""
    from forge_doctor_data.core.incident import build_incidents

    series, changes, graph = _load(root, events)
    incidents = build_incidents(series, changes, graph, window_ms=window_minutes * 60_000)
    if as_json:
        typer.echo(json.dumps([i.to_dict() for i in incidents], indent=2))
        return
    console = Console()
    console.print()
    console.print(
        f"[bold]Incidents[/bold]  series={len(series)} "
        f"changes={len(changes)} incidents={len(incidents)}"
    )
    for inc in incidents:
        console.print(
            f"  [bold]{inc.id}[/bold]  symptoms={len(inc.symptoms)} "
            f"candidates={len(inc.candidate_causes)}"
        )
        for s in inc.symptoms:
            console.print(f"    symptom: {s}")
        for c in inc.candidate_causes[:3]:
            console.print(
                f"    cause [{c.confidence.value}] {c.category.value} "
                f"on {c.entity} ({c.confirmed_links}/{c.expected_links} links)"
            )
        for u in inc.unknowns:
            console.print(f"    [dim]unknown: {u}[/dim]")
    if not incidents:
        console.print("  no regression episodes — nothing to explain")
    console.print()


@incident_app.command(name="explain")
def incident_explain(
    incident_id: Annotated[str, typer.Argument(help="Incident id from inspect.")],
    events: Annotated[
        Path | None,
        typer.Option("--events", help="JSON file of change events."),
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Project root holding .forge-doctor-data/.")
    ] = Path("."),
    window_minutes: Annotated[
        int, typer.Option("--window", help="Incident grouping/correlation window.")
    ] = 120,
) -> None:
    """Narrate one incident's evidence path, causes and unknowns."""
    from forge_doctor_data.core.incident import build_incidents

    series, changes, graph = _load(root, events)
    incidents = build_incidents(series, changes, graph, window_ms=window_minutes * 60_000)
    inc = next((i for i in incidents if i.id == incident_id), None)
    console = Console()
    if inc is None:
        console.print(f"[red]No incident {incident_id}[/red] — run `incident inspect`.")
        raise typer.Exit(1)

    console.print()
    console.print(f"[bold]{inc.id}[/bold]  {inc.start} -> {inc.end}")
    console.print("[bold]Symptoms[/bold]")
    for s in inc.symptoms:
        console.print(f"  - {s}")
    if inc.propagations:
        console.print("[bold]Propagation[/bold]")
        for p in inc.propagations:
            if p.path_found:
                console.print(f"  {p.upstream_entity} -> {p.downstream_symptom}")
                for hop in p.intermediate:
                    console.print(f"    [dim]{hop}[/dim]")
            else:
                console.print(
                    f"  {p.upstream_entity} -> {p.downstream_symptom} [dim](no directed path)[/dim]"
                )
    if inc.downstream_effects:
        console.print("[bold]Downstream effects[/bold]")
        for d in inc.downstream_effects:
            console.print(f"  -> {d}")
    console.print("[bold]Candidate causes[/bold]")
    for c in inc.candidate_causes:
        console.print(
            f"  [{c.confidence.value}] {c.category.value} on {c.entity} "
            f"— {c.confirmed_links}/{c.expected_links} expected links confirmed"
        )
        for link in c.evidence_path:
            console.print(f"    + {link}")
        for lim in c.limitations:
            console.print(f"    [dim]- {lim}[/dim]")
    if inc.owners:
        console.print(f"[bold]Routing[/bold]  owners: {', '.join(inc.owners)}")
    if inc.unknowns:
        console.print("[bold]Unknowns[/bold]")
        for u in inc.unknowns:
            console.print(f"  ? {u}")
    console.print()
