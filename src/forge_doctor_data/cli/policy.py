"""``forge-doctor-data policy`` - organization policy pack commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor_data.api import SCHEMA_VERSION
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _build_registry, _stderr
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.policy_pack import (
    PolicyPackError,
    evaluate_packs,
    load_pack,
    load_packs,
)
from forge_doctor_data.output.summary import INTERNAL_ERROR_EXIT

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
            "[dim]No policy packs discovered (.forge-doctor-data/policy/, "
            "policy.yml, org-policy.yml, or [tool.forge-doctor-data] policy_packs).[/dim]"
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

        from forge_doctor_data.output.json_renderer import result_to_dict

        typer.echo(
            _json.dumps(
                {
                    "tool": "forge-doctor-data",
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


@policy_app.command(name="report")
def policy_report(
    path: Annotated[Path, typer.Option("--path", help="Project root.")] = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Compliance report: packs, violations by rule, suppression audit."""
    import json as _json
    from collections import Counter
    from datetime import date

    from forge_doctor_data.core.policy import suppression_statuses
    from forge_doctor_data.core.runner import CheckRunner
    from forge_doctor_data.output.json_renderer import result_to_dict

    ctx = ProjectContext(root=path.resolve())
    packs, errors = load_packs(ctx.root, ctx.config.policy_packs)
    violations = list(errors)
    violations.extend(evaluate_packs(ctx, packs))

    # Suppression matching needs the real findings stream.
    registry, _ = _build_registry(no_plugins=True)
    report = CheckRunner(registry).run(ctx)
    statuses = suppression_statuses(ctx.config.suppressions, report.results, date.today())

    by_rule = Counter(v.check_id for v in violations)
    status_counts = Counter(s.status for s in statuses)
    unapproved = [s for s in statuses if not s.suppression.approved_by]

    if fmt == "json":
        typer.echo(
            _json.dumps(
                {
                    "tool": "forge-doctor-data",
                    "schema_version": SCHEMA_VERSION,
                    "packs": [
                        {
                            "name": p.name,
                            "version": p.version,
                            "rules": len(p.rules),
                            "extends": list(p.extends),
                            "require_approval": p.require_approval,
                            "path": str(p.path),
                        }
                        for p in packs
                    ],
                    "violations": {
                        "total": len(violations),
                        "by_rule": dict(sorted(by_rule.items())),
                        "findings": [result_to_dict(v) for v in violations],
                    },
                    "suppressions": {
                        "total": len(statuses),
                        "by_status": dict(sorted(status_counts.items())),
                        "unapproved": [
                            {"rule": s.suppression.rule, "path": s.suppression.path}
                            for s in unapproved
                        ],
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console = Console()
    console.print(f"[bold]Packs:[/bold] {len(packs)} loaded, {len(errors)} invalid")
    for pack in packs:
        flags = []
        if pack.extends:
            flags.append(f"extends {', '.join(pack.extends)}")
        if pack.require_approval:
            flags.append("requires approval")
        suffix = f" [dim]({'; '.join(flags)})[/dim]" if flags else ""
        console.print(f"  {pack.name} v{pack.version}: {len(pack.rules)} rules{suffix}")
    console.print(f"[bold]Violations:[/bold] {len(violations)}")
    for rule_id, count in sorted(by_rule.items()):
        console.print(f"  {rule_id}: {count}")
    console.print(
        f"[bold]Suppressions:[/bold] {len(statuses)} "
        f"({status_counts.get('active', 0)} active, "
        f"{status_counts.get('expired', 0)} expired, "
        f"{status_counts.get('unused', 0)} unused; "
        f"{len(unapproved)} without approved_by)"
    )
    if errors or violations:
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
