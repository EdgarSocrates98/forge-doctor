"""`forge-doctor runtime` - offline inspection of exported runtime artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from forge_doctor.analyzers.runtime_evidence import ingest_artifact
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr
from forge_doctor.core.runtime_evidence import RuntimeEvidenceModel

runtime_app = typer.Typer(name="runtime", help="Offline runtime evidence (exported artifacts).")
app.add_typer(runtime_app, name="runtime")

_AdapterOpt = Annotated[
    str | None,
    typer.Option("--adapter", help="Force an adapter instead of auto-detect."),
]
_JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


def _model_dict(model: RuntimeEvidenceModel) -> dict[str, Any]:
    return {
        "source": model.source,
        "identifiers": model.identifiers,
        "executions": [
            {
                "id": e.id,
                "kind": e.kind,
                "state": e.state,
                "duration_ms": e.duration_ms,
                "attempts": e.attempts,
            }
            for e in model.executions
        ],
        "metrics": [
            {"name": m.name, "value": m.value, "unit": m.unit, "scope": m.scope}
            for m in model.metrics
        ],
        "errors": [{"code": e.code, "message": e.message, "count": e.count} for e in model.errors],
        "timings": [{"phase": t.phase, "duration_ms": t.duration_ms} for t in model.timings],
        "throughput": [
            {
                "name": t.name,
                "input_rps": t.input_rps,
                "output_rps": t.output_rps,
                "input_rows": t.input_rows,
                "duration_ms": t.duration_ms,
            }
            for t in model.throughput
        ],
        "lag": [{"name": m.name, "value": m.value, "scope": m.scope} for m in model.lag],
        "retries": model.retries,
        "resource_usage": [
            {"name": m.name, "value": m.value, "unit": m.unit, "scope": m.scope}
            for m in model.resource_usage
        ],
        "state": model.state,
        "events": model.events,
    }


@runtime_app.command(name="inspect")
def runtime_inspect(
    artifact: Annotated[Path, typer.Argument(help="Exported runtime artifact.")],
    adapter: _AdapterOpt = None,
    as_json: _JsonOpt = False,
) -> None:
    """Normalize one artifact into runtime facts (offline)."""
    model = ingest_artifact(artifact, adapter=adapter)
    if as_json:
        typer.echo(json.dumps(_model_dict(model), indent=2))
        return
    console = Console()
    console.print()
    console.print(f"[bold]Runtime Evidence[/bold]  source={model.source}")
    if model.source in {"unknown", "unreadable"}:
        console.print("  no adapter matched - artifact unrecognized")
        raise typer.Exit(2)
    if model.identifiers:
        console.print("  identifiers:")
        for k, v in sorted(model.identifiers.items()):
            console.print(f"    {k:<14} {v}")
    if model.executions:
        console.print(f"  executions: {len(model.executions)}")
        for ex in model.executions[:10]:
            dur = f"{ex.duration_ms:.0f}ms" if ex.duration_ms is not None else "-"
            console.print(f"    {ex.id:<24} {ex.kind:<10} {ex.state:<10} {dur}")
    for mt in model.metrics:
        console.print(f"  metric  {mt.name}={mt.value:g}{mt.unit} {mt.scope}")
    for tm in model.timings:
        console.print(f"  timing  {tm.phase}={tm.duration_ms:.0f}ms {tm.execution_id}")
    for tp in model.throughput:
        console.print(
            f"  throughput {tp.name}: in={tp.input_rps}/s out={tp.output_rps}/s "
            f"rows={tp.input_rows} dur={tp.duration_ms}ms"
        )
    for er in model.errors:
        console.print(f"  error   {er.code}: {er.message}")
    if model.retries:
        console.print(f"  retries: {model.retries}")
    for ru in model.resource_usage:
        console.print(f"  resource {ru.name}={ru.value:g}{ru.unit} {ru.scope}")
    for lg in model.lag:
        console.print(f"  lag     {lg.name}={lg.value:g} {lg.scope}")
    for st in model.state:
        console.print(f"  state   {st}")
    for ev in model.events[:10]:
        console.print(f"  event   {ev}")
    console.print()


@runtime_app.command(name="executions")
def runtime_executions(
    artifact: Annotated[Path, typer.Argument(help="Exported engine artifact.")],
    adapter: _AdapterOpt = None,
    as_json: _JsonOpt = False,
) -> None:
    """Normalize an exported artifact into QueryExecution spines."""
    from forge_doctor.analyzers.execution_adapters import ingest_executions
    from forge_doctor.core.execution_model import sanitize_text

    source, executions = ingest_executions(artifact, adapter=adapter)
    if as_json:
        typer.echo(
            sanitize_text(
                json.dumps(
                    {
                        "source": source,
                        "count": len(executions),
                        "executions": [e.to_dict() for e in executions],
                    },
                    indent=2,
                )
            )
        )
        return
    console = Console()
    console.print()
    console.print(f"[bold]Query Executions[/bold]  source={source} count={len(executions)}")
    if source in {"unknown", "unreadable"}:
        console.print("  no adapter matched - artifact unrecognized")
        raise typer.Exit(2)
    for ex in executions:
        dur = f"{ex.duration_ms:.0f}ms" if ex.duration_ms is not None else "-"
        console.print(
            f"  {ex.execution_id} engine={ex.engine} status={ex.status.value} "
            f"dur={dur} fp={ex.query_fingerprint or '-'}"
        )
        for st in ex.stages:
            console.print(
                f"    stage {st.id} kind={st.kind.value} "
                f"in={st.input_bytes}B out={st.output_bytes}B "
                f"shuffle={st.shuffle_bytes}B spill={st.spill_bytes}B"
            )
        for name, mv in sorted(ex.metrics.known().items()):
            console.print(f"    metric {name}={mv.value:g} ({mv.basis})")
        unknown = ex.metrics.unknown()
        if unknown:
            console.print(f"    unknown: {', '.join(unknown)}")
    console.print()


@runtime_app.command(name="performance")
def runtime_performance(
    artifact: Annotated[Path, typer.Argument(help="Exported engine artifact.")],
    adapter: _AdapterOpt = None,
    as_json: _JsonOpt = False,
) -> None:
    """Derive performance signals + PERF findings from an artifact."""
    from forge_doctor.analyzers.execution_adapters import ingest_executions
    from forge_doctor.core.performance import (
        PerfPolicy,
        extract_signals,
        perf_findings,
    )

    source, executions = ingest_executions(artifact, adapter=adapter)
    signals = extract_signals(executions)
    findings = perf_findings(executions, signals, PerfPolicy.defaults())
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "source": source,
                    "executions": len(executions),
                    "signals": [
                        {
                            "family": s.family.value,
                            "subject": s.subject,
                            "value": s.value,
                            "observed": s.observed,
                            "derived": s.derived,
                            "confidence": s.confidence.value,
                        }
                        for s in signals
                    ],
                    "findings": [f.to_dict() for f in findings],
                },
                indent=2,
            )
        )
        return
    console = Console()
    console.print()
    console.print(
        f"[bold]Performance[/bold]  source={source} executions={len(executions)} "
        f"signals={len(signals)}"
    )
    for s in signals:
        console.print(f"  {s.family.value:<28} {s.subject} value={s.value} [{s.confidence.value}]")
        console.print(f"    observed: {s.observed} | derived: {s.derived}")
    warn = [f for f in findings if f.severity.value == "warning"]
    for f in warn:
        console.print(f"  [yellow]{f.check_id}[/yellow] {f.message}")
    console.print()


@runtime_app.command(name="cost")
def runtime_cost(
    artifact: Annotated[Path, typer.Argument(help="Exported engine artifact.")],
    adapter: _AdapterOpt = None,
    root: Annotated[
        Path | None,
        typer.Option("--root", help="Project root for entity drivers/transfers."),
    ] = None,
    as_json: _JsonOpt = False,
) -> None:
    """Derive technical cost drivers + COST findings (never prices)."""
    from forge_doctor.analyzers.execution_adapters import ingest_executions
    from forge_doctor.core.cost_drivers import (
        CostPolicy,
        cost_findings,
        detect_transfers,
        extract_drivers,
    )

    source, executions = ingest_executions(artifact, adapter=adapter)
    graph = None
    if root is not None:
        from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
        from forge_doctor.core.context import ProjectContext

        graph = build_platform_graph(ProjectContext(root=root.resolve()))
    drivers = extract_drivers(executions, graph)
    transfers = detect_transfers(executions, graph)
    findings = cost_findings(drivers, transfers, CostPolicy.defaults(), executions)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "source": source,
                    "executions": len(executions),
                    "drivers": [d.to_dict() for d in drivers],
                    "transfers": [t.to_dict() for t in transfers],
                    "findings": [f.to_dict() for f in findings],
                },
                indent=2,
            )
        )
        return
    console = Console()
    console.print()
    console.print(
        f"[bold]Cost Drivers[/bold]  source={source} executions={len(executions)} "
        f"drivers={len(drivers)} transfers={len(transfers)}"
    )
    for d in drivers:
        console.print(
            f"  {d.kind.value:<20} {d.entity or d.source} {d.value:.0f} {d.unit}"
            + (f" team={d.team}" if d.team else "")
        )
    for t in transfers:
        console.print(f"  [cyan]transfer[/cyan] {t.source_cloud} -> {t.target_cloud} ({t.mode})")
    warn = [f for f in findings if f.severity.value == "warning"]
    for f in warn:
        console.print(f"  [yellow]{f.check_id}[/yellow] {f.message}")
    console.print()


@runtime_app.command(name="reliability")
def runtime_reliability(
    root: Annotated[Path, typer.Argument(help="Project root to analyze.")],
    artifact: Annotated[
        Path | None,
        typer.Option("--artifact", help="Optional exported runtime artifact."),
    ] = None,
    adapter: _AdapterOpt = None,
    as_json: _JsonOpt = False,
) -> None:
    """Reliability models, delivery semantics, objectives + REL findings."""
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.reliability import (
        delivery_semantics,
        extract_objectives,
        extract_reliability,
        failure_domains,
        freshness_paths,
        observe_reliability,
        rel_findings,
    )

    graph = build_platform_graph(ProjectContext(root=root.resolve()))
    executions: list[Any] = []
    if artifact is not None:
        from forge_doctor.analyzers.execution_adapters import ingest_executions

        _, executions = ingest_executions(artifact, adapter=adapter)
    models = observe_reliability(extract_reliability(graph), executions)
    objectives = extract_objectives(graph)
    fps = freshness_paths(graph, executions)
    domains = failure_domains(graph)
    findings = rel_findings(models, objectives, fps, executions, graph)
    semantics = {m.subject: delivery_semantics(m).value for m in models}
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "models": [m.to_dict() for m in models],
                    "delivery_semantics": semantics,
                    "objectives": [o.to_dict() for o in objectives],
                    "freshness": [p.to_dict() for p in fps],
                    "failure_domains": [d.to_dict() for d in domains],
                    "findings": [f.to_dict() for f in findings],
                },
                indent=2,
            )
        )
        return
    console = Console()
    console.print()
    console.print(
        f"[bold]Reliability[/bold]  models={len(models)} "
        f"objectives={len(objectives)} paths={len(fps)} findings={len(findings)}"
    )
    for m in models:
        declared = [x.name for x in m.mechanisms if x.state.value in ("declared", "observed")]
        console.print(
            f"  {m.subject} ({m.engine}) -> {semantics[m.subject]} [{', '.join(declared)}]"
        )
    for p in fps:
        lag = f"{p.total_lag:.0f}s" if p.total_lag is not None else "PARTIAL"
        console.print(f"  freshness {p.subject}: lag={lag}")
    for f in findings:
        color = "yellow" if f.severity.value == "warning" else "dim"
        console.print(f"  [{color}]{f.check_id}[/{color}] {f.message}")
    console.print()


@runtime_app.command(name="diagnose")
def runtime_diagnose(
    artifact: Annotated[Path, typer.Argument(help="Exported runtime artifact.")],
    adapter: _AdapterOpt = None,
) -> None:
    """Match the artifact's errors against known error signatures."""
    from forge_doctor.core.diagnose import diagnose_text

    model = ingest_artifact(artifact, adapter=adapter)
    text = artifact.read_text(encoding="utf-8", errors="replace")
    console = Console()
    console.print()
    console.print(f"[bold]Runtime Diagnose[/bold]  source={model.source}")
    diagnoses = diagnose_text(text)
    for er in model.errors:
        console.print(f"  extracted {er.code}: {er.message} (x{er.count})")
    if diagnoses:
        console.print("  known signatures:")
        for d in diagnoses:
            console.print(
                f"    {d.signature.id} {d.signature.title} x{d.count} ({d.signature.domain})"
            )
            for fix in d.signature.fixes[:2]:
                console.print(f"      fix: {fix}")
    elif not model.errors:
        console.print("  no errors extracted, no signatures matched")
    console.print()


@runtime_app.command(name="history")
def runtime_history(
    artifact: Annotated[
        Path | None,
        typer.Argument(help="Artifact to record into execution history."),
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Project root holding .forge-doctor/.")
    ] = Path("."),
    kind: Annotated[str, typer.Option("--kind", help="production|experiment")] = "production",
    adapter: _AdapterOpt = None,
    as_json: _JsonOpt = False,
) -> None:
    """Record an artifact batch and/or list recorded history series.

    With ``artifact``, the normalized executions are appended as one
    compact JSONL snapshot under ``.forge-doctor/execution-history/``
    (metrics + fingerprints only — never raw logs or SQL).  Without an
    artifact, the stored series are listed.
    """
    from forge_doctor.core.execution_history import (
        iter_samples,
        record_executions,
    )

    recorded = ""
    if artifact is not None:
        from forge_doctor.analyzers.execution_adapters import ingest_executions

        source, executions = ingest_executions(artifact, adapter=adapter)
        if not executions:
            _stderr.print(f"[red]no executions extracted[/red] - source={source}, nothing recorded")
            raise typer.Exit(2)
        path = record_executions(root, executions, kind=kind)
        recorded = f"{source}->{path.name} ({len(executions)} samples)"
    samples = list(iter_samples(root, kind=None if kind == "all" else kind))
    series = build_series_from_samples(samples)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "recorded": recorded,
                    "series": [s.to_dict() for s in series.values()],
                },
                indent=2,
            )
        )
        return
    console = Console()
    console.print()
    console.print(f"[bold]Execution History[/bold]  root={root}")
    if recorded:
        console.print(f"  recorded: {recorded}")
    for sid, s in sorted(series.items()):
        span = f"{s.first_seen}..{s.last_seen}" if s.first_seen else "no timestamps"
        console.print(f"  {sid} samples={s.sample_count} engine={s.engine} [{span}]")
    if not series:
        console.print("  no recorded history - pass an artifact to record")
    console.print()


def build_series_from_samples(samples: list[Any]) -> dict[str, Any]:
    """Reaggregate stored samples back into fingerprint series."""
    from forge_doctor.core.execution_history import (
        ExecutionSeries,
        SubjectKind,
    )

    grouped: dict[str, list[Any]] = {}
    for s in samples:
        key = s.fingerprint or s.execution_id
        if not key:
            continue
        grouped.setdefault(key, []).append(s)
    out: dict[str, ExecutionSeries] = {}
    for sid, rows in grouped.items():
        ordered = sorted(rows, key=lambda s: (s.timestamp is None, s.timestamp or 0))
        out[sid] = ExecutionSeries(
            series_id=f"{SubjectKind.FINGERPRINT.value}:{sid}",
            subject_kind=SubjectKind.FINGERPRINT,
            subject_id=sid,
            engine=rows[0].engine,
            fingerprint=sid,
            samples=tuple(ordered),
            evidence_sources=tuple(sorted({e for s in rows for e in s.evidence})),
        )
    return out


@runtime_app.command(name="baseline")
def runtime_baseline(
    artifact: Annotated[
        Path | None,
        typer.Argument(help="Artifact to baseline directly (no recording)."),
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Project root holding .forge-doctor/.")
    ] = Path("."),
    last: Annotated[int, typer.Option("--last", help="Baseline over last N executions.")] = 0,
    days: Annotated[int, typer.Option("--days", help="Baseline over last N days.")] = 0,
    metric: Annotated[str | None, typer.Option("--metric", help="Show one metric only.")] = None,
    adapter: _AdapterOpt = None,
    as_json: _JsonOpt = False,
) -> None:
    """Robust baselines (median/p95/MAD) per fingerprint or recorded series."""
    from forge_doctor.core.execution_history import (
        BaselineWindow,
        BaselineWindowKind,
        SubjectKind,
        baseline_for,
        build_series,
    )

    if artifact is not None:
        from forge_doctor.analyzers.execution_adapters import ingest_executions

        _, executions = ingest_executions(artifact, adapter=adapter)
        series = build_series(executions, SubjectKind.FINGERPRINT)
    else:
        from forge_doctor.core.execution_history import iter_samples

        series = build_series_from_samples(list(iter_samples(root)))
    kind = (
        BaselineWindowKind.LAST_N
        if last
        else (BaselineWindowKind.LAST_DAYS if days else BaselineWindowKind.LAST_N)
    )
    window = BaselineWindow(kind=kind, n=last or days)
    rows = [baseline_for(s, window) for s in series.values()]
    if metric:
        rows = [b for b in rows if metric in b.metrics]
    if as_json:
        typer.echo(json.dumps({"baselines": [b.to_dict() for b in rows]}, indent=2))
        return
    console = Console()
    console.print()
    console.print(f"[bold]Baselines[/bold]  window={rows[0].window if rows else '-'}")
    for b in rows:
        dur = b.metrics.get("duration_ms")
        p95 = f"{dur.p95:.0f}ms" if dur and dur.p95 is not None else "-"
        med = f"{dur.median:.0f}ms" if dur and dur.median is not None else "-"
        console.print(
            f"  {b.subject_id} samples={b.sample_count} median={med} "
            f"p95={p95} confidence={b.confidence}"
        )
    console.print()


@runtime_app.command(name="trend")
def runtime_trend(
    artifact: Annotated[
        Path | None,
        typer.Argument(help="Artifact to trend directly (no recording)."),
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Project root holding .forge-doctor/.")
    ] = Path("."),
    metric: Annotated[str, typer.Option("--metric", help="Metric to trend.")] = "duration_ms",
    adapter: _AdapterOpt = None,
    as_json: _JsonOpt = False,
) -> None:
    """Per-series trend direction for one metric (rising/falling/stable)."""
    from forge_doctor.core.execution_history import (
        SubjectKind,
        build_series,
    )
    from forge_doctor.core.trends import trend_direction

    if artifact is not None:
        from forge_doctor.analyzers.execution_adapters import ingest_executions

        _, executions = ingest_executions(artifact, adapter=adapter)
        series = build_series(executions, SubjectKind.FINGERPRINT)
    else:
        from forge_doctor.core.execution_history import iter_samples

        series = build_series_from_samples(list(iter_samples(root)))
    rows = []
    for sid in sorted(series):
        ts = series[sid].metric_series(metric)
        rows.append(
            {
                "series": sid,
                "metric": metric,
                "samples": ts.count,
                "trend": trend_direction(ts).value,
            }
        )
    if as_json:
        typer.echo(json.dumps({"trends": rows}, indent=2))
        return
    console = Console()
    console.print()
    console.print(f"[bold]Trend[/bold]  metric={metric}")
    for r in rows:
        console.print(f"  {r['series']} samples={r['samples']} -> {r['trend']}")
    console.print()


@runtime_app.command(name="regressions")
def runtime_regressions(
    artifact: Annotated[
        Path | None,
        typer.Argument(help="Artifact to analyze (or recorded history when omitted)."),
    ] = None,
    root: Annotated[
        Path, typer.Option("--root", help="Project root holding .forge-doctor/.")
    ] = Path("."),
    adapter: _AdapterOpt = None,
    as_json: _JsonOpt = False,
) -> None:
    """Baseline-aware regression detection (PERFREG001-009).

    Compares each series' latest window against its own historical
    baseline — a single slow run reports as a candidate (INFO), only
    persistent breaches warn.
    """
    from forge_doctor.core.execution_history import SubjectKind, build_series
    from forge_doctor.core.regression import (
        RegressionPolicy,
        detect_regressions,
        perfreg_findings,
    )

    if artifact is not None:
        from forge_doctor.analyzers.execution_adapters import ingest_executions

        _, executions = ingest_executions(artifact, adapter=adapter)
        series = build_series(executions, SubjectKind.FINGERPRINT)
    else:
        from forge_doctor.core.execution_history import iter_samples

        series = build_series_from_samples(list(iter_samples(root)))
    pol = RegressionPolicy.defaults()
    signals = detect_regressions(series, pol)
    findings = perfreg_findings(series, pol)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "signals": [
                        {
                            "subject": s.subject,
                            "dimension": s.dimension.value,
                            "class": s.klass.value,
                            "current": s.current,
                            "baseline": s.baseline_value,
                            "basis": s.basis,
                            "confidence": s.confidence.value,
                        }
                        for s in signals
                    ],
                    "findings": [f.to_dict() for f in findings],
                },
                indent=2,
            )
        )
        return
    console = Console()
    console.print()
    console.print(f"[bold]Regressions[/bold]  series={len(series)}")
    for s in signals:
        console.print(
            f"  {s.subject} {s.dimension.value}: {s.klass.value} "
            f"(current={s.current} baseline={s.baseline_value} "
            f"conf={s.confidence.value})"
        )
        console.print(f"    {s.basis}")
    for f in findings:
        console.print(f"  [{f.severity.value}]{f.check_id}[/{f.severity.value}] {f.message}")
    if not signals and not findings:
        console.print("  insufficient data or no regression")
    console.print()


@runtime_app.command(name="correlate")
def runtime_correlate(
    events: Annotated[
        Path,
        typer.Argument(help="JSON file of change events (deployment export / diff output)."),
    ],
    root: Annotated[
        Path, typer.Option("--root", help="Project root holding .forge-doctor/.")
    ] = Path("."),
    window_minutes: Annotated[
        int, typer.Option("--window", help="Max minutes between change and regression start.")
    ] = 120,
    as_json: _JsonOpt = False,
) -> None:
    """Correlate recorded change events with regression episodes.

    Evidence-gated: a correlation is reported only when at least two
    evidence legs hold (temporal proximity, entity overlap, graph path,
    metric relevance).  Language stays 'correlated with' — never cause.
    """
    from forge_doctor.core.change_correlation import (
        change_events_from_json,
        correlate,
    )
    from forge_doctor.core.execution_history import iter_samples

    changes = change_events_from_json(events)
    series = build_series_from_samples(list(iter_samples(root)))
    graph = None
    try:
        from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
        from forge_doctor.core.context import ProjectContext

        graph = build_platform_graph(ProjectContext(root=root.resolve()))
    except Exception:
        graph = None
    correlations = correlate(changes, series, graph, window_ms=window_minutes * 60_000)
    if as_json:
        typer.echo(json.dumps([c.to_dict() for c in correlations], indent=2))
        return
    console = Console()
    console.print()
    console.print(
        f"[bold]Change <-> runtime correlation[/bold]  changes={len(changes)} series={len(series)}"
    )
    for c in correlations:
        dims = ",".join(d.value for d in c.matching_dimensions)
        console.print(f"  [{c.confidence.value}] {c.change.id} -> {c.subject} {dims}")
        console.print(f"    {c.explanation}")
    if not correlations:
        console.print("  no correlations met the evidence threshold")
    console.print()


@runtime_app.callback(invoke_without_command=True)
def _runtime_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor runtime inspect|diagnose <artifact>`")
        raise typer.Exit(2)
