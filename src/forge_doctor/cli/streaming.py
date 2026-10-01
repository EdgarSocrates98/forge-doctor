"""`forge-doctor streaming` - model-driven streaming inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.padding import Padding
from rich.text import Text

from forge_doctor.analyzers.streaming_model import (
    StreamingProjectModel,
    streaming_model,
)
from forge_doctor.checks.streaming import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr, render_findings
from forge_doctor.core.context import ProjectContext

streaming_app = typer.Typer(name="streaming", help="Streaming intelligence.")
app.add_typer(streaming_app, name="streaming")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


def _model_at(path: Path) -> tuple[ProjectContext, StreamingProjectModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, streaming_model(ctx)


@streaming_app.command(name="inspect")
def streaming_inspect(
    path: _PathOpt = Path("."),
) -> None:
    """Summarize streaming queries from the semantic model."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Streaming[/bold]")

    if not model.has_streaming:
        console.print("  no streaming workloads detected")
        return

    for q in model.queries:
        src = q.source or "unknown"
        snk = q.sink or "unknown"
        if q.source_identifier:
            src = f"{src}({q.source_identifier})"
        if q.sink_identifier:
            snk = f"{snk}({q.sink_identifier})"
        console.print(
            f"\n  [bold]{q.name}[/bold]  {q.engine}  {src} -> {snk}  "
            f"[dim]{q.file}:{q.line} ({q.grouping})[/dim]"
        )
        facts = []
        if q.output_mode:
            facts.append(f"outputMode={q.output_mode}")
        if q.trigger_kind:
            facts.append(f"trigger={q.trigger_kind}({q.trigger_arg})")
        ck = q.checkpoint or ("dynamic" if q.checkpoint_dynamic else "none")
        facts.append(f"checkpoint={ck}")
        if q.watermark:
            facts.append(f"watermark={q.watermark}")
        if q.stateful_ops:
            facts.append(f"stateful={','.join(q.stateful_ops)}")
        if q.foreach_batch:
            facts.append(f"foreachBatch={q.foreach_batch}")
        console.print(Padding(Text("  ".join(facts), style="dim"), pad=(0, 0, 0, 4)))

    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@streaming_app.callback(invoke_without_command=True)
def _streaming_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor streaming inspect`")
        raise typer.Exit(2)
