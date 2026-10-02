"""`forge-doctor kafka|kinesis|flink` - streaming-bus deep-intelligence CLIs."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.analyzers.flink_model import flink_model
from forge_doctor.analyzers.kafka_model import kafka_model
from forge_doctor.analyzers.kinesis_model import kinesis_model
from forge_doctor.checks.streaming_bus import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import render_findings
from forge_doctor.core.context import ProjectContext

kafka_app = typer.Typer(name="kafka", help="Kafka/MSK deep intelligence.")
app.add_typer(kafka_app, name="kafka")
kinesis_app = typer.Typer(name="kinesis", help="Kinesis deep intelligence.")
app.add_typer(kinesis_app, name="kinesis")
flink_app = typer.Typer(name="flink", help="Flink deep intelligence.")
app.add_typer(flink_app, name="flink")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


# ----------------------------------------------------------------- Kafka


@kafka_app.command(name="inspect")
def kafka_inspect(path: _PathOpt = Path(".")) -> None:
    """MSK clusters, topics, consumer groups, options, security."""
    ctx = ProjectContext(root=path.resolve())
    m = kafka_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Kafka / MSK[/bold]")
    if not m.has_kafka:
        console.print("  no kafka evidence detected")
        return
    for c in m.clusters:
        flags = []
        if c.serverless:
            flags.append("serverless")
        if c.public_access:
            flags.append("public")
        if c.logging:
            flags.append("logging")
        console.print(
            f"  cluster [bold]{c.name}[/bold] [dim]{c.source} {c.file.as_posix()}:{c.line}[/dim] "
            f"brokers={c.broker_nodes or '-'} transit={c.encryption_in_transit or 'unset'} "
            f"auth={c.client_auth or 'unset'} {' '.join(flags)}"
        )
    for t in m.topics:
        console.print(
            f"  topic {t.name} [dim]{t.source} {t.file.as_posix()}:{t.line}[/dim] "
            f"partitions={t.partitions or '?'} rf={t.replication or '?'}"
        )
    if m.subscribed_topics:
        console.print(f"  subscribed: {', '.join(sorted(m.subscribed_topics))}")
    if m.consumer_groups:
        console.print(f"  consumer groups: {', '.join(sorted(m.consumer_groups))}")
    caps = [o for o in m.options if "OffsetsPerTrigger" in o.key or o.key == "failOnDataLoss"]
    for o in caps[:8]:
        console.print(f"  option {o.key} [dim]{o.file.as_posix()}:{o.line}[/dim]")
    flags = []
    if m.schema_registry:
        flags.append("schema-registry")
    if m.secure_transport:
        flags.append("tls/sasl")
    if flags:
        console.print(f"  flags: {', '.join(flags)}")
    render_findings(console, ctx, [c for c in CHECKS if c.id.startswith("KFK")], "Kafka risks")


@kafka_app.command(name="findings")
def kafka_findings(path: _PathOpt = Path(".")) -> None:
    ctx = ProjectContext(root=path.resolve())
    render_findings(Console(), ctx, [c for c in CHECKS if c.id.startswith("KFK")], "Kafka findings")


# --------------------------------------------------------------- Kinesis


@kinesis_app.command(name="inspect")
def kinesis_inspect(path: _PathOpt = Path(".")) -> None:
    """Streams, shards, consumers, EFO, retention, flink apps."""
    ctx = ProjectContext(root=path.resolve())
    m = kinesis_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Kinesis[/bold]")
    if not m.has_kinesis:
        console.print("  no kinesis evidence detected")
        return
    for s in m.streams:
        console.print(
            f"  stream [bold]{s.name}[/bold] [dim]{s.source} {s.file.as_posix()}:{s.line}[/dim] "
            f"shards={s.shard_count or '?'} mode={s.mode or 'provisioned'} "
            f"retention={s.retention_hours or '24(default)'}h enc={s.encryption_type or '-'}"
        )
    for c in m.consumers:
        console.print(
            f"  efo-consumer {c['name']} [dim]{c['source']} "
            f"{c['file'].as_posix()}:{c['line']}[/dim]"
        )
    for f in m.flink_apps:
        console.print(
            f"  managed-flink {f.name} [dim]{f.source} {f.file.as_posix()}:{f.line}[/dim] "
            f"runtime={f.runtime_env or '?'} autoscale={f.autoscaling}"
        )
    for fs in m.firehose_streams:
        loc = f"{fs['file'].as_posix()}:{fs['line']}"
        console.print(f"  firehose {fs['name']} [dim]{fs['source']} {loc}[/dim]")
    if m.api_calls:
        apis = sorted({c.api for c in m.api_calls})
        console.print(f"  api calls: {', '.join(apis)}")
    render_findings(console, ctx, [c for c in CHECKS if c.id.startswith("KIN")], "Kinesis risks")


@kinesis_app.command(name="findings")
def kinesis_findings(path: _PathOpt = Path(".")) -> None:
    ctx = ProjectContext(root=path.resolve())
    render_findings(
        Console(), ctx, [c for c in CHECKS if c.id.startswith("KIN")], "Kinesis findings"
    )


# ----------------------------------------------------------------- Flink


@flink_app.command(name="inspect")
def flink_inspect(path: _PathOpt = Path(".")) -> None:
    """Jobs, sources, keyed state, windows, timers, checkpoints, sinks."""
    ctx = ProjectContext(root=path.resolve())
    m = flink_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Flink[/bold]")
    if not m.has_flink:
        console.print("  no flink evidence detected")
        return
    for j in m.jobs:
        console.print(
            f"  job [bold]{j.name}[/bold] [dim]{j.source} {j.file.as_posix()}:{j.line}[/dim] "
            f"parallelism={j.parallelism or '?'} autoscale={j.autoscaling}"
        )
    table = Table(show_header=True, header_style="bold")
    table.add_column("kind")
    table.add_column("detail")
    table.add_column("location")
    for e in m.evidence:
        table.add_row(e.kind, e.detail[:50], f"{e.file.as_posix()}:{e.line}")
    console.print(table)
    flags = []
    if m.has_checkpoint:
        flags.append(f"checkpoint({m.checkpoint_mode or 'default'},{m.checkpoint_interval_ms}ms)")
    if m.has_savepoint:
        flags.append("savepoints")
    if m.has_keyed_state:
        flags.append("keyed-state")
    if m.has_window:
        flags.append("windows")
    if m.has_timer:
        flags.append("timers")
    if flags:
        console.print(f"  flags: {', '.join(flags)}")
    render_findings(console, ctx, [c for c in CHECKS if c.id.startswith("FLK")], "Flink risks")


@flink_app.command(name="findings")
def flink_findings(path: _PathOpt = Path(".")) -> None:
    ctx = ProjectContext(root=path.resolve())
    render_findings(Console(), ctx, [c for c in CHECKS if c.id.startswith("FLK")], "Flink findings")
