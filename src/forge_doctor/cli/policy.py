"""``forge-doctor policy`` - organization policy pack commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.api import SCHEMA_VERSION
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.policy_pack import (
    PolicyPackError,
    evaluate_packs,
    load_pack,
    load_packs,
)
from forge_doctor.output.summary import INTERNAL_ERROR_EXIT

policy_app = typer.Typer(name="policy", help="Organization policy packs.")
app.add_typer(policy_app, name="policy")


@policy_app.command(name="list")
def policy_list(
    path: Annotated[Path, typer.Option("--path", help="Project root.")] = Path("."),
) -> None:
    """List discovered policy packs and their rules."""
    ctx = ProjectContext(root=path.resolve())
    packs, errors = load_packs(ctx.root, ctx.config.policy_packs)
    console = Console()
    if errors:
        for err in errors:
            console.print(f"[red]invalid pack:[/red] {err.message}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    if not packs:
        console.print(
            "[dim]No policy packs discovered (.forge-doctor/policy/, "
            "policy.yml, org-policy.yml, or [tool.forge-doctor] policy_packs).[/dim]"
        )
        return
    table = Table(title="Policy packs", title_justify="left")
    table.add_column("Pack", style="bold")
    table.add_column("Version")
    table.add_column("Rules", justify="right")
    table.add_column("Path", style="dim")
    for pack in packs:
        table.add_row(pack.name, pack.version, str(len(pack.rules)), str(pack.path))
    console.print(table)
    for pack in packs:
        rules = Table(title=pack.name, title_justify="left")
        rules.add_column("Id", style="dim")
        rules.add_column("Severity")
        rules.add_column("Kind")
        rules.add_column("Message")
        for rule in pack.rules:
            kind = "forbid" if rule.forbid else "require"
            rules.add_row(rule.id, rule.severity, kind, rule.message[:60])
        console.print(rules)


@policy_app.command(name="eval")
def policy_eval(
    path: Annotated[Path, typer.Option("--path", help="Project root.")] = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Evaluate policy packs and print violations (exit 1 on errors/violations)."""
    ctx = ProjectContext(root=path.resolve())
    packs, errors = load_packs(ctx.root, ctx.config.policy_packs)
    results = list(errors)
    results.extend(evaluate_packs(ctx, packs))

    if fmt == "json":
        import json as _json

        from forge_doctor.output.json_renderer import result_to_dict

        typer.echo(
            _json.dumps(
                {
                    "tool": "forge-doctor",
                    "schema_version": SCHEMA_VERSION,
                    "packs": [
                        {"name": p.name, "version": p.version, "rules": len(p.rules)} for p in packs
                    ],
                    "findings": [result_to_dict(r) for r in results],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        raise typer.Exit(1 if results else 0)

    console = Console()
    if not packs and not errors:
        console.print("[dim]No policy packs discovered.[/dim]")
        raise typer.Exit(0)
    if not results:
        console.print(f"[green]{sum(len(p.rules) for p in packs)} rules, no violations[/green]")
        raise typer.Exit(0)
    table = Table(title="Policy violations", title_justify="left")
    table.add_column("Id", style="dim")
    table.add_column("Severity")
    table.add_column("Finding")
    table.add_column("Location", style="dim")
    style = {"error": "red", "warning": "yellow", "info": "blue"}
    for r in results:
        table.add_row(
            r.check_id,
            f"[{style.get(r.severity.value, 'white')}]{r.severity.value}[/]",
            r.message[:70],
            f"{r.file.as_posix()}:{r.line}"
            if r.file and r.line
            else (r.file.as_posix() if r.file else "-"),
        )
    console.print(table)
    raise typer.Exit(1)


@policy_app.command(name="validate")
def policy_validate(
    pack: Annotated[Path, typer.Argument(help="Policy pack file to validate.")],
) -> None:
    """Lint a policy pack file: schema, duplicate ids, regexes, severities."""
    if not pack.is_file():
        _stderr.print(f"[red]Not a file:[/red] {pack}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    console = Console()
    try:
        loaded = load_pack(pack)
    except PolicyPackError as exc:
        console.print(f"[red]invalid:[/red] {exc}")
        raise typer.Exit(1) from None
    console.print(
        f"[green]{pack.name}[/green]: pack '{loaded.name}' v{loaded.version}, "
        f"{len(loaded.rules)} rules valid"
    )
