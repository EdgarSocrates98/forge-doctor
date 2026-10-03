"""``forge-doctor-data history`` - snapshot series, diffs, trends."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor_data.api import SCHEMA_VERSION
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _stderr
from forge_doctor_data.core.history import (
    HistoryError,
    HistorySnapshot,
    diff_snapshots,
    list_snapshots,
    load_snapshot,
    resolve_snapshot,
    trend,
)
from forge_doctor_data.output.summary import INTERNAL_ERROR_EXIT

history_app = typer.Typer(name="history", help="Recorded scan snapshots: list, diff, trend.")
app.add_typer(history_app, name="history")

PathOpt = Annotated[Path, typer.Option("--path", help="Project root.")]


def _all(root: Path) -> tuple[list[Path], list[HistorySnapshot]]:
    snaps = list_snapshots(root)
    return snaps, [load_snapshot(p) for p in snaps]


@history_app.callback(invoke_without_command=True)
def history_default(
    ctx: typer.Context,
    path: PathOpt = Path("."),
) -> None:
    """List recorded snapshots (``scan --record``)."""
    if ctx.invoked_subcommand is not None:
        return
    snaps, loaded = _all(path.resolve())
    console = Console()
    if not loaded:
        console.print(
            "[dim]No history. Record snapshots with `forge-doctor-data scan . --record`.[/dim]"
        )
        return
    table = Table(title=f"History: {path.resolve()}", title_justify="left")
    table.add_column("#", style="dim")
    table.add_column("Snapshot", style="bold")
    table.add_column("Created (UTC)")
    table.add_column("Errors", justify="right")
    table.add_column("Warnings", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Entities", justify="right")
    for i, (p, snap) in enumerate(zip(snaps, loaded, strict=True)):
        table.add_row(
            str(i),
            p.stem,
            snap.created[:19].replace("T", " "),
            str(snap.summary.get("errors", 0)),
            str(snap.summary.get("warnings", 0)),
            str(snap.summary.get("total", 0)),
            str(len(snap.entity_ids)),
        )
    console.print(table)
    console.print("[dim]Diff: forge-doctor-data history diff <a> <b> (or --last)[/dim]")


@history_app.command(name="diff")
def history_diff(
    path: PathOpt = Path("."),
    a: Annotated[str | None, typer.Argument(help="Snapshot name or index.")] = None,
    b: Annotated[str | None, typer.Argument(help="Snapshot name or index.")] = None,
    last: Annotated[bool, typer.Option("--last", help="Diff the two latest snapshots.")] = False,
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Diff two snapshots: new/resolved findings, entity + capability + drift deltas."""
    import dataclasses
    import json as _json

    root = path.resolve()
    _, loaded = _all(root)
    if last:
        if len(loaded) < 2:
            _stderr.print("[red]Need ≥2 snapshots for --last.[/red]")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        older, newer = loaded[-2], loaded[-1]
    else:
        if a is None or b is None:
            _stderr.print("[red]Usage: history diff <a> <b> or --last[/red]")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        try:
            older = load_snapshot(resolve_snapshot(root, a))
            newer = load_snapshot(resolve_snapshot(root, b))
        except HistoryError as exc:
            _stderr.print(f"[red]{exc}[/red]")
            raise typer.Exit(INTERNAL_ERROR_EXIT) from None
    diff = diff_snapshots(older, newer)

    if fmt == "json":
        typer.echo(
            _json.dumps(
                {"schema_version": SCHEMA_VERSION, **dataclasses.asdict(diff)},
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console = Console()
    console.print(f"[bold]{diff.older}[/bold] -> [bold]{diff.newer}[/bold]")
    console.print(
        f"findings: [green]+{len(diff.new_findings)} new[/green] "
        f"[red]-{len(diff.resolved_findings)} resolved[/red]; "
        f"entities: +{len(diff.entities_added)} -{len(diff.entities_removed)}; "
        f"capabilities: {len(diff.capability_transitions)} transition(s); "
        f"drift: +{len(diff.drift_added)} -{len(diff.drift_resolved)}"
    )
    if diff.new_findings:
        table = Table(title="New findings", title_justify="left")
        table.add_column("Id")
        table.add_column("Severity")
        table.add_column("Location", style="dim")
        for f in diff.new_findings:
            table.add_row(str(f["check_id"]), str(f["severity"]), str(f.get("file") or "-"))
        console.print(table)
    if diff.resolved_findings:
        table = Table(title="Resolved findings", title_justify="left")
        table.add_column("Id")
        table.add_column("Severity")
        table.add_column("Location", style="dim")
        for f in diff.resolved_findings:
            table.add_row(str(f["check_id"]), str(f["severity"]), str(f.get("file") or "-"))
        console.print(table)
    if diff.entities_added or diff.entities_removed:
        console.print("[bold]Entities added:[/bold]")
        for eid in diff.entities_added:
            console.print(f"  + {eid}")
        console.print("[bold]Entities removed:[/bold]")
        for eid in diff.entities_removed:
            console.print(f"  - {eid}")
    if diff.capability_transitions:
        console.print("[bold]Capability transitions:[/bold]")
        for t in diff.capability_transitions:
            console.print(f"  {t['capability']}: {t['from']} -> {t['to']}")
    if diff.drift_added or diff.drift_resolved:
        console.print(
            f"[bold]Drift:[/bold] +{len(diff.drift_added)} new, "
            f"-{len(diff.drift_resolved)} resolved"
        )


@history_app.command(name="trend")
def history_trend(
    path: PathOpt = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Per-category finding counts + entity counts across the series."""
    import json as _json
    from collections import Counter

    _, loaded = _all(path.resolve())
    rows = trend(loaded)
    if not rows:
        Console().print("[dim]No snapshots recorded yet.[/dim]")
        return

    if fmt == "json":
        typer.echo(
            _json.dumps(
                {"schema_version": SCHEMA_VERSION, "series": rows},
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    # Cap category columns at the busiest 8 - wide tables are unreadable.
    totals = Counter[str]()
    for r in rows:
        totals.update(r["by_category"])
    categories = [c for c, _ in totals.most_common(8)]
    console = Console()
    table = Table(title="History trend", title_justify="left")
    table.add_column("Snapshot", style="bold")
    table.add_column("Err", justify="right")
    table.add_column("Warn", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Entities", justify="right")
    for cat in categories:
        table.add_column(cat, justify="right", style="dim")
    for row in rows:
        table.add_row(
            str(row["snapshot"]).replace(".json", ""),
            str(row["errors"]),
            str(row["warnings"]),
            str(row["total"]),
            str(row["entities"]),
            *[str(row["by_category"].get(c, 0)) for c in categories],
        )
    console.print(table)
    if len(rows) >= 2:
        delta = rows[-1]["total"] - rows[0]["total"]
        direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")
        console.print(
            f"[bold]Debt trajectory:[/bold] {direction} ({delta:+d} findings "
            f"over {len(rows)} snapshots)"
        )
