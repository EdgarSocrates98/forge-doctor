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


@runtime_app.callback(invoke_without_command=True)
def _runtime_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor runtime inspect|diagnose <artifact>`")
        raise typer.Exit(2)
