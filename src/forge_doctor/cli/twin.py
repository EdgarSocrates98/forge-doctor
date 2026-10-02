"""``forge-doctor twin`` - the formal digital twin: validated platform snapshot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.cli.app import app
from forge_doctor.cli.common import PathArg, _build_registry
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.runner import CheckRunner
from forge_doctor.core.twin import Twin, build_twin, twin_snapshot

twin_app = typer.Typer(name="twin", help="Formal digital twin: validated platform snapshot.")
app.add_typer(twin_app, name="twin")


def _twin_at(path: Path) -> tuple[ProjectContext, Twin]:
    """Build graph + scan report, assemble the twin."""
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    ctx = ProjectContext(root=path.resolve())
    graph = build_platform_graph(ctx)
    registry, _ = _build_registry(config=ctx.config)
    report = CheckRunner(registry).run(ctx)
    return ctx, build_twin(graph, report, ctx.root)


@twin_app.command(name="inspect")
def twin_inspect(
    path: PathArg = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Twin summary + invariant report. Exit 1 on hard violations."""
    _, twin = _twin_at(path)
    rep = twin.report
    if fmt == "json":
        typer.echo(json.dumps(rep.to_dict(), indent=2, sort_keys=True))
    else:
        console = Console()
        console.print()
        console.print("[bold]Digital twin[/bold]")
        console.print(
            f"  {rep.entity_count} entities, {rep.relationship_count} relationships, "
            f"{twin.scan_findings} findings"
        )
        if rep.entities_by_kind:
            table = Table("kind", "entities", title="by kind", title_justify="left")
            for kind, count in rep.entities_by_kind:
                table.add_row(kind, str(count))
            console.print(table)
        if rep.entities_by_domain:
            table = Table("domain", "entities", title="by domain", title_justify="left")
            for domain, count in rep.entities_by_domain:
                table.add_row(domain, str(count))
            console.print(table)
        if rep.violations:
            console.print(f"[red]{len(rep.violations)} invariant violation(s)[/red]")
            for v in rep.violations:
                console.print(f"  [red]{v.invariant}[/red] {v.detail}")
        else:
            console.print("[green]invariants hold[/green]")
        if rep.attr_gaps:
            table = Table(
                "domain",
                "missing name",
                "missing file",
                "missing line",
                title="attr gaps (informational)",
                title_justify="left",
            )
            for g in rep.attr_gaps:
                table.add_row(
                    g.domain,
                    str(g.missing_name),
                    str(g.missing_file),
                    str(g.missing_line),
                )
            console.print(table)
        console.print()
    if not rep.ok:
        raise typer.Exit(1)


@twin_app.command(name="export")
def twin_export(
    path: PathArg = Path("."),
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write to file (default: stdout).")
    ] = None,
) -> None:
    """Emit the deterministic twin snapshot artifact."""
    _, twin = _twin_at(path)
    text = json.dumps(twin_snapshot(twin), indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(text)
    if not twin.report.ok:
        raise typer.Exit(1)
