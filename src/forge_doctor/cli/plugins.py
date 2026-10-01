"""``forge-doctor plugins`` - inspect and validate external plugins."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _build_registry
from forge_doctor.core.config import ForgeDoctorConfig, PluginRules
from forge_doctor.plugins.discovery import load_plugins

plugins_app = typer.Typer(
    name="plugins", help="Inspect and validate external plugins.", no_args_is_help=False
)
app.add_typer(plugins_app, name="plugins")


def _cwd_plugin_rules() -> PluginRules:
    """Trust rules of the current directory's pyproject, if any."""
    import tomllib

    pyproject = Path.cwd() / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except OSError:
        return PluginRules()
    return ForgeDoctorConfig.from_pyproject(data).plugins


def _plugins_table(rules: PluginRules) -> Table:
    table = Table(title="External plugins", show_lines=False, title_justify="left")
    table.add_column("Entry point")
    table.add_column("Distribution")
    table.add_column("Version", justify="right")
    table.add_column("API", justify="right")
    table.add_column("Status")
    _checks, infos, errors = load_plugins(trusted=rules.trusted, allow=rules.allow)
    if not infos:
        table.add_row("[dim]None detected[/dim]", "", "", "", "")
    for info in infos:
        status = info.status or "[green]ok[/green]"
        if "untrusted" in (info.status or ""):
            status = f"[yellow]{info.status}[/yellow]"
        table.add_row(
            info.name,
            info.distribution or "",
            info.version or "",
            info.api_version,
            status,
        )
    for error in errors:
        table.add_row(error.split(":")[0], "", "", "", f"[red]{error}[/red]")
    return table


@plugins_app.callback(invoke_without_command=True)
def plugins_default(ctx: typer.Context) -> None:
    """List built-in categories and discovered external plugins."""
    if ctx.invoked_subcommand is not None:
        return
    registry, _ = _build_registry(config=_cwd_config())
    console = Console()

    table = Table(title="Built-in categories", show_lines=False, title_justify="left")
    table.add_column("Category", style="bold")
    table.add_column("Checks", justify="right")
    counts: dict[str, int] = {}
    for check in registry.all():
        counts[check.category] = counts.get(check.category, 0) + 1
    for category in registry.categories():
        table.add_row(category, str(counts.get(category, 0)))
    console.print(table)
    console.print(_plugins_table(_cwd_plugin_rules()))


def _cwd_config() -> ForgeDoctorConfig:
    import tomllib

    pyproject = Path.cwd() / "pyproject.toml"
    try:
        return ForgeDoctorConfig.from_pyproject(
            tomllib.loads(pyproject.read_text(encoding="utf-8"))
        )
    except OSError:
        return ForgeDoctorConfig()


@plugins_app.command(name="list")
def plugins_list() -> None:
    """List installed plugins with API version and load/trust status."""
    Console().print(_plugins_table(_cwd_plugin_rules()))


@plugins_app.command(name="validate")
def plugins_validate() -> None:
    """Fail when any installed plugin is incompatible or unloadable."""
    rules = _cwd_plugin_rules()
    checks, infos, errors = load_plugins(trusted=rules.trusted, allow=rules.allow)
    console = Console()
    console.print(_plugins_table(rules))
    problems = [i for i in infos if i.status and "untrusted" not in i.status] + errors
    console.print(f"\n{len(checks)} plugin check(s) loaded, {len(problems)} problem(s).")
    raise typer.Exit(1 if problems else 0)


@plugins_app.command(name="doctor")
def plugins_doctor() -> None:
    """Per-plugin health: entry point resolves, api_version supported."""
    rules = _cwd_plugin_rules()
    checks, infos, errors = load_plugins(trusted=rules.trusted, allow=rules.allow)
    console = Console()
    for info in infos:
        state = "[green]ok[/green]" if info.status is None else f"[red]{info.status}[/red]"
        console.print(f"  {info.name}: api {info.api_version} - {state}")
    for error in errors:
        console.print(f"  [red]{error}[/red]")
    if not infos and not errors:
        console.print("  [dim]no plugins installed[/dim]")
    console.print(f"\n{len(checks)} check(s) available from plugins.")
    raise typer.Exit(1 if errors or any(i.status for i in infos) else 0)
