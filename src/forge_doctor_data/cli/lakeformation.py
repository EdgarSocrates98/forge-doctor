"""`forge-doctor-data lakeformation` - deep governance and cross-account intelligence."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor_data.analyzers.lakeformation_model import (
    LakeFormationProjectModel,
    lakeformation_model,
)
from forge_doctor_data.checks.lakeformation import CHECKS
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import render_findings as _render_findings
from forge_doctor_data.core.context import ProjectContext

lakeformation_app = typer.Typer(
    name="lakeformation", help="Lake Formation governance intelligence."
)
app.add_typer(lakeformation_app, name="lakeformation")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]


def _model_at(path: Path) -> tuple[ProjectContext, LakeFormationProjectModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, lakeformation_model(ctx)


@lakeformation_app.command(name="inspect")
def lf_inspect(path: _PathOpt = Path(".")) -> None:
    """Census: databases, tables, locations, tags, filters, links, shares."""
    _ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Lake Formation[/bold]")
    if not model.has_lakeformation:
        console.print("  no lake formation governance detected")
        return
    console.print(f"  databases       {len(model.databases)}")
    for db in model.databases[:8]:
        console.print(f"    [dim]{db}[/dim]")
    console.print(f"  tables          {len(model.tables)}")
    console.print(f"  grants          {len(model.grants)}")
    console.print(f"  principals      {len(model.principals)}")
    console.print(f"  admins          {len(model.admins)}")
    console.print(f"  data locations  {len(model.data_locations)}")
    console.print(f"  resource links  {len(model.resource_links)}")
    console.print(f"  lf tags         {len(model.lf_tags)}")
    console.print(f"  cells filters   {len(model.filters)}")
    console.print(f"  ram shares      {len(model.ram_shares)}")
    flags = [
        f"FGAC={'on' if model.fgac else 'off'}",
        f"LF-TBAC={'on' if model.fta else 'off'}",
        f"hybrid={'on' if model.hybrid_access else 'off'}",
    ]
    console.print(f"  flags           {' '.join(flags)}")
    if model.external_accounts:
        console.print(f"  external accts  {', '.join(sorted(model.external_accounts))}")


@lakeformation_app.command(name="permissions")
def lf_permissions(path: _PathOpt = Path(".")) -> None:
    """Grant table: principal x permissions x resource."""
    _ctx, model = _model_at(path)
    console = Console()
    if not model.grants:
        console.print("no lake formation grants detected")
        return
    table = Table()
    table.add_column("principal", style="bold")
    table.add_column("permissions")
    table.add_column("resource")
    table.add_column("source")
    table.add_column("flags")
    for g in model.grants:
        flags = []
        if g.cross_account:
            flags.append("cross-account")
        if g.grant_option:
            flags.append("grant-option")
        table.add_row(
            g.principal[:52],
            ", ".join(g.permissions) or "-",
            f"{g.resource_kind}:{g.resource_name}"[:52],
            g.source,
            ", ".join(flags),
        )
    console.print(table)


@lakeformation_app.command(name="graph")
def lf_graph(path: _PathOpt = Path(".")) -> None:
    """Governance graph view: principal -> resource edges."""
    _ctx, model = _model_at(path)
    console = Console()
    if not model.grants and not model.admins:
        console.print("no governance edges detected")
        return
    console.print()
    console.print("[bold]Governance edges[/bold]")
    for a in model.admins:
        console.print(f"  [bold]{a.arn}[/bold] --admin--> catalog @ {a.file.as_posix()}:{a.line}")
    for g in model.grants:
        perms = "+".join(g.permissions) or "-"
        tag = " [cyan](x-acct)[/cyan]" if g.cross_account else ""
        console.print(
            f"  [bold]{g.principal[:44]}[/bold] --{perms}--> "
            f"{g.resource_kind}:{g.resource_name or '?'}{tag}"
        )


@lakeformation_app.command(name="cross-account")
def lf_cross_account(path: _PathOpt = Path(".")) -> None:
    """Producer/consumer view: external accounts, RAM shares, links."""
    _ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Cross-account paths[/bold]")
    if not (model.external_accounts or model.resource_links or model.ram_shares):
        console.print("  no cross-account evidence detected")
        return
    for link in model.resource_links:
        console.print(
            f"  link [bold]{link.name}[/bold] -> {link.target_database} "
            f"@{link.target_catalog or '?'} [dim]({link.file.as_posix()}:{link.line})[/dim]"
        )
    for share in model.ram_shares:
        console.print(f"  ram share [bold]{share.name}[/bold]")
    for acct in sorted(model.external_accounts):
        grants = [g for g in model.grants if acct in g.principal]
        perms = ", ".join(sorted({p for g in grants for p in g.permissions})) or "none in repo"
        ram = "RAM principal" if acct in model.ram_principals else "no RAM association"
        console.print(f"  account {acct}: grants=({perms}) {ram}")


@lakeformation_app.command(name="compatibility")
def lf_compatibility(path: _PathOpt = Path(".")) -> None:
    """Engine x FGAC/FTA capability report (knowledge-pack driven)."""
    ctx, model = _model_at(path)
    console = Console()
    registry = ctx.capabilities
    observed = sorted(
        {
            g.resource_kind
            for g in model.grants
            if g.resource_kind in ("columns", "data_cells_filter", "lf_tag", "lf_tag_expression")
        }
        | ({"columns"} if model.fgac else set())
        | ({"lf_tag"} if model.fta else set())
    )
    console.print()
    console.print("[bold]Lake Formation compatibility[/bold]")
    if not observed:
        console.print("  no FGAC/FTA usage detected - catalog governance only")
    # Version matrix per engine: evaluate each declared pack version so the
    # answer is "status per engine version" rather than UNKNOWN-with-no-version.
    engines = {
        "glue": ("3.0", "4.0", "5.0"),
        "athena": ("2", "3"),
        "emr": ("5.31", "5.32", "6.0", "7.0"),
    }
    if observed:
        for platform, versions in engines.items():
            cells = []
            for version in versions:
                result = registry.supports(platform, "LAKEFORMATION_FGAC", version=version)
                cells.append(f"v{version}={result.status.value}")
            console.print(
                f"  {platform:8s} FGAC: {' '.join(cells)}  [dim]{result.reason[:60]}[/dim]"
            )
    variants: list[tuple[str, str, str, str | None]] = [
        ("FTA", "LAKEFORMATION_FTA", "athena", "3"),
        ("x-acct", "LAKEFORMATION_CROSS_ACCOUNT", "lakeformation", None),
        ("hybrid", "LAKEFORMATION_HYBRID_ACCESS", "lakeformation", None),
    ]
    for label, cap, plat, ver in variants:
        kwargs = {"version": ver} if ver else {}
        result = registry.supports(plat, cap, **kwargs)
        console.print(
            f"  {label:8s} {cap}: {result.status.value.upper():12s} [dim]{result.reason[:70]}[/dim]"
        )


@lakeformation_app.command(name="findings")
def lf_findings(path: _PathOpt = Path(".")) -> None:
    """Run LF### checks against the project."""
    ctx = ProjectContext(root=path.resolve())
    console = Console()
    _render_findings(console, ctx, CHECKS, title="Lake Formation findings")
