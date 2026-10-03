"""`forge-doctor-data neptune` - Neptune model, query, ingest, explain views."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor_data.analyzers.neptune_explain import analyze_explain
from forge_doctor_data.analyzers.neptune_model import (
    NeptuneProjectModel,
    neptune_model,
)
from forge_doctor_data.analyzers.neptune_queries import neptune_queries
from forge_doctor_data.checks.neptune import CHECKS
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _stderr, render_findings
from forge_doctor_data.core.context import ProjectContext

neptune_app = typer.Typer(name="neptune", help="Neptune Database + Analytics intelligence.")
app.add_typer(neptune_app, name="neptune")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


def _model_at(path: Path) -> tuple[ProjectContext, NeptuneProjectModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, neptune_model(ctx)


@neptune_app.command(name="inspect")
def neptune_inspect(path: _PathOpt = Path(".")) -> None:
    """Clusters, instances, endpoints, languages, product split."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Neptune[/bold]")
    if not model.has_neptune:
        console.print("  no neptune workloads detected")
        return
    console.print(f"  product: {model.product}")
    for c in model.clusters:
        flags = []
        if c.engine_version:
            flags.append(f"engine={c.engine_version}")
        if c.iam_auth is not None:
            flags.append(f"iam_auth={c.iam_auth}")
        if c.serverless:
            flags.append("serverless")
        if c.global_cluster:
            flags.append(f"global:{c.global_cluster}")
        console.print(
            f"  [bold]{c.name}[/bold] [dim]{c.source} {c.file.as_posix()}:{c.line}[/dim] "
            f"instances={len(c.instances)} {' '.join(flags)}"
        )
    for e in model.endpoints[:10]:
        console.print(
            f"    endpoint {e.value} port={e.port or '?'} kind={e.kind or '-'} "
            f"ssl={e.ssl} [dim]{e.file.as_posix()}:{e.line}[/dim]"
        )
    if model.query_languages:
        console.print(f"  languages: {', '.join(sorted(model.query_languages))}")
    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@neptune_app.command(name="schema")
def neptune_schema(path: _PathOpt = Path(".")) -> None:
    """Graph schema facts (labels, endpoints) seen by Neptune queries."""
    ctx, _model = _model_at(path)
    from forge_doctor_data.analyzers.graph_model import graph_model

    gm = graph_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]Neptune schema surface[/bold]")
    if not gm.vertex_labels and not gm.edge_labels and not gm.predicates:
        console.print("  no graph schema facts detected")
        return
    for label in sorted(gm.vertex_labels):
        console.print(f"  vertex :{label}")
    for label in sorted(gm.edge_labels):
        console.print(f"  edge   :{label}")
    for pred in sorted(gm.predicates):
        console.print(f"  predicate <{pred}>")
    console.print()


@neptune_app.command(name="queries")
def neptune_queries_cmd(path: _PathOpt = Path(".")) -> None:
    """Per-query shape inventory (language, selectivity, bounds)."""
    ctx, _model = _model_at(path)
    report = neptune_queries(ctx)
    console = Console()
    console.print()
    console.print("[bold]Neptune queries[/bold]")
    if not report.queries:
        console.print("  no queries detected")
        return
    table = Table("lang", "start", "hops", "bounds", "file")
    for q in report.queries:
        bounds = q.hop_bounds or ("bounded" if q.result_bounded else "-")
        table.add_row(
            q.language,
            (q.start or "?")[:30],
            str(q.hop_count),
            bounds + ("!" if not q.parsed else ""),
            f"{q.file.as_posix()}:{q.line}",
        )
    console.print(table)
    console.print()


@neptune_app.command(name="ingest")
def neptune_ingest(path: _PathOpt = Path(".")) -> None:
    """Bulk-loader usage and ingestion-relevant config."""
    _ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Neptune ingest[/bold]")
    if not model.bulk_loads:
        console.print("  no bulk-loader usage detected")
        return
    for load in model.bulk_loads:
        console.print(
            f"  [{load.origin}] {load.file.as_posix()}:{load.line} "
            f"source={load.source_s3 or '-'} format={load.format or '-'} "
            f"role={'set' if load.iam_role else 'MISSING'} "
            f"parallelism={load.parallelism or '-'}"
        )
    console.print()


def _render_explain(path: Path) -> int:
    console = Console()
    report = analyze_explain(path)
    console.print()
    console.print(f"[bold]explain[/bold] {path}")
    kind = report.evidence_kind.value if report.evidence_kind else "unknown"
    console.print(
        f"  language={report.language} evidence={kind} steps={len(report.steps)}"
        + (f" max_cardinality={report.max_cardinality}" if report.max_cardinality else "")
    )
    for flag in report.flags:
        console.print(f"  [yellow]{flag.kind}[/yellow] {flag.detail}")
    if not report.parsed:
        console.print("  [dim]artifact did not parse[/dim]")
    console.print()
    return 0 if report.parsed else 2


@neptune_app.command(name="explain")
def neptune_explain(
    file: Annotated[Path, typer.Argument(help="Exported explain/profile artifact.")],
) -> None:
    """Analyze a user-supplied explain/profile file (offline)."""
    if not file.exists():
        _stderr.print(f"not found: {file}")
        raise typer.Exit(2)
    code = _render_explain(file)
    if code:
        raise typer.Exit(code)


@neptune_app.command(name="analyze-explain")
def neptune_analyze_explain(
    file: Annotated[Path, typer.Argument(help="Exported explain/profile artifact.")],
) -> None:
    """Alias of `explain` (spec 179 names both entry points)."""
    neptune_explain(file)


@neptune_app.command(name="compatibility")
def neptune_compatibility(path: _PathOpt = Path(".")) -> None:
    """Query-language vs graph-paradigm compatibility via registry."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Neptune compatibility[/bold]")
    caps = getattr(ctx, "capabilities", None)
    if caps is None:
        console.print("  capability registry unavailable")
        return
    from forge_doctor_data.analyzers.graph_model import graph_model

    paradigms = graph_model(ctx).paradigms or {"unknown"}
    cap_by_lang = {
        "gremlin": "NEPTUNE_GREMLIN",
        "opencypher": "NEPTUNE_OPENCYPHER",
        "sparql": "NEPTUNE_SPARQL",
    }
    any_row = False
    for lang in sorted(model.query_languages):
        cap_id = cap_by_lang.get(lang)
        if cap_id is None:
            continue
        for paradigm in sorted(paradigms):
            res = caps.evaluate(cap_id, platform="neptune", graph_model=paradigm)
            console.print(f"  {lang} on {paradigm}: {res.status.value} - {res.reason}")
            any_row = True
    if not any_row:
        console.print("  no query languages detected")
    console.print()


@neptune_app.callback(invoke_without_command=True)
def _neptune_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor-data neptune inspect`")
        raise typer.Exit(2)
