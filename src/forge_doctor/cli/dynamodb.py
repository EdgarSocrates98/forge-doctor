"""`forge-doctor dynamodb` - DynamoDB model + access-pattern inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.analyzers.dynamodb_model import (
    DynamoDBProjectModel,
    dynamodb_model,
)
from forge_doctor.checks.dynamodb import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr, render_findings
from forge_doctor.core.context import ProjectContext

dynamodb_app = typer.Typer(name="dynamodb", help="DynamoDB intelligence.")
app.add_typer(dynamodb_app, name="dynamodb")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


def _model_at(path: Path) -> tuple[ProjectContext, DynamoDBProjectModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, dynamodb_model(ctx)


@dynamodb_app.command(name="inspect")
def dynamodb_inspect(path: _PathOpt = Path(".")) -> None:
    """Summarize tables, keys, capacity mode, streams, global tables."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]DynamoDB[/bold]")
    if not model.has_dynamodb:
        console.print("  no dynamodb workloads detected")
        return
    for t in model.tables:
        keys = f"pk={t.partition_key or '?'}"
        if t.sort_key:
            keys += f" sk={t.sort_key}"
        flags = []
        if t.stream_view_type:
            flags.append(f"stream:{t.stream_view_type}")
        if t.ttl_attribute:
            flags.append(f"ttl:{t.ttl_attribute}")
        if t.is_global:
            flags.append(f"global:{t.global_mode}({len(t.replicas)} regions)")
        if t.encrypted:
            flags.append("encrypted")
        console.print(
            f"  [bold]{t.name}[/bold] [dim]{t.source} {t.file.as_posix()}:{t.line}[/dim] "
            f"{keys} {t.billing_mode or ''} {' '.join(flags)}"
        )
        for g in t.indexes:
            console.print(
                f"    {g.kind} {g.name or '?'}  pk={g.partition_key or '-'}"
                f" sk={g.sort_key or '-'} proj={g.projection or '-'}"
            )
    if model.single_table.entities:
        console.print("  entity prefixes: " + ", ".join(sorted(model.single_table.entities)))
    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@dynamodb_app.command(name="access-patterns")
def dynamodb_access(path: _PathOpt = Path(".")) -> None:
    """List observed access operations per table."""
    _ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Access patterns[/bold]")
    if not model.accesses:
        console.print("  no access operations detected")
        return
    table = Table("op", "table", "index", "file", "flags")
    for a in sorted(model.accesses, key=lambda x: (x.file.as_posix(), x.line)):
        flags = []
        if a.has_key_condition:
            flags.append("key")
        if a.has_projection:
            flags.append("proj")
        if a.has_filter:
            flags.append("filter")
        if a.consistent:
            flags.append("strong")
        table.add_row(
            a.op,
            a.table or "-",
            a.index or "-",
            f"{a.file.as_posix()}:{a.line}",
            ",".join(flags),
        )
    console.print(table)
    console.print()


@dynamodb_app.command(name="indexes")
def dynamodb_indexes(path: _PathOpt = Path(".")) -> None:
    """GSI/LSI inventory vs observed IndexName usage."""
    _ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Indexes[/bold]")
    declared = [(t.name, g) for t in model.tables for g in t.indexes]
    used = {a.index for a in model.accesses if a.index}
    if not declared:
        console.print("  no GSIs/LSIs declared")
    else:
        table = Table("table", "index", "kind", "pk", "sk", "used")
        for tname, g in sorted(declared, key=lambda x: (x[0], x[1].name)):
            mark = "yes" if g.name in used else ("-" if not model.accesses else "no")
            table.add_row(tname, g.name, g.kind, g.partition_key, g.sort_key or "-", mark)
        console.print(table)
    console.print()


@dynamodb_app.command(name="streams")
def dynamodb_streams(path: _PathOpt = Path(".")) -> None:
    """Stream configuration and detected consumers."""
    _ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Streams[/bold]")
    if not model.streams:
        console.print("  no streams detected")
        return
    for s in model.streams:
        console.print(
            f"  [bold]{s.table or '(undeclared table)'}[/bold] "
            f"view={s.view_type or '?'}  "
            f"consumers={len(s.consumers)}  "
            f"idempotency={'yes' if s.idempotency_signal else 'no'}"
        )
        for kind, file, line in s.consumers:
            console.print(f"    consumer {kind} [dim]{file.as_posix()}:{line}[/dim]")
    console.print()


@dynamodb_app.command(name="global-tables")
def dynamodb_global(path: _PathOpt = Path(".")) -> None:
    """Global-table modes (MREC/MRSC), regions, transaction semantics."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Global tables[/bold]")
    if not model.global_tables:
        console.print("  no global tables detected")
        return
    for g in model.global_tables:
        console.print(
            f"  [bold]{g.table}[/bold] mode={g.mode} regions={','.join(g.regions) or '?'}"
        )
        cap = getattr(ctx, "capabilities", None)
        if cap is not None and model.transactions_used:
            res = cap.evaluate(
                "DYNAMODB_TRANSACTIONS",
                platform="dynamodb_global_table",
                variant=g.mode.upper() or None,
            )
            console.print(f"    transactions: {res.status.value} - {res.reason}")
    console.print()


@dynamodb_app.command(name="capacity")
def dynamodb_capacity(path: _PathOpt = Path(".")) -> None:
    """Billing-mode inventory - structure only, no cost math."""
    _ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Capacity[/bold]")
    if not model.tables:
        console.print("  no tables detected")
        return
    for t in model.tables:
        console.print(f"  {t.name}: {t.billing_mode or 'unspecified'} ({t.source})")
    console.print("  [dim]static inventory only - no throughput/cost claims[/dim]")
    console.print()


@dynamodb_app.callback(invoke_without_command=True)
def _dynamodb_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor dynamodb inspect`")
        raise typer.Exit(2)
