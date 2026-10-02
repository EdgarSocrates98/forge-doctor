"""``forge-doctor plugins`` - inspect and validate external plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _build_registry, _stderr
from forge_doctor.core.config import ForgeDoctorConfig, PluginRules
from forge_doctor.output.summary import INTERNAL_ERROR_EXIT
from forge_doctor.plugins.discovery import load_plugins
from forge_doctor.plugins.manager import (
    install_plan,
    install_plugin,
    lock_plugins,
    scaffold_plugin,
    verify_plugins,
)

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
    _checks, infos, errors = load_plugins(
        trusted=rules.trusted, allow=rules.allow, strict=rules.mode == "strict"
    )
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
    checks, infos, errors = load_plugins(
        trusted=rules.trusted, allow=rules.allow, strict=rules.mode == "strict"
    )
    console = Console()
    console.print(_plugins_table(rules))
    problems = [i for i in infos if i.status and "untrusted" not in i.status] + errors
    console.print(f"\n{len(checks)} plugin check(s) loaded, {len(problems)} problem(s).")
    raise typer.Exit(1 if problems else 0)


@plugins_app.command(name="doctor")
def plugins_doctor() -> None:
    """Per-plugin health: entry point resolves, api_version supported."""
    rules = _cwd_plugin_rules()
    checks, infos, errors = load_plugins(
        trusted=rules.trusted, allow=rules.allow, strict=rules.mode == "strict"
    )
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


@plugins_app.command(name="init")
def plugins_init(
    name: Annotated[str, typer.Argument(help="Distribution name, e.g. forge-doctor-snowflake.")],
    dest: Annotated[Path, typer.Option("--dest", help="Parent directory.")] = Path("."),
) -> None:
    """Scaffold a plugin package (pyproject + check + test) under dest/name."""
    console = Console()
    try:
        written = scaffold_plugin(name, dest)
    except (ValueError, FileExistsError) as exc:
        _stderr.print(f"[red]{exc}[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT) from exc
    console.print(f"[green]{name}[/green] scaffolded:")
    for path in written:
        console.print(f"  {path}")
    console.print(
        "\n[dim]Install editable with pipx inject forge-doctor ./<dir> "
        "or pip install -e <dir>[/dim]"
    )


@plugins_app.command(name="lock")
def plugins_lock(
    path: Annotated[Path, typer.Option("--path", help="Project root.")] = Path("."),
) -> None:
    """Pin every installed plugin's content digest to .forge-doctor/plugins.lock."""
    pins = lock_plugins(path.resolve())
    console = Console()
    if not pins:
        console.print("[dim]No plugins installed - empty lock written.[/dim]")
        return
    for pin in pins:
        console.print(f"  {pin.name} {pin.version or ''} sha256:{pin.digest[:12]}...")
    console.print(f"[green]{len(pins)} plugin(s) locked[/green]")


@plugins_app.command(name="verify")
def plugins_verify(
    path: Annotated[Path, typer.Option("--path", help="Project root.")] = Path("."),
) -> None:
    """Verify installed plugins against .forge-doctor/plugins.lock."""
    results = verify_plugins(path.resolve())
    console = Console()
    if not results:
        console.print("[dim]No lock and no plugins - nothing to verify.[/dim]")
        return
    bad = 0
    style = {"ok": "green", "changed": "red", "missing": "red", "added": "yellow"}
    for r in results:
        if r.status != "ok":
            bad += 1
        suffix = f" - {r.detail}" if r.detail else ""
        console.print(f"  [{style[r.status]}]{r.status}[/{style[r.status]}] {r.name}{suffix}")
    console.print(f"\n{len(results) - bad} ok, {bad} problem(s).")
    raise typer.Exit(1 if bad else 0)


@plugins_app.command(name="install")
def plugins_install(
    dist: Annotated[str, typer.Argument(help="Distribution, e.g. forge-doctor-snowflake.")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the command only.")] = False,
) -> None:
    """Install a plugin via pipx inject (or pip), then validate loading."""
    console = Console()
    if dry_run:
        console.print(" ".join(install_plan(dist)))
        return
    cmd, code = install_plugin(dist)
    if code != 0:
        _stderr.print(f"[red]{' '.join(cmd)} failed ({code})[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    console.print(f"[green]{dist} installed[/green] - validating...")
    checks, infos, errors = load_plugins()
    mine = [i for i in infos if i.distribution == dist]
    for info in mine:
        state = "[green]ok[/green]" if info.status is None else f"[red]{info.status}[/red]"
        console.print(f"  {info.name}: api {info.api_version} - {state}")
    for error in errors:
        console.print(f"  [red]{error}[/red]")
    console.print(f"{len(checks)} plugin check(s) now loadable.")
