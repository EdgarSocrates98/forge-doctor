"""``forge-doctor diff`` - compare findings between refs or saved reports."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _resolve_diff_side, _stderr
from forge_doctor.output.summary import INTERNAL_ERROR_EXIT


@app.command(name="diff")
def diff_cmd(
    old: Annotated[
        str,
        typer.Argument(help="Older report JSON, git ref, or 'base...head' range."),
    ],
    new: Annotated[
        str | None,
        typer.Argument(help="Newer report JSON or git ref."),
    ] = None,
    path: Annotated[Path, typer.Option("--path", help="Repo used to resolve git refs.")] = Path(
        "."
    ),
) -> None:
    """Diff findings: NEW in <new> vs <old>, or in a 'base...head' git range."""
    if new is None:
        if "..." not in old:
            _stderr.print("[red]Provide two sides or a 'base...head' range.[/red]")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        old, _, new = old.partition("...")
    if not old or not new:
        _stderr.print("[red]Empty diff side - expected 'base...head'.[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    old_side = _resolve_diff_side(old, path)
    new_side = _resolve_diff_side(new, path)
    if old_side is None or new_side is None:
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    old_label, old_items = old_side
    new_label, new_items = new_side
    if not old_items and not new_items:
        _stderr.print("[red]Both sides produced no findings.[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT)

    def _sort_key(item: dict[str, object]) -> tuple[str, str, int]:
        file = item.get("file")
        line = item.get("line")
        return (
            str(item.get("check_id", "")),
            file if isinstance(file, str) else "",
            line if isinstance(line, int) else 0,
        )

    added = sorted((new_items[fp] for fp in new_items.keys() - old_items.keys()), key=_sort_key)
    removed = sorted((old_items[fp] for fp in old_items.keys() - new_items.keys()), key=_sort_key)
    console = Console()
    console.print(
        Panel(
            f"[magenta]+{len(added)} new[/magenta]   "
            f"[green]-{len(removed)} fixed[/green]   "
            f"{len(old_items.keys() & new_items.keys())} pre-existing",
            title=f"[bold]diff[/bold]  {old_label} -> {new_label}",
            expand=False,
        )
    )
    for item in added:
        loc = item.get("file") or ""
        line = f":{item['line']}" if item.get("line") else ""
        console.print(f"  [magenta]+[/magenta] {item.get('check_id')} [dim]{loc}{line}[/dim]")
    for item in removed:
        loc = item.get("file") or ""
        line = f":{item['line']}" if item.get("line") else ""
        console.print(f"  [green]-[/green] {item.get('check_id')} [dim]{loc}{line}[/dim]")
    raise typer.Exit(1 if added else 0)
