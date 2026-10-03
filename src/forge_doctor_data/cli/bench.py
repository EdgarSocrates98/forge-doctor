"""`forge-doctor-data bench` - performance & scale benchmark."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _stderr
from forge_doctor_data.core.bench import (
    BenchResult,
    generate_project,
    load_budget,
    result_dict,
    run_bench,
)

bench_app = typer.Typer(name="bench", help="Performance & scale benchmark.")
app.add_typer(bench_app, name="bench")

_JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
_BudgetOpt = Annotated[Path | None, typer.Option("--budget", help="JSON budget thresholds.")]


def _print(console: Console, r: BenchResult) -> None:
    console.print(f"    files={r.files} py={r.py_files} findings={r.findings}")
    console.print(
        f"    cold={r.cold_ms:.0f}ms warm={r.warm_ms:.0f}ms "
        f"ratio={r.warm_ratio:.2f} packs={r.packs} ({r.pack_ms:.0f}ms)"
    )
    console.print(
        f"    ast={r.ast_parsed}/{r.py_files} graph={r.graph_ms:.0f}ms "
        f"({r.graph_entities} ent/{r.graph_edges} rel) peak={r.peak_mb:.0f}MB"
    )
    for f in r.budget_failures:
        console.print(f"    [red]budget:[/red] {f}")


def _load_budget_or_exit(path: Path | None) -> dict[str, float] | None:
    if path is None:
        return None
    try:
        return load_budget(path)
    except (OSError, ValueError) as exc:
        _stderr.print(f"[red]budget file:[/red] {exc}")
        raise typer.Exit(2) from exc


@bench_app.command(name="run")
def bench_run(
    path: Annotated[
        Path | None,
        typer.Argument(help="Project root (omit to generate a synthetic one)."),
    ] = None,
    files: Annotated[int, typer.Option("--files", help="Synthetic corpus size.")] = 200,
    seed: Annotated[int, typer.Option("--seed", help="Generator seed.")] = 7,
    budget: _BudgetOpt = None,
    as_json: _JsonOpt = False,
) -> None:
    """Run the benchmark on a project or a generated synthetic corpus."""
    thresholds = _load_budget_or_exit(budget)
    console = Console()
    if path is not None:
        result = run_bench(path, thresholds)
        if as_json:
            typer.echo(json.dumps(result_dict(result), indent=2))
        else:
            console.print(f"\n[bold]Bench[/bold]  {path}")
            _print(console, result)
            console.print()
        if result.budget_failures:
            raise typer.Exit(1)
        return
    # synthetic corpus under temp dir (never inside the project)
    with tempfile.TemporaryDirectory(prefix="fd-bench-") as tmp:
        corpus = Path(tmp) / f"gen{files}"
        counts = generate_project(corpus, files, seed)
        result = run_bench(corpus, thresholds)
        if as_json:
            out = result_dict(result)
            out["generated"] = counts
            typer.echo(json.dumps(out, indent=2))
        else:
            console.print(f"\n[bold]Bench[/bold]  synthetic corpus files={files} seed={seed}")
            console.print(
                f"    generated: py={counts['py']} tf={counts['tf']} "
                f"sql={counts['sql']} other={counts['other']}"
            )
            _print(console, result)
            console.print()
        if result.budget_failures:
            raise typer.Exit(1)
