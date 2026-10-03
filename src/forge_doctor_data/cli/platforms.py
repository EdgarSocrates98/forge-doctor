"""`forge-doctor-data emr|databricks|delta` - platform deep-intelligence CLIs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor_data.analyzers.databricks_model import databricks_model
from forge_doctor_data.analyzers.delta_model import delta_model
from forge_doctor_data.analyzers.emr_model import emr_model
from forge_doctor_data.checks.platforms import CHECKS
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import render_findings
from forge_doctor_data.core.context import ProjectContext

emr_app = typer.Typer(name="emr", help="EMR deep intelligence (EC2/Serverless/EKS).")
app.add_typer(emr_app, name="emr")
databricks_app = typer.Typer(name="databricks", help="Databricks workspace/job/UC intelligence.")
app.add_typer(databricks_app, name="databricks")
delta_app = typer.Typer(name="delta", help="Delta Lake feature and ops intelligence.")
app.add_typer(delta_app, name="delta")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


# ---------------------------------------------------------------- EMR


@emr_app.command(name="inspect")
def emr_inspect(path: _PathOpt = Path(".")) -> None:
    """Clusters, serverless apps, EKS virtual clusters, releases, steps."""
    ctx = ProjectContext(root=path.resolve())
    m = emr_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]EMR[/bold]")
    if not m.has_emr:
        console.print("  no EMR workloads detected")
        return
    for c in m.clusters:
        flags = []
        if c.spot_fleets:
            flags.append(f"spot:{c.spot_fleets}")
        if c.dynamic_allocation:
            flags.append("dynalloc")
        if c.autoscaling:
            flags.append("autoscale")
        if c.kerberos:
            flags.append("kerberos")
        console.print(
            f"  [bold]{c.name}[/bold] [dim]{c.source} {c.file.as_posix()}:{c.line}[/dim] "
            f"{c.release} master={c.master_type or '?'} core={c.core_count or '?'} "
            f"{' '.join(flags)}"
        )
        if c.steps:
            console.print(
                f"    steps: {', '.join(c.steps)} ({c.steps_without_fail_action} no-fail-action)"
            )
    for a in m.serverless_apps:
        console.print(
            f"  [bold]{a.name}[/bold] serverless {a.release} engine={a.engine} "
            f"max_cpu={a.max_cpu or '-'} auto_stop={a.auto_stop}"
        )
    for e in m.eks_clusters:
        console.print(f"  [bold]{e.name}[/bold] eks virtual cluster -> {e.eks_cluster or '?'}")
    if m.boto3_calls:
        console.print(f"  boto3: {', '.join(m.boto3_calls)}")


@emr_app.command(name="findings")
def emr_findings(path: _PathOpt = Path(".")) -> None:
    ctx = ProjectContext(root=path.resolve())
    render_findings(
        Console(), ctx, [c for c in CHECKS if c.id.startswith("EMR")], title="EMR findings"
    )


# ---------------------------------------------------------- Databricks


@databricks_app.command(name="inspect")
def dbx_inspect(path: _PathOpt = Path(".")) -> None:
    """Jobs, clusters, warehouses, UC objects, pipelines, bundles."""
    ctx = ProjectContext(root=path.resolve())
    m = databricks_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Databricks[/bold]")
    if not m.has_databricks:
        console.print("  no databricks surface detected")
        return
    for j in m.jobs:
        flags = []
        if j.uses_existing_cluster:
            flags.append("existing-cluster")
        if j.uses_job_cluster:
            flags.append("job-cluster")
        if j.has_notebook_task:
            flags.append("notebook")
        console.print(
            f"  job [bold]{j.name}[/bold] [dim]{j.file.as_posix()}:{j.line}[/dim] "
            f"tasks={j.task_count} {' '.join(flags)}"
        )
    for c in m.clusters:
        console.print(
            f"  cluster [bold]{c.name}[/bold] dbr={c.dbr_version or '?'} "
            f"node={c.node_type or '?'} workers={c.num_workers or '-'} "
            f"{'autoscale' if c.autoscale else 'fixed'}"
            f"{' job-cluster' if c.is_job_cluster else ''}"
        )
    for w in m.warehouses:
        console.print(f"  warehouse [bold]{w.name}[/bold] size={w.cluster_size or '?'}")
    for o in m.uc_objects:
        console.print(f"  uc {o.kind} [bold]{o.name}[/bold]")
    for p in m.pipelines:
        console.print(
            f"  pipeline [bold]{p.name}[/bold] continuous={p.continuous} dev={p.development}"
        )
    for b in m.bundles:
        console.print(f"  bundle [dim]{b.as_posix()}[/dim]")
    if m.sdk_imports or m.dbutils_calls:
        console.print(
            f"  sdk: {', '.join(m.sdk_imports[:4])} dbutils: {', '.join(m.dbutils_calls[:6])}"
        )


@databricks_app.command(name="findings")
def dbx_findings(path: _PathOpt = Path(".")) -> None:
    ctx = ProjectContext(root=path.resolve())
    render_findings(
        Console(), ctx, [c for c in CHECKS if c.id.startswith("DBX")], title="Databricks findings"
    )


# --------------------------------------------------------------- Delta


@delta_app.command(name="inspect")
def delta_inspect(path: _PathOpt = Path(".")) -> None:
    """Tables, ops (merge/optimize/vacuum), features, protocol."""
    ctx = ProjectContext(root=path.resolve())
    m = delta_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Delta Lake[/bold]")
    if not m.has_delta:
        console.print("  no delta usage detected")
        return
    if m.tables:
        console.print(f"  tables: {', '.join(sorted(m.tables))}")
    counts = m.op_counts()
    if counts:
        console.print(f"  ops: {', '.join(f'{k}={n}' for k, n in sorted(counts.items()))}")
    if m.features:
        console.print(f"  features: {', '.join(sorted(m.features))}")
    console.print(
        f"  protocol: r{m.protocol_reader or '?'}/w{m.protocol_writer or '?'} "
        f"reads={m.delta_reads} writes={m.delta_writes} streaming={m.streaming_delta}"
    )


@delta_app.command(name="findings")
def delta_findings(path: _PathOpt = Path(".")) -> None:
    ctx = ProjectContext(root=path.resolve())
    render_findings(
        Console(), ctx, [c for c in CHECKS if c.id.startswith("DELTA")], title="Delta findings"
    )


@delta_app.command(name="features")
def delta_features(path: _PathOpt = Path(".")) -> None:
    """Detected Delta features mapped to protocol requirements."""
    ctx = ProjectContext(root=path.resolve())
    m = delta_model(ctx)
    console = Console()
    console.print()
    if not m.features:
        console.print("no delta features detected")
        return
    table = Table()
    table.add_column("feature")
    table.add_column("ops")
    for feat in sorted(m.features):
        related = [o.op for o in m.ops if o.op in ("merge", "vacuum", "optimize", "cluster_by")]
        table.add_row(feat, ", ".join(sorted(set(related))) or "-")
    console.print(table)
