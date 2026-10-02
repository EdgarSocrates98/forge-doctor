"""``forge-doctor diff`` - compare findings between refs or saved reports."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from forge_doctor.api import SCHEMA_VERSION
from forge_doctor.cli.app import app
from forge_doctor.cli.common import (
    _ref_worktree,
    _resolve_diff_side,
    _scan_items,
    _stderr,
)
from forge_doctor.core.context import ProjectContext
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
    semantic: Annotated[
        bool,
        typer.Option("--semantic", help="Entity-level diff + blast radius (git refs only)."),
    ] = False,
    fmt: Annotated[
        str, typer.Option("--format", "-f", help="text|json (semantic diff only)")
    ] = "text",
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

    if semantic:
        _semantic_diff(old, new, path, fmt)
        return
    if fmt != "text":
        _stderr.print("[red]--format is only supported with --semantic.[/red]")
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


def _semantic_diff(base_ref: str, head_ref: str, repo: Path, fmt: str = "text") -> None:
    """Entity-level diff between two git refs: changes, blast radius, risk."""
    import subprocess

    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.semantic_diff import RISK_HIGH, diff_graphs

    names = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", base_ref, head_ref],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )
    if names.returncode != 0:
        _stderr.print(f"[red]git diff failed:[/red] {names.stderr.strip()}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    changed = frozenset(n.strip() for n in names.stdout.splitlines() if n.strip())

    console = Console()
    with _ref_worktree(repo, base_ref) as base_dir, _ref_worktree(repo, head_ref) as head_dir:
        if base_dir is None or head_dir is None:
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        base_g = build_platform_graph(ProjectContext(root=base_dir))
        head_g = build_platform_graph(ProjectContext(root=head_dir))
        base_items = _scan_items(base_dir)
        head_items = _scan_items(head_dir)

    diff = diff_graphs(base_g, head_g, changed)
    added = len(set(head_items) - set(base_items))
    removed = len(set(base_items) - set(head_items))

    from forge_doctor.core.change_intel import analyze_change

    intel = analyze_change(base_g, head_g)

    if fmt == "json":
        import dataclasses
        import json as _json

        typer.echo(
            _json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "base": base_ref,
                    "head": head_ref,
                    "risk": diff.risk,
                    "reasons": list(diff.reasons),
                    "changed_files": list(diff.changed_files),
                    "changes": [dataclasses.asdict(c) for c in diff.changes],
                    "unmapped_files": list(diff.unmapped_files),
                    "findings_delta": {"added": added, "removed": removed},
                    "contract_changes": [
                        {
                            "check_id": "DCTR004",
                            "entity": c.entity_id,
                            "breaking": list(c.breaking),
                            "impacted": list(c.impacted),
                        }
                        for c in diff.changes
                        if c.breaking
                    ],
                    "capabilities": [
                        {
                            "capability": t.capability,
                            "platform": t.platform,
                            "from": t.before,
                            "to": t.after,
                        }
                        for t in intel.capability_transitions
                    ],
                    "migration_requirements": [
                        {
                            "entity": r.move.entity_id,
                            "attr": r.move.attr,
                            "from": r.move.from_version,
                            "to": r.move.to_version,
                            "status": r.status,
                            "required_changes": list(r.required_changes),
                            "blockers": list(r.blockers),
                        }
                        for r in intel.migration_requirements
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        raise typer.Exit(1 if added or diff.risk == RISK_HIGH else 0)

    style = {RISK_HIGH: "red", "medium": "yellow", "low": "green"}[diff.risk]
    console.print(
        Panel(
            f"[{style}]risk: {diff.risk.upper()}[/{style}]   "
            f"{len(changed)} files changed   "
            f"[magenta]+{added}[/magenta] new findings   "
            f"[green]-{removed}[/green] fixed",
            title=f"[bold]semantic diff[/bold]  {base_ref} -> {head_ref}",
            expand=False,
        )
    )
    for reason in diff.reasons:
        console.print(f"  [dim]-[/dim] {reason}")

    if diff.changes:
        table = Table(title="Entity changes", title_justify="left")
        table.add_column("Change")
        table.add_column("Entity")
        table.add_column("Attrs", style="dim")
        table.add_column("Impacts", justify="right")
        color = {"added": "green", "removed": "red", "modified": "yellow", "touched": "dim"}
        for c in diff.changes:
            table.add_row(
                f"[{color.get(c.change, 'white')}]{c.change}[/]",
                c.entity_id,
                ", ".join(c.attr_diffs) or "-",
                str(len(c.impacted)) if c.impacted else "-",
            )
        console.print(table)
        breaking = [c for c in diff.changes if c.breaking]
        if breaking:
            console.print("[bold]Breaking contract changes[/bold] (DCTR004):")
            for c in breaking:
                console.print(f"  [red]![/red] {c.entity_id}: {'; '.join(c.breaking)}")
        impacted = diff.impacted_entities
        if impacted:
            console.print("[bold]Blast radius[/bold] (depends on changed entities):")
            for eid in impacted:
                console.print(f"  [dim]->[/dim] {eid}")
    if intel.capability_transitions:
        ctable = Table(title="Capability transitions", title_justify="left")
        ctable.add_column("Capability", style="bold")
        ctable.add_column("Platform")
        ctable.add_column("Was")
        ctable.add_column("Now")
        for t in intel.capability_transitions:
            ctable.add_row(t.capability, t.platform, t.before, t.after)
        console.print(ctable)
    if intel.migration_requirements:
        console.print("[bold]Migration requirements[/bold]")
        for req in intel.migration_requirements:
            m = req.move
            console.print(
                f"  {m.entity_id}: {m.attr} {m.from_version} -> {m.to_version} "
                f"[dim]({req.status})[/dim]"
            )
            for chg in req.required_changes[:5]:
                console.print(f"    [dim]- {chg}[/dim]")
            for blocker in req.blockers:
                console.print(f"    [red]BLOCKER: {blocker}[/red]")
    if diff.unmapped_files:
        console.print(
            f"[dim]{len(diff.unmapped_files)} changed file(s) map to no platform entity[/dim]"
        )
    raise typer.Exit(1 if added or diff.risk == RISK_HIGH else 0)
