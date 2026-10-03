"""``forge-doctor-data workspace`` - discover and orchestrate sub-projects."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor_data import __version__
from forge_doctor_data.api import SCHEMA_VERSION
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import (
    FailOnOpt,
    NoPluginsOpt,
    ProfileOpt,
    QuietOpt,
    _execute_scan,
    _scan_git_ref,
    _ScanCli,
    _stderr,
    _workspace_projects,
)
from forge_doctor_data.core.models import CheckResult, ScanReport, Severity
from forge_doctor_data.output.json_renderer import result_to_dict
from forge_doctor_data.output.sarif_renderer import render_sarif
from forge_doctor_data.output.summary import INTERNAL_ERROR_EXIT

workspace_app = typer.Typer(
    name="workspace", help="Discover and orchestrate sub-projects.", no_args_is_help=False
)
app.add_typer(workspace_app, name="workspace")


@workspace_app.callback(invoke_without_command=True)
def workspace_default(
    ctx: typer.Context,
    path: Annotated[Path, typer.Option("--path", help="Workspace root.")] = Path("."),
) -> None:
    """Discover sub-projects (pyproject.toml) under a workspace root."""
    if ctx.invoked_subcommand is not None:
        return
    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    console = Console()
    table = Table(title=f"Workspace: {path.resolve()}", title_justify="left")
    table.add_column("Project", style="bold")
    table.add_column("Path")
    projects = _workspace_projects(path)
    if not projects:
        table.add_row("-", "no nested pyproject.toml found")
    for name, rel in projects:
        table.add_row(name, rel.as_posix())
    console.print(table)
    console.print("\n[dim]Scan all: forge-doctor-data workspace scan --path <dir>[/dim]")


@workspace_app.command(name="scan")
def workspace_scan(
    path: Annotated[Path, typer.Option("--path", help="Workspace root.")] = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json|sarif")] = "text",
    profile: ProfileOpt = "default",
    quiet: QuietOpt = False,
    fail_on: FailOnOpt = "error",
    no_plugins: NoPluginsOpt = False,
) -> None:
    """Scan every nested project and aggregate results with a project column."""
    import json as _json

    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    projects = _workspace_projects(path)
    if not projects:
        _stderr.print("[yellow]No nested pyproject.toml found.[/yellow]")
        raise typer.Exit(0)

    merged: list[CheckResult] = []
    per_project: dict[str, list[CheckResult]] = {}
    console = Console(no_color=False)
    for name, rel in projects:
        sub = path / rel
        report, _runner, _sel, _errs, _ctx = _execute_scan(
            _ScanCli(
                path=sub,
                categories=(),
                ignore=(),
                fmt="json",
                quiet=quiet,
                fail_on=fail_on,
                verbose=False,
                no_plugins=no_plugins,
                profile=profile,
            )
        )
        per_project[name] = report.results
        for result in report.results:
            merged.append(
                dataclasses.replace(
                    result,
                    file=Path(rel.as_posix()) / result.file
                    if result.file
                    else Path(rel.as_posix()),
                )
            )

    if fmt == "json":
        typer.echo(
            _json.dumps(
                {
                    "tool": "forge-doctor-data",
                    "schema_version": SCHEMA_VERSION,
                    "projects": {
                        name: [result_to_dict(r) for r in rs] for name, rs in per_project.items()
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    if fmt == "sarif":
        report = ScanReport(version=__version__, project=path.resolve(), results=merged)
        typer.echo(render_sarif(report))
        return

    for name, rs in per_project.items():
        table = Table(title=f"{name}", title_justify="left")
        table.add_column("Id", style="dim")
        table.add_column("Severity")
        table.add_column("Finding")
        table.add_column("Location", style="dim")
        for r in rs:
            if r.severity is Severity.PASS:
                continue
            table.add_row(
                r.check_id,
                r.severity.value,
                r.message[:70],
                f"{r.file.as_posix()}:{r.line}" if r.file and r.line else "-",
            )
        console.print(table)
    summary = ScanReport(version=__version__, project=path.resolve(), results=merged).summary
    console.print(
        f"\n[bold]{len(projects)} projects[/bold] - {summary.errors} errors, "
        f"{summary.warnings} warnings"
    )
    raise typer.Exit(1 if summary.errors else 0)


@workspace_app.command(name="inspect")
def workspace_inspect(
    path: Annotated[Path, typer.Option("--path", help="Workspace root.")] = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Build the WorkspaceModel: repos, merged platform graph, cross-repo links."""
    import json as _json

    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.workspace import build_workspace_model

    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    model = build_workspace_model(path.resolve(), ProjectContext(root=path.resolve()))

    if fmt == "json":
        typer.echo(
            _json.dumps(
                {
                    "tool": "forge-doctor-data",
                    "schema_version": SCHEMA_VERSION,
                    "repositories": [
                        {
                            "name": r.name,
                            "path": r.path,
                            "markers": list(r.markers),
                            "languages": list(r.languages),
                        }
                        for r in model.repositories
                    ],
                    "links": [
                        {
                            "kind": lnk.kind.value,
                            "source_repo": lnk.source_repo,
                            "target_repo": lnk.target_repo,
                            "entity": lnk.entity_id,
                        }
                        for lnk in model.links
                    ],
                    "graph": {
                        "entities": [e.id for e in model.graph.entities()],
                        "relationships": [
                            {"src": r.src, "dst": r.dst, "kind": r.kind.value}
                            for r in model.graph.relationships()
                        ],
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console = Console()
    table = Table(title=f"WorkspaceModel: {path.resolve()}", title_justify="left")
    table.add_column("Repository", style="bold")
    table.add_column("Path")
    table.add_column("Languages")
    table.add_column("Markers", style="dim")
    for r in model.repositories:
        table.add_row(r.name, r.path, ", ".join(r.languages) or "-", ", ".join(r.markers) or "-")
    console.print(table)

    links = Table(title="Cross-repo links", title_justify="left")
    links.add_column("Repo")
    links.add_column("Edge")
    links.add_column("Entity")
    links.add_column("Defined in", style="dim")
    for lnk in model.links:
        links.add_row(
            lnk.source_repo,
            lnk.kind.value,
            lnk.entity_id,
            lnk.target_repo or "(external)",
        )
    if not model.links:
        links.add_row("-", "-", "no cross-repo links", "-")
    console.print(links)
    s = model.summary()
    console.print(
        f"\n[bold]{s['repositories']} repositories[/bold] - {s['entities']} entities, "
        f"{s['relationships']} relationships, {s['cross_repo_links']} cross-repo links"
    )


@workspace_app.command(name="diff")
def workspace_diff(
    range_spec: Annotated[str, typer.Argument(help="'base...head' git range.")],
    path: Annotated[Path, typer.Option("--path", help="Repo/workspace root.")] = Path("."),
) -> None:
    """Per-subproject finding diff across a git range."""
    if "..." not in range_spec:
        _stderr.print("[red]Expected a 'base...head' range.[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    base, _, head = range_spec.partition("...")
    projects = _workspace_projects(path)
    prefix = {rel.as_posix(): name for name, rel in projects}

    def _project_of(file: str | None) -> str:
        if not file:
            return "(root)"
        for rel, name in prefix.items():
            if file.startswith(rel + "/") or file == rel:
                return name
        return "(root)"

    old_items = _scan_git_ref(path, base) or {}
    new_items = _scan_git_ref(path, head) or {}
    added = new_items.keys() - old_items.keys()
    removed = old_items.keys() - new_items.keys()
    console = Console()
    table = Table(title=f"workspace diff {base} -> {head}", title_justify="left")
    table.add_column("Project", style="bold")
    table.add_column("+", justify="right")
    table.add_column("-", justify="right")
    per_proj: dict[str, list[int]] = {}
    for fp in added:
        file = new_items[fp].get("file")
        proj = _project_of(file if isinstance(file, str) else None)
        per_proj.setdefault(proj, [0, 0])[0] += 1
    for fp in removed:
        file = old_items[fp].get("file")
        proj = _project_of(file if isinstance(file, str) else None)
        per_proj.setdefault(proj, [0, 0])[1] += 1
    for proj, (a, r) in sorted(per_proj.items()):
        table.add_row(proj, f"[magenta]+{a}[/magenta]", f"[green]-{r}[/green]")
    if not per_proj:
        table.add_row("(all)", "0", "0")
    console.print(table)
    raise typer.Exit(1 if added else 0)
