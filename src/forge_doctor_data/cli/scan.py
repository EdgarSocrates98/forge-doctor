"""``scan`` and per-category commands, plus the ``spark`` runtime group."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import (
    BaselineOpt,
    CacheOpt,
    CheckOpt,
    EmitOpt,
    EvidenceCompactOpt,
    EvidenceOutOpt,
    FailOnOpt,
    FilesOpt,
    FormatOpt,
    IgnoreOpt,
    IncrementalOpt,
    KeepOpt,
    NewOnlyOpt,
    NoColorOpt,
    NoPluginsOpt,
    OutputOpt,
    PathArg,
    ProfileOpt,
    QuietOpt,
    RecordOpt,
    SaveBaselineOpt,
    ShowRootOpt,
    StatsOpt,
    VerboseOpt,
    WatchOpt,
    _render_findings,
    _run_scan,
    _ScanCli,
    _stderr,
)
from forge_doctor_data.core.models import Severity
from forge_doctor_data.output.summary import INTERNAL_ERROR_EXIT


@app.command(
    epilog=(
        "Examples:\n\n"
        "  forge-doctor-data scan . --quiet                     # only problems\n\n"
        "  forge-doctor-data scan . -f json -o report.json      # machine-readable\n\n"
        "  forge-doctor-data scan . --baseline main --new-only  # gate on new findings\n\n"
        "  forge-doctor-data scan . --record --keep 30          # snapshot for 'history'\n\n"
        "  forge-doctor-data scan . --emit sarif:out.sarif --emit html:out.html"
    )
)
def scan(
    path: PathArg = Path("."),
    check: CheckOpt = [],
    ignore: IgnoreOpt = [],
    fmt: FormatOpt = "text",
    quiet: QuietOpt = False,
    fail_on: FailOnOpt = "error",
    verbose: VerboseOpt = False,
    baseline: BaselineOpt = None,
    save_baseline: SaveBaselineOpt = None,
    output: OutputOpt = None,
    watch: WatchOpt = False,
    no_color: NoColorOpt = False,
    new_only: NewOnlyOpt = False,
    no_plugins: NoPluginsOpt = False,
    files: FilesOpt = [],
    profile: ProfileOpt = "default",
    emit: EmitOpt = [],
    show_root: ShowRootOpt = False,
    cache: CacheOpt = True,
    stats: StatsOpt = False,
    evidence_out: EvidenceOutOpt = None,
    evidence_compact: EvidenceCompactOpt = False,
    record: RecordOpt = False,
    keep: KeepOpt = None,
    incremental: IncrementalOpt = False,
) -> None:
    """Scan a project for data-engineering problems."""
    _run_scan(
        _ScanCli(
            path=path,
            categories=tuple(check),
            ignore=tuple(ignore),
            fmt=fmt,
            quiet=quiet,
            fail_on=fail_on,
            verbose=verbose,
            baseline=baseline,
            save_baseline=save_baseline,
            output=output,
            watch=watch,
            no_color=no_color,
            new_only=new_only,
            no_plugins=no_plugins,
            files=tuple(files),
            profile=profile,
            emit=tuple(emit),
            show_root=show_root,
            cache=cache,
            stats=stats,
            evidence_out=evidence_out,
            evidence_compact=evidence_compact,
            record=record,
            keep=keep,
            incremental=incremental,
        )
    )


def _category_command(category: str) -> Callable[..., None]:
    def command(
        path: PathArg = Path("."),
        ignore: IgnoreOpt = [],
        fmt: FormatOpt = "text",
        quiet: QuietOpt = False,
        fail_on: FailOnOpt = "error",
        verbose: VerboseOpt = False,
        baseline: BaselineOpt = None,
        save_baseline: SaveBaselineOpt = None,
        output: OutputOpt = None,
        no_color: NoColorOpt = False,
        new_only: NewOnlyOpt = False,
        no_plugins: NoPluginsOpt = False,
        files: FilesOpt = [],
        profile: ProfileOpt = "default",
        emit: EmitOpt = [],
        show_root: ShowRootOpt = False,
        cache: CacheOpt = True,
        stats: StatsOpt = False,
    ) -> None:
        _run_scan(
            _ScanCli(
                path=path,
                categories=(category,),
                ignore=tuple(ignore),
                fmt=fmt,
                quiet=quiet,
                fail_on=fail_on,
                verbose=verbose,
                baseline=baseline,
                save_baseline=save_baseline,
                output=output,
                no_color=no_color,
                new_only=new_only,
                no_plugins=no_plugins,
                files=tuple(files),
                profile=profile,
                emit=tuple(emit),
                show_root=show_root,
                cache=cache,
                stats=stats,
            )
        )

    command.__name__ = category
    command.__doc__ = f"Run only {category} checks."
    return command


for _category in (
    "repository",
    "python",
    "dependencies",
    "git",
    "aws",
    "docker",
    "glue",
    "ci",
    "iac",
):
    _name = "repo" if _category == "repository" else _category
    app.command(_name)(_category_command(_category))

# ``spark`` is both a scan category and the runtime-doctor group.
spark_app = typer.Typer(name="spark", help="Spark checks and runtime diagnosis.")
app.add_typer(spark_app, name="spark")


@spark_app.callback(invoke_without_command=True)
def spark_default(
    ctx: typer.Context,
    path: Annotated[Path, typer.Option("--path", help="Project directory.")] = Path("."),
    ignore: IgnoreOpt = [],
    fmt: FormatOpt = "text",
    quiet: QuietOpt = False,
    fail_on: FailOnOpt = "error",
    verbose: VerboseOpt = False,
    no_color: NoColorOpt = False,
    profile: ProfileOpt = "default",
) -> None:
    """Run only spark checks (default) or a runtime subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    _run_scan(
        _ScanCli(
            path=path,
            categories=("spark",),
            ignore=tuple(ignore),
            fmt=fmt,
            quiet=quiet,
            fail_on=fail_on,
            verbose=verbose,
            no_color=no_color,
            profile=profile,
        )
    )


@spark_app.command(name="eventlog")
def spark_eventlog(
    source: Annotated[str, typer.Argument(help="Event-log file or directory.")],
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Analyze a Spark event log (JSONL) for runtime problems."""
    from forge_doctor_data.core.spark_runtime import analyze_eventlog

    path = Path(source)
    if not path.exists():
        _stderr.print(f"[red]Not found:[/red] {source}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    findings = analyze_eventlog(path)
    _render_findings(findings, fmt)
    if fmt != "json":
        raise typer.Exit(1 if any(f.severity is Severity.ERROR for f in findings) else 0)


@spark_app.command(name="plan")
def spark_plan(
    source: Annotated[str, typer.Argument(help="File containing a physical plan.")],
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Scan a Spark physical plan for pathological operators."""
    from forge_doctor_data.core.spark_runtime import analyze_plan

    path = Path(source)
    if not path.is_file():
        _stderr.print(f"[red]Not a file:[/red] {source}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    findings = analyze_plan(path)
    _render_findings(findings, fmt)
    if fmt != "json":
        raise typer.Exit(0)


@spark_app.command(name="logs")
def spark_logs(
    source: Annotated[str, typer.Argument(help="Driver/executor log file.")],
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Fingerprint a Spark log against error packs and runtime signatures."""
    from forge_doctor_data.core.spark_runtime import analyze_log

    path = Path(source)
    if not path.is_file():
        _stderr.print(f"[red]Not a file:[/red] {source}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    findings = analyze_log(path)
    _render_findings(findings, fmt)
    if fmt != "json":
        raise typer.Exit(1 if any(f.severity is Severity.ERROR for f in findings) else 0)
