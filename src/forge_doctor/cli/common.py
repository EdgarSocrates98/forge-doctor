"""Shared scan plumbing: options, the one-analysis pipeline, emit engine."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from forge_doctor.core.schema import SchemaInfo
    from forge_doctor.plugins.protocol import Check

import typer
from rich.console import Console
from rich.padding import Padding
from rich.table import Table
from rich.text import Text

from forge_doctor.api import SCHEMA_VERSION
from forge_doctor.core.baseline import load_baseline_items
from forge_doctor.core.cache import scan_cache
from forge_doctor.core.config import ForgeDoctorConfig
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import CheckResult, ScanReport, Severity
from forge_doctor.core.profiles import PROFILES
from forge_doctor.core.registry import CheckRegistry
from forge_doctor.core.runner import CheckRunner
from forge_doctor.core.service import ScanRequest, ScanRequestError, ScanService
from forge_doctor.output.agent_renderer import render_agent
from forge_doctor.output.console import ConsoleRenderer
from forge_doctor.output.html_renderer import render_html
from forge_doctor.output.json_renderer import render_json, render_jsonl, result_to_dict
from forge_doctor.output.sarif_renderer import render_sarif
from forge_doctor.output.summary import INTERNAL_ERROR_EXIT, exit_code

_stderr = Console(stderr=True)

_SEV_ORDER = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2, Severity.PASS: 3}
_SEV_STYLE = {Severity.ERROR: "red", Severity.WARNING: "yellow", Severity.INFO: "blue"}


def render_findings(
    console: Console,
    ctx: ProjectContext,
    checks: list[Check],
    title: str = "Risks",
) -> int:
    """Shared inspect-command block: severity-sorted non-PASS findings.

    Returns the rendered count so callers can stay quiet when empty.
    """
    findings = [r for check in checks for r in check.run(ctx) if r.severity != Severity.PASS]
    if not findings:
        return 0
    console.print(f"\n[bold]{title}[/bold]")
    for r in sorted(findings, key=lambda r: (_SEV_ORDER[r.severity], r.check_id)):
        loc = ""
        if r.file:
            loc = f" {r.file.as_posix()}:{r.line}" if r.line else f" {r.file.as_posix()}"
        style = _SEV_STYLE.get(r.severity, "")
        console.print(
            f"  [{style}]{r.check_id} {r.severity.value.upper()}[/{style}][dim]{loc}[/dim]"
        )
        console.print(Padding(Text(r.message, style="dim"), pad=(0, 0, 0, 6)))
    return len(findings)


FORMATS = ("text", "json", "jsonl", "html", "sarif", "agent")
MACHINE_FORMATS = ("json", "jsonl", "sarif", "agent")
WATCH_INTERVAL = 1.0

PathArg = Annotated[Path, typer.Argument(help="Project directory to scan.")]
CheckOpt = Annotated[list[str], typer.Option("--check", "-c", help="Limit scan to a category.")]
IgnoreOpt = Annotated[list[str], typer.Option("--ignore", "-i", help="Suppress a check id.")]
FormatOpt = Annotated[str, typer.Option("--format", "-f", help="text|json|html|sarif|agent")]
QuietOpt = Annotated[bool, typer.Option("--quiet", "-q", help="Only problems.")]
FailOnOpt = Annotated[
    str, typer.Option("--fail-on", help="Lowest severity that fails: error|warning.")
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Internal detail.")]
BaselineOpt = Annotated[
    Path | None,
    typer.Option(
        "--baseline",
        help="Compare against a baseline file or a named baseline "
        "in .forge-doctor/baselines/<name>.json.",
    ),
]
SaveBaselineOpt = Annotated[
    Path | None,
    typer.Option(
        "--save-baseline",
        help="Write results to a baseline file or a named baseline "
        "in .forge-doctor/baselines/<name>.json.",
    ),
]
OutputOpt = Annotated[Path | None, typer.Option("--output", "-o", help="Write output to a file.")]
WatchOpt = Annotated[bool, typer.Option("--watch", "-w", help="Re-scan whenever files change.")]
NoColorOpt = Annotated[bool, typer.Option("--no-color", help="Disable ANSI colors.")]
NewOnlyOpt = Annotated[
    bool, typer.Option("--new-only", help="Show only findings new vs --baseline.")
]
NoPluginsOpt = Annotated[bool, typer.Option("--no-plugins", help="Skip external plugin loading.")]
FilesOpt = Annotated[
    list[str],
    typer.Option("--files", "-F", help="Report findings for these files only."),
]
ProfileOpt = Annotated[
    str, typer.Option("--profile", help=f"Severity policy: {'|'.join(PROFILES)}.")
]
EmitOpt = Annotated[
    list[str],
    typer.Option(
        "--emit",
        help="Extra output: FMT or FMT:PATH (repeatable). One may target stdout.",
    ),
]
ShowRootOpt = Annotated[
    bool,
    typer.Option("--show-root", help="Include the absolute project root in JSON output."),
]
CacheOpt = Annotated[
    bool | None,
    typer.Option(
        "--cache/--no-cache",
        help="Reuse per-file analysis facts (user cache dir; off by default in CI).",
    ),
]
StatsOpt = Annotated[
    bool,
    typer.Option("--stats", help="Per-check timings + cache hit rate to stderr."),
]
EvidenceOutOpt = Annotated[
    Path | None,
    typer.Option(
        "--evidence-out",
        help="Write a dated audit bundle (report + suppressions + policy packs).",
    ),
]
RecordOpt = Annotated[
    bool,
    typer.Option("--record", help="Append a history snapshot to .forge-doctor/history/."),
]
KeepOpt = Annotated[
    int | None,
    typer.Option("--keep", help="With --record: retain only the newest N snapshots."),
]


@dataclass
class _ScanCli:
    path: Path
    categories: tuple[str, ...]
    ignore: tuple[str, ...]
    fmt: str
    quiet: bool
    fail_on: str
    verbose: bool
    baseline: Path | None = None
    save_baseline: Path | None = None
    output: Path | None = None
    watch: bool = False
    no_color: bool = False
    new_only: bool = False
    no_plugins: bool = False
    files: tuple[str, ...] = ()
    profile: str = "default"
    emit: tuple[str, ...] = ()
    show_root: bool = False
    cache: bool | None = None
    stats: bool = False
    evidence_out: Path | None = None
    record: bool = False
    keep: int | None = None


def _build_registry(
    no_plugins: bool = False,
    config: ForgeDoctorConfig | None = None,
) -> tuple[CheckRegistry, list[str]]:
    """Thin shim over :meth:`ScanService.build_registry` (plugin trust gate)."""
    return ScanService().build_registry(no_plugins, config)


def _execute_scan(
    opts: _ScanCli,
) -> tuple[ScanReport, CheckRunner, int, list[str], ProjectContext]:
    """Build context, run checks, apply profile/baseline/filters."""
    from forge_doctor.core.baseline import BaselineError

    request = ScanRequest(
        path=opts.path,
        categories=opts.categories,
        ignore=opts.ignore,
        fail_on=opts.fail_on,
        verbose=opts.verbose,
        cache=opts.cache,
        no_plugins=opts.no_plugins,
        profile=opts.profile if opts.profile != "default" else None,
        files=opts.files or None,
        baseline=opts.baseline,
        save_baseline=opts.save_baseline,
        new_only=opts.new_only,
    )
    try:
        outcome = ScanService().run(request)
    except ScanRequestError as exc:
        _stderr.print(f"[red]{exc}[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT) from exc
    except BaselineError as exc:
        _stderr.print(f"[red]Baseline error:[/red] {exc}")
        raise typer.Exit(INTERNAL_ERROR_EXIT) from exc
    return (
        outcome.report,
        outcome.runner,
        outcome.selected,
        outcome.plugin_errors,
        outcome.ctx,
    )


def _validate_scan(opts: _ScanCli) -> None:
    """Early validation - cheap failures before any scanning work."""
    if not opts.path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {opts.path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    if opts.fmt not in FORMATS:
        _stderr.print(f"[red]Unknown format:[/red] {opts.fmt} ({'|'.join(FORMATS)})")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    if opts.fail_on not in ("error", "warning"):
        _stderr.print(f"[red]Unknown --fail-on:[/red] {opts.fail_on} (error|warning)")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    if opts.profile not in PROFILES:
        _stderr.print(f"[red]Unknown profile:[/red] {opts.profile} ({'|'.join(PROFILES)})")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    if opts.new_only and opts.baseline is None:
        _stderr.print("[red]--new-only requires --baseline.[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    if opts.watch and opts.fmt != "text":
        _stderr.print("[red]--watch only supports text output.[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    for spec in opts.emit:
        fmt = spec.partition(":")[0].strip().lower()
        if fmt not in FORMATS:
            _stderr.print(f"[red]Unknown emit format:[/red] {fmt} ({'|'.join(FORMATS)})")
            raise typer.Exit(INTERNAL_ERROR_EXIT)


def _render_to(report: ScanReport, fmt: str, opts: _ScanCli, selected: int) -> str:
    """Render one report in one format; text uses a recording console."""
    if fmt == "json":
        return render_json(report, show_root=opts.show_root)
    if fmt == "sarif":
        return render_sarif(report)
    if fmt == "agent":
        return render_agent(report)
    if fmt == "jsonl":
        return render_jsonl(report)
    if fmt == "html":
        return render_html(report, check_count=selected)
    recording = Console(record=True, no_color=opts.no_color, width=100)
    ConsoleRenderer(console=recording, quiet=opts.quiet).render(report, check_count=selected)
    return recording.export_text()


def _emit_reports(
    report: ScanReport,
    opts: _ScanCli,
    console: Console,
    selected: int,
    plugin_errors: list[str],
) -> None:
    """One analysis, N renderings: --emit FMT[:PATH], or --format/--output."""
    emits = list(opts.emit) or [f"{opts.fmt}:{opts.output}" if opts.output else opts.fmt]
    stdout_emits = [e for e in emits if ":" not in e]
    if len(stdout_emits) > 1:
        _stderr.print(
            "[red]Only one --emit may write to stdout;[/red] "
            "give the others a path: --emit sarif:report.sarif"
        )
        raise typer.Exit(INTERNAL_ERROR_EXIT)

    for spec in emits:
        fmt, _, target = spec.partition(":")
        fmt = fmt.strip().lower()
        if fmt not in FORMATS:
            _stderr.print(f"[red]Unknown emit format:[/red] {fmt} ({'|'.join(FORMATS)})")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        if not target and fmt == "html":
            target = "forge-doctor-report.html"  # html historically writes a file
        if not target and fmt == "text":
            # Live console keeps colors; the recorded copy exists for files.
            ConsoleRenderer(console=console, quiet=opts.quiet).render(report, check_count=selected)
            continue
        payload = _render_to(report, fmt, opts, selected)
        if target:
            Path(target).write_text(payload + "\n", encoding="utf-8")
            console.print(f"[green]{fmt} report written:[/green] {Path(target).resolve()}")
        else:
            typer.echo(payload)

    for error in plugin_errors:
        _stderr.print(f"[yellow]Plugin skipped:[/yellow] {error}")


def _write_evidence(target: Path, report: ScanReport, ctx: ProjectContext) -> Path:
    """``--evidence-out``: dated audit bundle - the full JSON report, the
    suppression audit trail, and the policy packs that were in effect."""
    import dataclasses
    import json as _json
    from datetime import UTC, datetime

    from forge_doctor.core.policy_pack import load_packs

    bundle = target / f"evidence-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "report.json").write_text(
        render_json(report, show_root=True) + "\n", encoding="utf-8"
    )
    (bundle / "suppressions.json").write_text(
        _json.dumps(
            [dataclasses.asdict(s) for s in report.suppressions],
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    packs, errors = load_packs(ctx.root, ctx.config.policy_packs)
    (bundle / "packs.json").write_text(
        _json.dumps(
            {
                "packs": [
                    {
                        "name": p.name,
                        "version": p.version,
                        "rules": [r.id for r in p.rules],
                        "extends": list(p.extends),
                        "require_approval": p.require_approval,
                        "path": str(p.path),
                    }
                    for p in packs
                ],
                "errors": [e.message for e in errors],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return bundle


def _run_scan(opts: _ScanCli) -> None:
    """Shared engine for ``scan`` and the per-category commands."""
    _validate_scan(opts)
    if opts.watch:
        _watch_loop(opts)
        return

    console = Console(no_color=opts.no_color)
    with _stderr.status("[cyan]Analyzing project...[/cyan]", spinner="dots"):
        report, runner, selected, plugin_errors, ctx = _execute_scan(opts)

    if opts.record:
        from forge_doctor.core.history import prune, record_snapshot

        root = opts.path.resolve()
        snap_path = record_snapshot(report, root, ctx)
        console.print(f"[green]snapshot recorded:[/green] {snap_path}")
        if opts.keep is not None:
            removed = prune(root, opts.keep)
            if removed:
                console.print(f"[dim]pruned {len(removed)} oldest snapshot(s)[/dim]")

    if opts.evidence_out is not None:
        bundle = _write_evidence(opts.evidence_out, report, ctx)
        console.print(f"[green]evidence bundle written:[/green] {bundle.resolve()}")

    _emit_reports(report, opts, console, selected, plugin_errors)

    if opts.stats:
        _print_stats(runner, ctx)

    if opts.verbose:
        for failure in runner.failures:
            _stderr.print(f"[red]{failure.check.id}[/red]\n{failure.traceback}")

    raise typer.Exit(exit_code(report, fail_on=opts.fail_on))


def _print_stats(runner: CheckRunner, ctx: ProjectContext) -> None:
    """--stats: per-check durations + cache hit rate on stderr."""
    cache = scan_cache(ctx)
    total = cache.hits + cache.misses
    hit_rate = f"{100 * cache.hits / total:.0f}%" if total else "n/a"
    _stderr.print(f"[dim]cache: {cache.hits} hits / {cache.misses} misses ({hit_rate})[/dim]")
    for check_id, seconds in sorted(runner.timings.items(), key=lambda kv: -kv[1])[:15]:
        _stderr.print(f"[dim]{check_id:<10} {seconds * 1000:>7.1f} ms[/dim]")


def _snapshot(path: Path) -> dict[str, float]:
    """File -> mtime map for change detection."""
    ctx = ProjectContext(root=path)
    snap: dict[str, float] = {}
    for relative in ctx.files:
        try:
            snap[relative.as_posix()] = (ctx.root / relative).stat().st_mtime
        except OSError:
            continue
    return snap


def _watch_loop(opts: _ScanCli) -> None:
    """Re-scan on file changes until Ctrl+C; exit with last scan's code."""
    console = Console(no_color=opts.no_color)
    code = 0

    try:
        from watchfiles import watch as wf_watch
    except ImportError:
        wf_watch = None

    try:
        while True:
            report, runner, selected, _, ctx = _execute_scan(opts)
            code = exit_code(report, fail_on=opts.fail_on)
            console.clear()
            ConsoleRenderer(console=console, quiet=opts.quiet).render(report, check_count=selected)
            cache = scan_cache(ctx)
            console.print(
                f"[dim]watching for changes ({'watchfiles' if wf_watch else 'polling'})"
                f" - cache {cache.hits} hits/{cache.misses} misses - Ctrl+C to exit[/dim]"
            )
            if opts.verbose:
                for failure in runner.failures:
                    console.print(f"[red]{failure.check.id}[/red]\n{failure.traceback}")
            if wf_watch is not None:
                next(wf_watch(opts.path, yield_on_timeout=True))
            else:
                current = _snapshot(opts.path)
                while _snapshot(opts.path) == current:
                    time.sleep(WATCH_INTERVAL)
    except KeyboardInterrupt:
        raise typer.Exit(code) from None


def _render_findings(findings: list[CheckResult], fmt: str) -> None:
    """Shared renderer for runtime findings (text table or JSON list)."""
    import json as _json

    if fmt == "json":
        typer.echo(
            _json.dumps(
                {
                    "tool": "forge-doctor",
                    "schema_version": SCHEMA_VERSION,
                    "findings": [result_to_dict(f) for f in findings],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    console = Console()
    table = Table(title="Runtime findings", title_justify="left")
    table.add_column("Id", style="dim")
    table.add_column("Severity")
    table.add_column("Finding", style="bold")
    style = {
        "error": "red",
        "warning": "yellow",
        "info": "blue",
        "pass": "green",
    }
    for finding in findings:
        sev = finding.severity.value
        table.add_row(finding.check_id, f"[{style.get(sev, 'white')}]{sev}[/]", finding.message)
    console.print(table)


def _scan_items(path: Path) -> dict[str, dict[str, object]]:
    """Scan a directory in-process and key results by fingerprint."""
    ctx = ProjectContext(root=path)
    registry, _ = _build_registry(config=ctx.config)
    report = CheckRunner(registry).run(ctx)
    return {r.fingerprint: result_to_dict(r) for r in report.results if r.fingerprint is not None}


@contextlib.contextmanager
def _ref_worktree(repo: Path, ref: str) -> Iterator[Path | None]:
    """Yield a detached worktree of ``ref`` (None when the ref fails)."""
    tmp = Path(tempfile.mkdtemp(prefix="forge-doctor-diff-"))
    add = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(tmp), ref],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    if add.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        _stderr.print(f"[red]Cannot resolve ref '{ref}':[/red] {add.stderr.strip()}")
        yield None
        return
    try:
        yield tmp
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(tmp)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def _scan_git_ref(repo: Path, ref: str) -> dict[str, dict[str, object]] | None:
    """Scan a git ref via a temporary detached worktree. None on failure."""
    with _ref_worktree(repo, ref) as tmp:
        if tmp is None:
            return None
        return _scan_items(tmp)


def _resolve_diff_side(spec: str, repo: Path) -> tuple[str, dict[str, dict[str, object]]] | None:
    """A diff side is either a saved report/baseline JSON or a git ref."""
    candidate = Path(spec)
    if candidate.is_file():
        from forge_doctor.core.baseline import BaselineError

        try:
            return spec, load_baseline_items(candidate)
        except BaselineError as exc:
            _stderr.print(f"[red]Cannot read report:[/red] {exc}")
            return None
    items = _scan_git_ref(repo, spec)
    if items is None:
        _stderr.print(
            f"[red]Not a file or git ref:[/red] {spec} "
            "(save a report with --format json -o FILE or pass a ref like HEAD~1)"
        )
        return None
    return spec, items


def _workspace_projects(path: Path) -> list[tuple[str, Path]]:
    """(name, relative-dir) for every nested pyproject.toml."""
    import tomllib

    ctx = ProjectContext(root=path)
    projects = []
    for pyproject_path in sorted(
        p for p in ctx.files if p.name == "pyproject.toml" and len(p.parts) >= 2
    ):
        name = pyproject_path.parent.name
        try:
            data = tomllib.loads(ctx.read_text(pyproject_path) or "")
            name = str(
                data.get("project", {}).get("name")
                or data.get("tool", {}).get("poetry", {}).get("name")
                or name
            )
        except Exception:
            pass
        projects.append((name, pyproject_path.parent))
    return projects


_SCHEMA_SUFFIXES = {".avsc", ".sql", ".yml", ".yaml", ".json"}


def _schema_files(root: Path) -> dict[str, Path]:
    return {
        p.relative_to(root).as_posix(): p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in _SCHEMA_SUFFIXES
    }


def _ref_schemas(repo: Path, ref: str) -> dict[str, SchemaInfo] | None:
    """Materialize ``ref`` in a worktree; parse schemas before cleanup."""
    from forge_doctor.core.schema import parse_schema

    tmp = Path(tempfile.mkdtemp(prefix="forge-doctor-schema-"))
    add = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(tmp), ref],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    if add.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        _stderr.print(f"[red]Cannot resolve ref '{ref}':[/red] {add.stderr.strip()}")
        return None
    try:
        schemas: dict[str, SchemaInfo] = {}
        for rel, schema_file in _schema_files(tmp).items():
            try:
                schemas[rel] = parse_schema(schema_file)
            except OSError:
                continue
        return schemas
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(tmp)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        )
        shutil.rmtree(tmp, ignore_errors=True)
