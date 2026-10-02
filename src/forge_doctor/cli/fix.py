"""``forge-doctor fix`` - safety-classified deterministic fix proposals.

Dry-run by default: prints unified diffs for ``safe``/``review``
actions and guidance for ``manual`` ones. ``--apply`` writes only
``safe`` transforms; ``--apply --class review`` also applies
``review``. ``manual`` never applies. Writes stay inside the scanned
root; no git operations are performed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.cli.app import app
from forge_doctor.cli.common import _build_registry
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.fixes import (
    MANUAL,
    REVIEW,
    SAFE,
    FixAction,
    FixRefused,
    apply_fix,
    plan_fixes,
)
from forge_doctor.core.runner import CheckRunner

console = Console()


@app.command(name="fix")
def fix(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
    apply_: Annotated[
        bool,
        typer.Option("--apply", help="Write SAFE transforms to disk."),
    ] = False,
    fix_class: Annotated[
        str,
        typer.Option("--class", help="Apply tier: 'safe' (default) or 'review'."),
    ] = "safe",
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON audit output.")] = False,
) -> None:
    """Preview (or apply) deterministic safe fixes for findings."""
    ctx = ProjectContext(root=path.resolve())
    registry, _ = _build_registry(config=ctx.config)
    report = CheckRunner(registry).run(ctx)
    actions = plan_fixes(report.results, registry.select(), ctx)

    applying = apply_
    allowed = {SAFE} | ({REVIEW} if fix_class == REVIEW else set())
    applied: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    if applying:
        for action in actions:
            if action.fix_class in allowed:
                try:
                    record = apply_fix(action, path.resolve())
                except FixRefused as exc:
                    record = {"file": action.file, "status": "refused", "reason": str(exc)}
                (
                    applied
                    if record.get("status") in {"updated", "created", "deleted"}
                    else skipped
                ).append(record)
            elif action.fix_class != MANUAL:
                skipped.append({"file": action.file, "status": f"needs --class {action.fix_class}"})

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "applied": applied,
                    "skipped": skipped,
                    "manual": [_manual_row(a) for a in actions if a.fix_class == MANUAL],
                    "proposals": [_action_row(a) for a in actions if a.fix_class != MANUAL],
                },
                indent=2,
            )
        )
        return

    _print_actions(actions, applied, applying)


def _action_row(action: FixAction) -> dict[str, object]:
    return {
        "check_ids": list(action.check_ids),
        "fingerprints": [fp for fp in action.fingerprints if fp],
        "file": action.file,
        "class": action.fix_class,
        "transform": action.transform,
        "title": action.title,
        "superseded": action.superseded,
        "diff": action.diff(),
    }


def _manual_row(action: FixAction) -> dict[str, object]:
    return {
        "check_id": action.check_id,
        "fingerprints": [fp for fp in action.fingerprints if fp],
        "file": action.file,
        "title": action.title,
        "guidance": action.guidance,
    }


def _print_actions(actions: list[FixAction], applied: list[dict[str, object]], applying: bool) -> None:
    console.print()
    console.print("[bold]Fix Plan[/bold]")
    if not actions:
        console.print("  no fixable findings")
        return
    for klass, label in ((SAFE, "safe"), (REVIEW, "review-required"), (MANUAL, "manual-only")):
        group = [a for a in actions if a.fix_class == klass]
        if not group:
            continue
        console.print(f"\n[bold]{label}[/bold]")
        for action in group:
            suffix = " [dim](superseded)[/dim]" if action.superseded else ""
            console.print(
                f"  {action.title}  [dim]{action.file} {','.join(action.check_ids)}[/dim]{suffix}"
            )
            if action.fix_class == MANUAL:
                if action.guidance:
                    console.print(f"    [dim]{action.guidance}[/dim]")
            else:
                diff = action.diff()
                if diff:
                    for line in diff.rstrip("\n").splitlines():
                        color = (
                            "green"
                            if line.startswith("+")
                            else "red"
                            if line.startswith("-")
                            else "dim"
                        )
                        console.print(f"    [{color}]{line}[/{color}]")
    if applying:
        console.print(f"\n[bold]applied {len(applied)} fix(es)[/bold]")
    else:
        console.print("\n  [dim]dry run - `forge-doctor fix --apply` writes safe fixes only[/dim]")
