"""`forge-doctor graph` - graph model inspection (domain data plane).

The name ``graph`` predates this domain: the legacy top-level command
dumps the *project* intelligence graph (jobs/datasets/infra). It is kept
as ``graph project`` and as an implicit fallback - ``forge-doctor graph
<path> [--format dot|mermaid|json]`` still works.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.padding import Padding
from rich.table import Table
from rich.text import Text
from typer.core import TyperGroup

from forge_doctor.analyzers.graph_model import GraphProjectModel, graph_model
from forge_doctor.checks.graph import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr, render_findings
from forge_doctor.core.context import ProjectContext
from forge_doctor.output.summary import INTERNAL_ERROR_EXIT

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


class _GraphGroup(TyperGroup):
    """Unknown subcommand -> legacy ``project`` command (path + --format)."""

    def resolve_command(self, ctx, args):  # type: ignore[no-untyped-def]
        if not args:
            return super().resolve_command(ctx, ["project"])
        try:
            return super().resolve_command(ctx, args)
        except Exception:
            if args[0] in {"--help", "-h"}:
                raise
            return super().resolve_command(ctx, ["project", *args])


graph_app = typer.Typer(name="graph", help="Graph intelligence.")
app.add_typer(graph_app, name="graph", cls=_GraphGroup)


def _model_at(path: Path) -> tuple[ProjectContext, GraphProjectModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, graph_model(ctx)


@graph_app.command(name="inspect")
def graph_inspect(path: _PathOpt = Path(".")) -> None:
    """Summarize detected graph workloads, languages, and paradigms."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Graph data[/bold]")
    if not model.has_graph:
        console.print("  no graph workloads detected")
        return
    console.print(
        f"  paradigms: {', '.join(sorted(model.paradigms)) or 'unknown'}   "
        f"languages: {', '.join(sorted(model.languages)) or 'none'}"
    )
    for w in model.workloads:
        console.print(
            f"  [bold]{w.kind}[/bold] {w.paradigm}  [dim]{w.file.as_posix()} ({w.detail})[/dim]"
        )
    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@graph_app.command(name="schema")
def graph_schema(path: _PathOpt = Path(".")) -> None:
    """Show the vertex/edge schema reconstructed from static evidence."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Graph schema[/bold]")
    if not model.vertex_labels and not model.edge_labels and not model.predicates:
        console.print("  no schema evidence")
        return
    if model.vertex_labels:
        table = Table("vertex label", "sites")
        for label, sites in sorted(model.vertex_labels.items()):
            table.add_row(label, str(len(sites)))
        console.print(table)
    if model.edge_labels:
        table = Table("edge label", "sites")
        for label, sites in sorted(model.edge_labels.items()):
            table.add_row(label, str(len(sites)))
        console.print(table)
    if model.predicates:
        table = Table("predicate", "sites")
        for pred, sites in sorted(model.predicates.items()):
            table.add_row(pred, str(len(sites)))
        console.print(table)
    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@graph_app.command(name="traversals")
def graph_traversals(path: _PathOpt = Path(".")) -> None:
    """List traversal inventory with per-traversal shape summary."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Traversals[/bold]")
    if not model.traversals:
        console.print("  no traversals detected")
        return
    for t in model.traversals:
        if not t.parsed:
            console.print(f"  [dim]{t.file.as_posix()}:{t.line} {t.language} unparsed[/dim]")
            continue
        hops = t.hop_bounds or ("variable" if t.has_variable_length else "-")
        selective = "selective" if t.start_selective else "unselective"
        console.print(
            f"\n  [bold]{t.language}[/bold] {t.start}  "
            f"[dim]{t.file.as_posix()}:{t.line} {selective} hops={hops}[/dim]"
        )
        facts = []
        if t.steps:
            facts.append(f"steps={','.join(t.steps[:8])}")
        if t.directions:
            facts.append(f"dirs={','.join(t.directions)}")
        if t.filters:
            facts.append(f"filters={len(t.filters)}")
        if t.writes:
            facts.append("writes")
        if t.projection_star:
            facts.append("RETURN *")
        console.print(Padding(Text("  ".join(facts), style="dim"), pad=(0, 0, 0, 4)))
    render_findings(console, ctx, CHECKS, title="Risks")
    console.print()


@graph_app.command(name="project", hidden=True)
def graph_project(
    path: _PathOpt = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="json|dot|mermaid")] = "json",
) -> None:
    """Project Intelligence Graph: jobs, datasets, infra, orchestrators."""
    import json as _json

    from forge_doctor.core.graph import build_graph

    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    graph = build_graph(ProjectContext(root=path))
    if fmt == "dot":
        typer.echo(graph.to_dot())
    elif fmt == "mermaid":
        typer.echo(graph.to_mermaid())
    else:
        typer.echo(_json.dumps(graph.to_dict(), indent=2, ensure_ascii=False))
