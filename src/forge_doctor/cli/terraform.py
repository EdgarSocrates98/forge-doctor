"""`forge-doctor terraform` - model-driven Terraform inspection."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.analyzers.terraform_model import TerraformProjectModel, terraform_model
from forge_doctor.checks.terraform import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr, render_findings
from forge_doctor.core.context import ProjectContext

terraform_app = typer.Typer(name="terraform", help="Terraform intelligence: inspect.")
app.add_typer(terraform_app, name="terraform")

_PathOpt = Annotated[Path, typer.Argument(help="Project root.")]

_DOMAIN_HINTS = {
    "aws_": "AWS",
    "azurerm_": "Azure",
    "google_": "GCP",
    "databricks_": "Databricks",
    "kubernetes_": "Kubernetes",
    "random_": "random",
    "null_": "null",
}


def _model_at(path: Path) -> tuple[ProjectContext, TerraformProjectModel]:
    ctx = ProjectContext(root=path.resolve())
    return ctx, terraform_model(ctx)


def _domain_of(resource_type: str) -> str:
    for prefix, label in _DOMAIN_HINTS.items():
        if resource_type.startswith(prefix):
            return label
    return resource_type.split("_", 1)[0].capitalize()


@terraform_app.command(name="inspect")
def terraform_inspect(
    path: _PathOpt = Path("."),
) -> None:
    """Summarize the project's Terraform surface from the semantic model."""
    ctx, model = _model_at(path)
    console = Console()
    console.print()
    console.print("[bold]Terraform project[/bold]")

    if not model.has_terraform:
        console.print("  no Terraform files detected")
        return

    if model.required_version:
        console.print(f"\n[bold]Terraform[/bold]\n  required version: {model.required_version}")

    provider_names = sorted(
        {b.labels[0] for b in model.providers if b.labels} | set(model.required_providers)
    )
    if provider_names:
        console.print("\n[bold]Providers[/bold]")
        reqs = model.required_providers
        for name in provider_names:
            version = str(reqs[name].attrs.get("version", "")) if name in reqs else ""
            aliases = sorted(
                b.attrs.get("alias", "")
                for b in model.providers
                if b.labels and b.labels[0] == name and b.attrs.get("alias")
            )
            suffix = f"  {version}" if version else ""
            if aliases:
                suffix += f"  aliases: {', '.join(aliases)}"
            console.print(f"  {name}{suffix}")

    if model.resources:
        console.print()
        table = Table(title="Resources", title_justify="left", show_edge=False, pad_edge=False)
        table.add_column("domain", style="bold", min_width=14)
        table.add_column("count", justify="right")
        counts = Counter(_domain_of(b.labels[0]) for b in model.resources)
        for domain, count in sorted(counts.items()):
            table.add_row(domain, str(count))
        console.print(table)

    if model.modules:
        local = sum(
            1 for b in model.modules if str(b.attrs.get("source", "")).startswith(("./", "../"))
        )
        remote = len(model.modules) - local
        console.print(f"[bold]Modules[/bold]\n  {local} local\n  {remote} remote")

    console.print(f"\n[bold]State[/bold]\n  backend: {model.backend or 'default (local)'}")

    console.print(f"[bold]References[/bold]\n  {len(model.edges)} edges")

    render_findings(console, ctx, CHECKS, title="Findings")
    console.print()


@terraform_app.callback(invoke_without_command=True)
def _terraform_default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _stderr.print("use `forge-doctor terraform inspect`")
        raise typer.Exit(2)
