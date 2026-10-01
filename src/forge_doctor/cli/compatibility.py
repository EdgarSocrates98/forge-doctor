"""``compatibility`` and the ``migrate`` group - runtime/migration intel."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from forge_doctor.cli.app import app
from forge_doctor.cli.common import PathArg, _stderr
from forge_doctor.core.compat import detect_environment, migration_risks
from forge_doctor.core.context import ProjectContext
from forge_doctor.output.summary import INTERNAL_ERROR_EXIT


@app.command(name="compatibility")
def compatibility_cmd(
    path: PathArg = Path("."),
    source: Annotated[str | None, typer.Option("--from", help="Source Glue version.")] = None,
    target: Annotated[str | None, typer.Option("--to", help="Target Glue version.")] = None,
) -> None:
    """Detect runtimes and show Glue migration risks (knowledge packs)."""
    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    console = Console()
    env = detect_environment(ProjectContext(root=path))

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    grid.add_column(style="dim")
    for label, value in (
        ("Glue", env.glue),
        ("Spark", env.spark),
        ("Python", env.python),
        ("Java", env.java),
        ("Iceberg", env.iceberg),
    ):
        grid.add_row(label, value or "-", env.sources.get(label.lower(), ""))
    console.print()
    console.print(Panel(grid, title="[bold]Detected environment[/bold]", expand=False))

    src, dst, changes = migration_risks(source or env.glue, target)
    table = Table(title=f"Migration risks  {src} -> {dst}", title_justify="left")
    table.add_column("Severity", style="bold")
    table.add_column("Change")
    table.add_column("Detail", style="dim")
    styles = {"HIGH": "red", "MEDIUM": "yellow", "INFO": "blue"}
    if not changes:
        table.add_row("-", "No migration changes recorded.", "")
    for change in changes:
        sev = str(change.get("severity", "INFO"))
        table.add_row(
            f"[{styles.get(sev, 'white')}]{sev}[/]",
            str(change.get("change", "")),
            str(change.get("detail", "")),
        )
    console.print(table)
    console.print()


migrate_app = typer.Typer(name="migrate", help="Migration intelligence.")
app.add_typer(migrate_app, name="migrate")


@migrate_app.command(name="glue")
def migrate_glue(
    path: PathArg = Path("."),
    source: Annotated[str | None, typer.Option("--from", help="Source Glue version.")] = None,
    target: Annotated[str | None, typer.Option("--to", help="Target Glue version.")] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Glue migration report: knowledge changes + this project's real signals."""
    import json as _json

    from forge_doctor.analyzers.hcl_lite import project_iac

    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    ctx = ProjectContext(root=path)
    env = detect_environment(ctx)
    src, dst, changes = migration_risks(source or env.glue, target)

    groups: dict[str, list[dict[str, str]]] = {
        "Runtime": [],
        "Code": [],
        "Dependencies": [],
        "Infrastructure": [],
    }

    # Knowledge-pack changes -> Runtime.
    for change in changes:
        groups["Runtime"].append(
            {
                "severity": str(change.get("severity", "INFO")),
                "detail": f"{change.get('change')} - {change.get('detail', '')}",
                "location": "knowledge",
            }
        )

    # Real signals: glue_version pins in code.
    from forge_doctor.checks.glue import _VERSION_PREFIX, analyze_project

    buckets = analyze_project(ctx)
    for pattern, occs in buckets.items():
        if pattern == "dynamic_frame":
            for file, line in occs:
                groups["Code"].append(
                    {
                        "severity": "HIGH",
                        "detail": "DynamicFrame in use - migrate to DataFrame "
                        "API (Glue 4.0+ defaults)",
                        "location": f"{file.as_posix()}:{line}",
                    }
                )
        elif pattern.startswith(_VERSION_PREFIX):
            version = pattern.removeprefix(_VERSION_PREFIX)
            for file, line in occs:
                groups["Runtime"].append(
                    {
                        "severity": "INFO",
                        "detail": f"glue_version pin {version} in code",
                        "location": f"{file.as_posix()}:{line}",
                    }
                )

    # IaC pins -> Infrastructure.
    for resource in project_iac(ctx.files, ctx.root):
        if resource.type == "aws_glue_job":
            tf_version = resource.attrs.get("glue_version")
            if tf_version:
                groups["Infrastructure"].append(
                    {
                        "severity": "INFO",
                        "detail": f"terraform aws_glue_job {resource.name} pins "
                        f"glue_version {tf_version}",
                        "location": f"{resource.file}:{resource.line}",
                    }
                )
        elif resource.type == "AWS::Glue::Job":
            cfn_version = resource.attrs.get("GlueVersion")
            if cfn_version:
                groups["Infrastructure"].append(
                    {
                        "severity": "INFO",
                        "detail": f"CFN AWS::Glue::Job {resource.name} pins "
                        f"GlueVersion {cfn_version}",
                        "location": f"{resource.file}:{resource.line}",
                    }
                )

    # Dependencies -> Dependencies.
    project = (ctx.pyproject or {}).get("project", {})
    for dep in project.get("dependencies", []):
        dep_str = str(dep)
        if "pyspark" in dep_str.lower():
            groups["Dependencies"].append(
                {
                    "severity": "MEDIUM",
                    "detail": f"pyspark dependency '{dep_str}' - verify target Spark compatibility",
                    "location": "pyproject.toml",
                }
            )
    if project.get("requires-python"):
        groups["Dependencies"].append(
            {
                "severity": "INFO",
                "detail": f"requires-python {project['requires-python']}",
                "location": "pyproject.toml",
            }
        )
    if env.iceberg:
        groups["Dependencies"].append(
            {
                "severity": "MEDIUM",
                "detail": f"Iceberg {env.iceberg} detected - confirm target bundle",
                "location": env.sources.get("iceberg", "deps"),
            }
        )

    if fmt == "json":
        typer.echo(
            _json.dumps({"from": src, "to": dst, "groups": groups}, indent=2, ensure_ascii=False)
        )
        return

    console = Console()
    console.print(
        f"\n[bold]Glue migration[/bold] {src} -> {dst}  "
        f"[dim](detected: glue={env.glue or '-'}, spark={env.spark or '-'}, "
        f"python={env.python or '-'})[/dim]\n"
    )
    styles = {"HIGH": "red", "MEDIUM": "yellow", "INFO": "blue"}
    for group, items in groups.items():
        console.print(f"[bold]{group}[/bold]")
        if not items:
            console.print("  [dim]no signals[/dim]")
        for item in items:
            sev = item["severity"]
            console.print(
                f"  [{styles.get(sev, 'white')}]{sev:<6}[/] {item['detail']} "
                f"[dim]({item['location']})[/dim]"
            )
        console.print()
    raise typer.Exit(0)
