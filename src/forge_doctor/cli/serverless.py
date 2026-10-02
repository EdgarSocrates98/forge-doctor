"""`forge-doctor athena|lambda` - serverless deep-intelligence CLIs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.analyzers.athena_model import athena_model
from forge_doctor.analyzers.lambda_model import lambda_model
from forge_doctor.checks.serverless import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import render_findings
from forge_doctor.core.context import ProjectContext

athena_app = typer.Typer(name="athena", help="Athena workgroup/query intelligence.")
app.add_typer(athena_app, name="athena")
lambda_app = typer.Typer(name="lambda", help="Lambda function/trigger intelligence.")
app.add_typer(lambda_app, name="lambda")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


# -------------------------------------------------------------- Athena


@athena_app.command(name="inspect")
def athena_inspect(path: _PathOpt = Path(".")) -> None:
    """Workgroups, catalogs, named queries, SQL ops, boto3 evidence."""
    ctx = ProjectContext(root=path.resolve())
    m = athena_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Athena[/bold]")
    if not m.has_athena:
        console.print("  no athena surface detected")
        return
    for w in m.workgroups:
        flags = []
        if w.enforce_config:
            flags.append("enforced")
        if w.bytes_scanned_cutoff:
            flags.append(f"cutoff={w.bytes_scanned_cutoff}")
        if w.encryption:
            flags.append(w.encryption)
        console.print(
            f"  workgroup [bold]{w.name}[/bold] [dim]{w.file.as_posix()}:{w.line}[/dim] "
            f"engine={w.engine_version or '?'} out={w.result_location or '-'}"
            f"{' ' + ' '.join(flags) if flags else ''}"
        )
    for c in m.catalogs:
        console.print(f"  catalog [bold]{c.name}[/bold] type={c.type}")
    for d in m.databases:
        console.print(f"  database [bold]{d}[/bold]")
    for q in m.named_queries:
        console.print(
            f"  query [bold]{q.name}[/bold] wg={q.workgroup or '?'}"
            f"{' prepared' if q.prepared else ''}"
        )
    if m.sql_ops:
        ops: dict[str, int] = {}
        for o in m.sql_ops:
            ops[o.op] = ops.get(o.op, 0) + 1
        console.print(f"  sql-ops: {', '.join(f'{k}={n}' for k, n in sorted(ops.items()))}")
    if m.iceberg_ddl:
        console.print("  iceberg DDL: yes")
    if m.boto3_calls:
        console.print(f"  boto3: {', '.join(m.boto3_calls)}")


@athena_app.command(name="findings")
def athena_findings(path: _PathOpt = Path(".")) -> None:
    ctx = ProjectContext(root=path.resolve())
    render_findings(
        Console(), ctx, [c for c in CHECKS if c.id.startswith("ATH")], title="Athena findings"
    )


# -------------------------------------------------------------- Lambda


@lambda_app.command(name="inspect")
def lambda_inspect(path: _PathOpt = Path(".")) -> None:
    """Functions, runtimes, triggers, destinations, idempotency evidence."""
    ctx = ProjectContext(root=path.resolve())
    m = lambda_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Lambda[/bold]")
    if not m.has_lambda:
        console.print("  no lambda surface detected")
        return
    for f in m.functions:
        flags = []
        if f.vpc:
            flags.append("vpc")
        if f.dlq:
            flags.append("dlq")
        if f.reserved_concurrency >= 0:
            flags.append(f"reserved={f.reserved_concurrency}")
        if f.provisioned_concurrency:
            flags.append("provisioned")
        if f.layers:
            flags.append(f"layers={f.layers}")
        console.print(
            f"  fn [bold]{f.name}[/bold] [dim]{f.file.as_posix()}:{f.line}[/dim] "
            f"rt={f.runtime or '?'} mem={f.memory_mb or '?'}MB to={f.timeout_s or '?'}s "
            f"arch={'+'.join(f.architectures) or '?'}"
            f"{' ' + ' '.join(flags) if flags else ''}"
        )
    for s in m.event_sources:
        console.print(
            f"  trigger {s.kind} -> [bold]{s.function or '?'}[/bold] "
            f"[dim]{s.file.as_posix()}:{s.line}[/dim]"
        )
    for d in m.destinations:
        console.print(
            f"  dest {d.function}: on_success={d.on_success or '-'} "
            f"on_failure={d.on_failure or '-'}"
        )
    if m.layer_versions:
        console.print(f"  layers: {', '.join(m.layer_versions)}")
    if m.idempotency_evidence:
        console.print(f"  idempotency evidence: {len(m.idempotency_evidence)} site(s)")
    if m.boto3_calls:
        console.print(f"  boto3: {', '.join(m.boto3_calls)}")


@lambda_app.command(name="findings")
def lambda_findings(path: _PathOpt = Path(".")) -> None:
    ctx = ProjectContext(root=path.resolve())
    render_findings(
        Console(), ctx, [c for c in CHECKS if c.id.startswith("LAM")], title="Lambda findings"
    )
