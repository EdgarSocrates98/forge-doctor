"""`forge-doctor-data iceberg` - model-driven Iceberg inspection commands."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor_data.analyzers.iceberg_model import (
    _MAINTENANCE_PROCS,
    IcebergProjectModel,
    iceberg_model,
)
from forge_doctor_data.checks.iceberg import CHECKS
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _stderr, render_findings
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.knowledge import load_pack

iceberg_app = typer.Typer(
    name="iceberg", help="Iceberg intelligence: inspect, maintenance, compatibility."
)
app.add_typer(iceberg_app, name="iceberg")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]

_MAINTENANCE_LABELS = {
    "expire_snapshots": "expire_snapshots",
    "rewrite_data_files": "rewrite_data_files",
    "rewrite_manifests": "rewrite_manifests",
    "remove_orphan_files": "remove_orphan_files",
    "rewrite_position_delete_files": "rewrite_position_delete_files",
}


def _model_at(path: Path) -> tuple[ProjectContext, IcebergProjectModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, iceberg_model(ctx)


def _operations_table(model: IcebergProjectModel) -> Table:
    table = Table(
        title="Detected operations", title_justify="left", show_edge=False, pad_edge=False
    )
    table.add_column("operation", style="bold", min_width=18)
    table.add_column("count", justify="right")
    for name, count in sorted(Counter(e.name for e in model.operations).items()):
        table.add_row(name, str(count))
    return table


def _maintenance_rows(model: IcebergProjectModel) -> list[tuple[str, str]]:
    found = model.maintenance_names
    rows = []
    for proc in _MAINTENANCE_PROCS:
        label = _MAINTENANCE_LABELS.get(proc, proc)
        where = ""
        if proc in found:
            hit = next(e for e in model.by_kind("maintenance") if e.name == proc)
            where = f"{hit.file.as_posix()}:{hit.line}"
        rows.append((label, f"detected ({where})" if where else "not detected"))
    return rows


@iceberg_app.command(name="inspect")
def iceberg_inspect(
    path: _PathOpt = Path("."),
) -> None:
    """Summarize the project's Iceberg surface from the semantic model."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Iceberg environment[/bold]")

    if not model.has_iceberg and not model.evidence:
        console.print("  no Iceberg evidence detected")
        return

    runtimes = model.runtimes
    if runtimes:
        rt = Table.grid(padding=(0, 2))
        rt.add_column(style="bold")
        rt.add_column()
        for name, e in sorted(runtimes.items()):
            rt.add_row(f"{name.capitalize()}:", e.value or "unset")
        console.print("\n[bold]Runtime[/bold]")
        console.print(rt)

    if model.catalog_names:
        console.print("\n[bold]Catalogs[/bold]")
        for name in sorted(model.catalog_names):
            console.print(f"  {name}")

    if model.operations:
        console.print()
        console.print(_operations_table(model))

    if model.has_writes or model.maintenance_names:
        console.print("[bold]Maintenance[/bold]")
        for label, status in _maintenance_rows(model):
            console.print(f"  {label:<34} {status}")

    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@iceberg_app.command(name="maintenance")
def iceberg_maintenance(
    path: _PathOpt = Path("."),
) -> None:
    """Maintenance posture: which Iceberg housekeeping ops exist in code."""
    _, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Iceberg maintenance[/bold]")
    if not model.has_iceberg:
        console.print("  no Iceberg evidence detected")
        return
    for label, status in _maintenance_rows(model):
        console.print(f"  {label:<34} {status}")
    if not model.has_writes:
        console.print("  (no write operations detected - maintenance may be unnecessary)")
    console.print()


@iceberg_app.command(name="compatibility")
def iceberg_compatibility(
    path: _PathOpt = Path("."),
) -> None:
    """Cross detected runtimes with the Iceberg compatibility pack."""
    _, model = _model_at(path)
    console = Console()
    pack = load_pack("iceberg", "compatibility")
    entries = pack.get("runtimes", {})
    console.print()
    console.print("[bold]Iceberg compatibility[/bold]")
    if not model.runtimes:
        console.print("  no runtime pins detected (no IaC glue_version etc.)")
        return
    for name, e in sorted(model.runtimes.items()):
        entry = entries.get(name, {}).get(e.value)
        if entry is None:
            console.print(f"  {name} {e.value}: no compatibility data")
            continue
        note = entry.get("note", "")
        sev = entry.get("severity", "INFO")
        console.print(f"  {name} {e.value} [{sev}] iceberg={entry.get('iceberg', '?')}")
        console.print(f"    {note}")
    console.print()


@iceberg_app.command(name="merge")
def iceberg_merge(
    path: _PathOpt = Path("."),
) -> None:
    """Reconstruct every detected MERGE statement from the semantic model."""
    _, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Iceberg MERGE analysis[/bold]")
    merges = model.by_kind("merge_detail")
    if not merges:
        console.print("  no MERGE statements detected")
        return
    part_cols = {
        e.name.removeprefix("partition.")
        for e in model.by_kind("property")
        if e.name.startswith("partition.")
    }
    on_cols: dict[str, set[str]] = {}
    for e in model.by_kind("merge_on"):
        on_cols.setdefault(e.name, set()).add(e.value)
    for e in merges:
        cols = sorted(on_cols.get(e.name, set()))
        prunes = bool(set(cols) & part_cols) if part_cols else None
        console.print(f"  [bold]{e.name or '?'}[/bold]  [dim]{e.file.as_posix()}:{e.line}[/dim]")
        console.print(f"    source: {e.value or '?'}  (uniqueness: unknown statically)")
        console.print(f"    on cols: {', '.join(cols) or 'none detected'}")
        if part_cols:
            verdict = "yes" if prunes else "no"
            console.print(
                f"    partition predicate: {verdict} "
                f"(partition cols: {', '.join(sorted(part_cols))})"
            )
        else:
            console.print("    partition predicate: n/a (no PARTITIONED BY evidence)")
    console.print()


@iceberg_app.command(name="files")
def iceberg_files(
    path: _PathOpt = Path("."),
) -> None:
    """Static small-file risk posture (repartition/coalesce near writes)."""
    _, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Iceberg small-file posture[/bold] [dim](static risk)[/dim]")
    patterns = model.by_kind("write_pattern")
    if not patterns:
        console.print("  no repartition/coalesce-before-write patterns detected")
    else:
        for e in patterns:
            console.print(f"  {e.name:<12} [dim]{e.file.as_posix()}:{e.line}[/dim]  {e.value}")
        console.print("  check partition counts; repartition(1)/coalesce() serializes the write")
    if model.by_kind("write_api"):
        console.print("\n[bold]Write APIs[/bold]")
        for e in model.by_kind("write_api"):
            console.print(f"  {e.name:<15} {e.value}  [dim]{e.file.as_posix()}:{e.line}[/dim]")
    console.print()


@iceberg_app.callback(invoke_without_command=True)
def _iceberg_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print(
            "use `forge-doctor-data iceberg inspect|maintenance|compatibility|merge|files`"
        )
        raise typer.Exit(2)
