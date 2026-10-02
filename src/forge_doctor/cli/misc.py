"""Utility commands: init, info, explain, checks, cache, suppressions,
lineage, schema, knowledge, sbom, mcp, lsp, doctor, graph, diagnose, trace."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from forge_doctor.core.schema import SchemaChange

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor import __version__
from forge_doctor.api import SCHEMA_VERSION
from forge_doctor.cli.app import app
from forge_doctor.cli.common import (
    OutputOpt,
    PathArg,
    _build_registry,
    _ref_schemas,
    _stderr,
)
from forge_doctor.core.cache import ScanCache
from forge_doctor.core.config import PluginRules
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.project_info import collect_info
from forge_doctor.core.runner import CheckRunner
from forge_doctor.core.scaffold import scaffold
from forge_doctor.output.summary import INTERNAL_ERROR_EXIT
from forge_doctor.plugins.discovery import load_plugins


@app.command(name="init")
def init_cmd(
    path: PathArg = Path("."),
    name: Annotated[str | None, typer.Option("--name", help="Project name.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing files.")] = False,
) -> None:
    """Scaffold a new Python project (pyproject, .gitignore, README, src/, tests/)."""
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=True)
    report = scaffold(root, name=name, force=force)
    console = Console()
    console.print(f"\n[bold]Forge Doctor[/bold] init - {root}\n")
    for created in report.created:
        console.print(f"  [green]+[/green] {created.relative_to(root).as_posix()}")
    for skipped in report.skipped:
        console.print(
            f"  [dim]= {skipped.relative_to(root).as_posix()} (exists - use --force)[/dim]"
        )
    console.print()
    if report.created:
        console.print("[dim]Next: poetry install && poetry run forge-doctor scan .[/dim]\n")


@app.command(name="info")
def info_cmd(path: PathArg = Path(".")) -> None:
    """Quick project stats - files, languages, tooling - no checks run."""
    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    info = collect_info(ProjectContext(root=path))
    console = Console()

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    grid.add_row("Name", info.name)
    if info.project_version:
        grid.add_row("Version", info.project_version)
    grid.add_row("Path", info.path)
    grid.add_row("Python", info.python_version or "not on PATH")
    grid.add_row("requires-python", info.requires_python or "not declared")
    grid.add_row("Files", str(info.file_count))
    grid.add_row("Lines", f"{info.line_count:,}")
    grid.add_row(
        "Git",
        f"repo, {info.tracked_files} tracked" if info.git_repo else "not a repo",
    )
    grid.add_row("Tooling", ", ".join(info.tools) or "none detected")
    if info.by_extension:
        top = ", ".join(f"{ext} ({n})" for ext, n in info.by_extension[:5])
        grid.add_row("Top types", top)
    console.print()
    console.print(grid)
    console.print()


@app.command(name="explain")
def explain_cmd(
    check_id: Annotated[str, typer.Argument(help="Check id, e.g. SPARK001.")],
    as_json: Annotated[bool, typer.Option("--json", help="Emit rule metadata as JSON.")] = False,
) -> None:
    """Explain what a check looks for, when it is OK, and how to fix it."""
    registry, _ = _build_registry()
    check = registry.get(check_id.upper())
    if check is None:
        if check_id.upper().startswith("SQL") and importlib.util.find_spec("sqlglot") is None:
            _stderr.print(
                f"[yellow]{check_id}[/yellow] requires the sql extra"
                " (`pip install forge-doctor[sql]`)"
            )
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        _stderr.print(f"[red]Unknown check id:[/red] {check_id} (see `forge-doctor checks`)")
        raise typer.Exit(INTERNAL_ERROR_EXIT)

    if as_json:
        import json

        typer.echo(
            json.dumps(
                {
                    "id": check.id,
                    "title": check.title,
                    "category": check.category,
                    "why": getattr(check, "why", "") or "",
                    "when_ok": getattr(check, "when_ok", "") or "",
                    "fix": getattr(check, "fix", "") or "",
                    "confidence": getattr(getattr(check, "confidence", None), "value", "high"),
                    "tags": list(getattr(check, "tags", ()) or ()),
                    "docs_uri": getattr(check, "docs_uri", None),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console = Console()
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold", justify="right")
    grid.add_column()
    doc = (check.__doc__ or "").strip().splitlines()
    if doc:
        grid.add_row("", doc[0])
    for label, value in (
        ("Why", getattr(check, "why", "") or ""),
        ("When OK", getattr(check, "when_ok", "") or ""),
        ("Fix", getattr(check, "fix", "") or ""),
    ):
        if value:
            grid.add_row(f"{label}:", value)
    console.print(f"\n[bold]{check.id}[/bold] {check.title}")
    console.print(f"[dim]category: {check.category}[/dim]")
    console.print(grid)
    console.print()


@app.command(name="checks")
def checks_cmd() -> None:
    """List every registered check id, category and title."""
    registry, _ = _build_registry()
    console = Console()
    table = Table(show_lines=False)
    table.add_column("Id", style="dim")
    table.add_column("Category", style="bold")
    table.add_column("Title")
    categories = set()
    for check in registry.all():
        categories.add(check.category)
        table.add_row(check.id, check.category, check.title)
    console.print(table)
    console.print(
        f"[dim]{len(registry.all())} checks across {len(categories)} categories "
        "- `forge-doctor explain <ID>` for details[/dim]"
    )


cache_app = typer.Typer(name="cache", help="Inspect or clear the incremental analysis cache.")
app.add_typer(cache_app, name="cache")


@cache_app.callback(invoke_without_command=True)
def cache_default(
    ctx: typer.Context,
    path: Annotated[Path, typer.Option("--path", help="Project root.")] = Path("."),
) -> None:
    """Show cache statistics for the project."""
    if ctx.invoked_subcommand is not None:
        return
    cache = ScanCache(path.resolve())
    data = cache._files
    console = Console()
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    grid.add_row("Location", cache.path.as_posix())
    grid.add_row("Cached files", str(len(data)))
    with_buckets = sum(
        1
        for entry in data.values()
        if entry.get("facts", {}).get("spark_buckets") or entry.get("facts", {}).get("glue_buckets")
    )
    grid.add_row("With analyzer buckets", str(with_buckets))
    console.print()
    console.print(grid)
    console.print("\n[dim]forge-doctor cache clean[/dim] removes the cache.\n")


@cache_app.command(name="clean")
def cache_clean(
    path: Annotated[Path, typer.Option("--path", help="Project root.")] = Path("."),
) -> None:
    """Delete the incremental analysis cache."""
    removed = ScanCache(path.resolve()).clear()
    Console().print("[green]Cache cleared.[/green]" if removed else "[dim]No cache found.[/dim]")


@app.command(name="suppressions")
def suppressions_cmd(
    path: PathArg = Path("."),
    as_json: Annotated[bool, typer.Option("--json", help="Emit statuses as JSON.")] = False,
) -> None:
    """Audit configured suppressions: ACTIVE / EXPIRED / UNUSED."""
    import json as _json
    from datetime import date

    from forge_doctor.core.policy import suppression_statuses

    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    ctx = ProjectContext(root=path)
    if not ctx.config.suppressions:
        console = Console()
        console.print("[dim]No suppressions configured in pyproject.toml.[/dim]")
        return

    # A real scan is needed to know whether each suppression matched anything.
    registry, _ = _build_registry(no_plugins=True)
    report = CheckRunner(registry).run(ctx)
    statuses = suppression_statuses(ctx.config.suppressions, report.results, date.today())

    if as_json:
        typer.echo(
            _json.dumps(
                [
                    {
                        "rule": s.suppression.rule,
                        "path": s.suppression.path,
                        "owner": s.suppression.owner,
                        "expires": s.suppression.expires,
                        "approved_by": s.suppression.approved_by,
                        "status": s.status,
                        "matched": s.matched,
                        "reason": s.suppression.reason,
                    }
                    for s in statuses
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console = Console()
    table = Table(title="Suppressions", title_justify="left")
    for column in ("Rule", "Path", "Owner", "Expires", "Approved", "Status", "Matched"):
        table.add_column(column, style="bold" if column == "Rule" else "")
    style = {"active": "yellow", "expired": "red", "unused": "dim"}
    for status in statuses:
        s = status.suppression
        table.add_row(
            s.rule,
            s.path or "*",
            s.owner or "-",
            s.expires or "-",
            s.approved_by or "-",
            f"[{style.get(status.status, 'white')}]{status.status.upper()}[/]",
            str(status.matched),
        )
    console.print(table)
    expired = [s for s in statuses if s.status == "expired"]
    if expired:
        _stderr.print(f"[yellow]{len(expired)} expired suppression(s) reactivated.[/yellow]")
        raise typer.Exit(1)


@app.command(name="lineage")
def lineage_cmd(
    path: PathArg = Path("."),
    fmt: Annotated[
        str, typer.Option("--format", "-f", help="text|json|dot|mermaid|openlineage")
    ] = "text",
) -> None:
    """Static lineage: which jobs read/write which datasets."""
    import json as _json

    from forge_doctor.core.lineage import build_lineage

    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    graph = build_lineage(ProjectContext(root=path))
    if fmt == "json":
        typer.echo(_json.dumps(graph.to_dict(), indent=2, ensure_ascii=False))
        return
    if fmt == "dot":
        typer.echo(graph.to_dot())
        return
    if fmt == "mermaid":
        typer.echo(graph.to_mermaid())
        return
    if fmt == "openlineage":
        typer.echo(_json.dumps(graph.to_openlineage(), indent=2, ensure_ascii=False))
        return

    console = Console()
    if not graph.edges:
        console.print("[dim]No dataset reads/writes detected.[/dim]")
        return
    table = Table(title="Static lineage", title_justify="left")
    table.add_column("Direction")
    table.add_column("Dataset", style="bold")
    table.add_column("Job")
    table.add_column("Location", style="dim")
    for edge in graph.edges:
        if edge.kind == "reads":
            table.add_row(
                "[cyan]reads[/cyan]", edge.source, edge.target, f"{edge.file}:{edge.line}"
            )
        else:
            table.add_row(
                "[magenta]writes[/magenta]", edge.target, edge.source, f"{edge.file}:{edge.line}"
            )
    console.print(table)
    console.print(
        f"\n{len(graph.datasets)} dataset(s), {len(graph.jobs)} job(s), {len(graph.edges)} edge(s)."
    )


schema_app = typer.Typer(name="schema", help="Schema extraction and diffing.")
app.add_typer(schema_app, name="schema")


def _diff_two_files(old_path: Path, new_path: Path) -> list[SchemaChange]:
    from forge_doctor.core.schema import diff_schemas, parse_schema

    return diff_schemas(parse_schema(old_path), parse_schema(new_path))


@schema_app.command(name="contracts")
def schema_contracts(
    name: Annotated[str | None, typer.Argument(help="Contract name; omit to list all.")] = None,
) -> None:
    """Dump the JSON Schemas for Forge Doctor's public artifacts."""
    import json as _json

    from forge_doctor.core.schemas import SCHEMAS

    if name is None:
        console = Console()
        for key, item in SCHEMAS.items():
            console.print(f"  [bold]{key}[/bold]  [dim]{item.get('title', '')}[/dim]")
        return
    found = SCHEMAS.get(name)
    if found is None:
        _stderr.print(f"[red]Unknown contract:[/red] {name} (valid: {', '.join(sorted(SCHEMAS))})")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    typer.echo(_json.dumps(found, indent=2))


@schema_app.command(name="diff")
def schema_diff_cmd(
    old: Annotated[str, typer.Argument(help="Old schema file, or 'base...head' git range.")],
    new: Annotated[str | None, typer.Argument(help="New schema file.")] = None,
    path: Annotated[Path, typer.Option("--path", help="Repo root for git ranges.")] = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Diff two schema files, or all schema files across a git range."""
    import json as _json

    from forge_doctor.core.schema import diff_schemas, parse_schema

    results: list[dict[str, object]] = []
    if new is not None:
        old_p, new_p = Path(old), Path(new)
        if not old_p.is_file() or not new_p.is_file():
            _stderr.print("[red]Both sides must be files, or pass 'base...head'.[/red]")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        old_info, new_info = parse_schema(old_p), parse_schema(new_p)
        for change in diff_schemas(old_info, new_info):
            results.append(
                {
                    "file": f"{old_p.name} -> {new_p.name}",
                    "kind": change.kind,
                    "column": change.column,
                    "detail": change.detail,
                    "classification": change.classification,
                }
            )
    else:
        if "..." not in old:
            _stderr.print("[red]Provide two files or a 'base...head' range.[/red]")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        base, _, head = old.partition("...")
        base_schemas = _ref_schemas(path, base)
        head_schemas = _ref_schemas(path, head)
        if base_schemas is None or head_schemas is None:
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        from forge_doctor.core.schema import SchemaChange

        for rel in sorted(set(base_schemas) | set(head_schemas)):
            old_schema = base_schemas.get(rel)
            new_schema = head_schemas.get(rel)
            changes: list[SchemaChange]
            if old_schema is None and new_schema is not None:
                changes = [
                    SchemaChange("added", n, f"new file column `{n}`", "potentially breaking")
                    for n in sorted(new_schema.columns)
                ]
            elif new_schema is None and old_schema is not None:
                changes = [
                    SchemaChange("dropped", n, "schema file removed", "breaking")
                    for n in sorted(old_schema.columns)
                ]
            elif old_schema is not None and new_schema is not None:
                changes = diff_schemas(old_schema, new_schema)
            else:
                changes = []
            for change in changes:
                results.append(
                    {
                        "file": rel,
                        "kind": change.kind,
                        "column": change.column,
                        "detail": change.detail,
                        "classification": change.classification,
                    }
                )

    if fmt == "json":
        typer.echo(
            _json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    # Honesty stamp: schema parsing is syntax-level only.
                    "parser": "best-effort",
                    "changes": results,
                },
                indent=2,
            )
        )
        return
    console = Console()
    table = Table(title="Schema diff", title_justify="left")
    table.add_column("File", style="dim")
    table.add_column("Kind")
    table.add_column("Column", style="bold")
    table.add_column("Classification")
    table.add_column("Detail", style="dim")
    styles = {
        "compatible": "green",
        "potentially breaking": "yellow",
        "breaking": "red",
    }
    if not results:
        table.add_row("-", "-", "-", "[green]no changes[/green]", "")
    for item in results:
        cls = str(item["classification"])
        table.add_row(
            str(item["file"]),
            str(item["kind"]),
            str(item["column"]),
            f"[{styles.get(cls, 'white')}]{cls}[/]",
            str(item["detail"]),
        )
    console.print(table)
    if any(r["classification"] == "breaking" for r in results):
        raise typer.Exit(1)


knowledge_app = typer.Typer(name="knowledge", help="Knowledge-pack provenance.")
app.add_typer(knowledge_app, name="knowledge")


def _knowledge_table() -> Table:
    from forge_doctor.core.knowledge import list_packs, pack_meta

    table = Table(title="Knowledge packs", title_justify="left")
    table.add_column("Pack", style="bold")
    table.add_column("Schema", justify="right")
    table.add_column("Version")
    table.add_column("Verified")
    table.add_column("Sources", justify="right")
    for domain, name, pack in list_packs():
        meta = pack_meta(pack)
        table.add_row(
            f"{domain}/{name}",
            str(meta["schema_version"]),
            str(meta["pack_version"]),
            str(meta["verified_at"] or "-"),
            str(len(meta["sources"])),
        )
    return table


@knowledge_app.callback(invoke_without_command=True)
def knowledge_default(ctx: typer.Context) -> None:
    """List all bundled knowledge packs with provenance."""
    if ctx.invoked_subcommand is not None:
        return
    Console().print(_knowledge_table())


@knowledge_app.command(name="list")
def knowledge_list() -> None:
    """Alias for the default listing."""
    Console().print(_knowledge_table())


@knowledge_app.command(name="info")
def knowledge_info(
    domain: Annotated[str, typer.Argument(help="Pack domain (glue, spark, errors...).")],
) -> None:
    """Show provenance detail for one domain's packs."""
    from forge_doctor.core.knowledge import list_packs, pack_meta

    console = Console()
    found = False
    for d, name, pack in list_packs():
        if d != domain:
            continue
        found = True
        meta = pack_meta(pack)
        console.print(f"[bold]{d}/{name}[/bold]")
        console.print(f"  schema_version: {meta['schema_version']}")
        console.print(f"  pack_version:   {meta['pack_version']}")
        console.print(f"  verified_at:    {meta['verified_at'] or '-'}")
        for source in meta["sources"]:
            console.print(f"  source: {source}")
        console.print()
    if not found:
        _stderr.print(f"[red]No packs for domain:[/red] {domain}")
        raise typer.Exit(1)


@knowledge_app.command(name="verify")
def knowledge_verify() -> None:
    """Validate structure + flag packs stale (>90d since verified_at)."""
    from forge_doctor.core.knowledge import list_packs, verify_pack

    console = Console()
    problems: list[str] = []
    for domain, name, _pack in list_packs():
        issues = verify_pack(domain, name)
        status = "[green]ok[/green]" if not issues else "[yellow]stale/incomplete[/]"
        console.print(f"  {domain}/{name}: {status}")
        problems.extend(issues)
    console.print()
    if problems:
        for issue in problems:
            _stderr.print(f"[yellow]{issue}[/yellow]")
        raise typer.Exit(1)


@app.command(name="sbom")
def sbom_cmd(
    path: PathArg = Path("."),
    fmt: Annotated[str, typer.Option("--format", "-f", help="cyclonedx|text")] = "cyclonedx",
    output: OutputOpt = None,
) -> None:
    """Emit a CycloneDX 1.5 SBOM: deps, plugins, knowledge packs, images."""
    import json as _json

    from forge_doctor.core.sbom import build_sbom

    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    bom = build_sbom(ProjectContext(root=path))
    if fmt == "text":
        console = Console()
        table = Table(title="SBOM components", title_justify="left")
        table.add_column("Type")
        table.add_column("Name", style="bold")
        table.add_column("Version")
        for c in bom["components"]:
            table.add_row(c["type"], c["name"], str(c.get("version", "-")))
        console.print(table)
        console.print(f"\n{len(bom['components'])} components")
        return
    payload = _json.dumps(bom, indent=2, ensure_ascii=False)
    if output:
        output.write_text(payload + "\n", encoding="utf-8")
        Console().print(f"[green]SBOM written:[/green] {output.resolve()}")
    else:
        typer.echo(payload)


@app.command(name="mcp")
def mcp_cmd(
    root: Annotated[
        Path | None,
        typer.Option(
            "--root",
            help="Sandbox all tool path arguments to this directory tree.",
        ),
    ] = None,
) -> None:
    """Start a zero-dep MCP (JSON-RPC stdio) server for agent integrations."""
    from forge_doctor.integrations.mcp_server import serve

    if root is not None and not root.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {root}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    serve(root=root)


@app.command(name="lsp")
def lsp_cmd() -> None:
    """Start a stdio LSP server (requires the optional 'lsp' extra)."""
    from forge_doctor.integrations.lsp_server import run_stdio

    run_stdio()


@app.command(name="doctor")
def doctor_cmd(path: PathArg = Path(".")) -> None:
    """Self-check: config, plugins, cache dir, git, environment health."""
    console = Console()
    checks: list[tuple[str, str, str]] = []

    git_path = shutil.which("git")
    checks.append(("git", "ok" if git_path else "missing", git_path or "not on PATH"))

    rules = PluginRules()
    if path.is_dir():
        ctx = ProjectContext(root=path)
        try:
            cfg = ctx.config
            rules = cfg.plugins
            checks.append(
                (
                    "config",
                    "ok",
                    f"{len(cfg.suppressions)} suppressions, {len(cfg.policy.rules)} rule overrides",
                )
            )
        except Exception as exc:
            checks.append(("config", "error", str(exc)))
        cache = ScanCache(path)
        try:
            cache.path.parent.mkdir(parents=True, exist_ok=True)
            probe = cache.path.parent / ".probe"
            probe.write_text("ok")
            probe.unlink()
            checks.append(("cache dir", "ok", cache.path.parent.as_posix()))
        except OSError as exc:
            checks.append(("cache dir", "error", str(exc)))
        checks.append(("project files", "ok", f"{len(ctx.files)} files indexed"))
    else:
        checks.append(("project", "error", f"{path} is not a directory"))

    _checks, infos, errors = load_plugins(trusted=rules.trusted, allow=rules.allow)
    if infos or errors:
        bad = [i.name for i in infos if i.status and "untrusted" not in i.status] + errors
        checks.append(
            (
                "plugins",
                "error" if bad else "ok",
                f"{len(infos)} discovered, {len(bad)} problem(s)",
            )
        )
    else:
        checks.append(("plugins", "ok", "none installed"))

    from forge_doctor.core.knowledge import list_packs, verify_pack

    stale = [f"{d}/{n}" for d, n, _p in list_packs() for _ in verify_pack(d, n)]
    checks.append(("knowledge packs", "warning" if stale else "ok", f"{len(stale)} issue(s)"))

    table = Table(title="forge-doctor doctor", title_justify="left")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail", style="dim")
    styles = {"ok": "green", "warning": "yellow", "error": "red", "missing": "red"}
    for name, status, detail in checks:
        table.add_row(name, f"[{styles.get(status, 'white')}]{status}[/]", detail)
    console.print(table)
    if any(s == "error" for _, s, _ in checks):
        raise typer.Exit(1)


@app.command(name="version")
def version_cmd() -> None:
    """Print the installed Forge Doctor version."""
    typer.echo(f"forge-doctor {__version__}")


@app.command(name="diagnose")
def diagnose_cmd(
    source: Annotated[str, typer.Argument(help="Log file path, or '-' to read stdin.")],
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
    path: Annotated[
        Path, typer.Option("--path", help="Project root for repo-evidence correlations.")
    ] = Path("."),
) -> None:
    """Fingerprint log errors against known signatures (deterministic, offline)."""
    import json as _json
    import sys

    from forge_doctor.core.diagnose import diagnose_text, project_correlations

    if source == "-":
        text = sys.stdin.read()
    else:
        log_path = Path(source)
        if not log_path.is_file():
            _stderr.print(f"[red]Not a file:[/red] {source}")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        text = log_path.read_text(encoding="utf-8", errors="replace")

    diagnoses = diagnose_text(text)
    # Repo-evidence correlations only make sense once a governed-domain
    # signature matched; skip the project scan entirely otherwise.
    correlations: list[str] = []
    if path.is_dir() and any(d.signature.domain == "lakeformation" for d in diagnoses):
        correlations = project_correlations(ProjectContext(root=path), diagnoses)
    if fmt == "json":
        typer.echo(
            _json.dumps(
                {
                    "tool": "forge-doctor",
                    "schema_version": SCHEMA_VERSION,
                    "findings": [
                        {
                            "id": d.signature.id,
                            "title": d.signature.title,
                            "severity": d.signature.severity,
                            "domain": d.signature.domain,
                            "count": d.count,
                            "causes": list(d.signature.causes),
                            "fixes": list(d.signature.fixes),
                            "related": list(d.signature.related),
                            "samples": d.samples,
                        }
                        for d in diagnoses
                    ],
                    "correlations": correlations,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console = Console()
    if not diagnoses:
        console.print("[green]No known error signatures matched.[/green]")
        return
    table = Table(title="Detected errors", title_justify="left")
    table.add_column("Id", style="dim")
    table.add_column("Count", justify="right")
    table.add_column("Error", style="bold")
    table.add_column("Severity")
    for d in diagnoses:
        style = {"error": "red", "warning": "yellow"}.get(d.signature.severity, "white")
        table.add_row(
            d.signature.id, str(d.count), d.signature.title, f"[{style}]{d.signature.severity}[/]"
        )
    console.print(table)
    for d in diagnoses:
        console.print(f"\n[bold]{d.signature.id}[/bold] {d.signature.title}")
        for cause in d.signature.causes:
            console.print(f"  [dim]->[/dim] {cause}")
        for fix_hint in d.signature.fixes:
            console.print(f"  [dim]fix:[/dim] {fix_hint}")
        if d.signature.related:
            console.print(f"  [dim]related: {', '.join(d.signature.related)}[/dim]")
    for line in correlations:
        console.print(f"\n[yellow]correlation:[/yellow] {line}")
    raise typer.Exit(1)


@app.command(name="trace")
def trace_cmd(
    check_id: Annotated[str, typer.Argument(help="Check id, e.g. SPARK001.")],
    location: Annotated[str, typer.Argument(help="file:line, e.g. jobs/etl.py:42.")],
    path: Annotated[Path, typer.Option("--path", help="Project root.")] = Path("."),
    as_json: Annotated[bool, typer.Option("--json", help="Emit structured chain.")] = False,
) -> None:
    """Explain ONE finding: evidence, enclosing symbol, receiver chain."""
    import json as _json

    if not path.is_dir():
        _stderr.print(f"[red]Not a directory:[/red] {path}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    file_part, sep, line_part = location.rpartition(":")
    if not sep or not line_part.isdigit():
        _stderr.print("[red]Location must be file:line (e.g. jobs/etl.py:42).[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT)
    target_file = Path(file_part).as_posix().removeprefix("./")
    target_line = int(line_part)

    ctx = ProjectContext(root=path)
    registry, _ = _build_registry(config=ctx.config)
    check = registry.get(check_id.upper())
    if check is None:
        _stderr.print(f"[red]Unknown check id:[/red] {check_id}")
        raise typer.Exit(INTERNAL_ERROR_EXIT)

    results = [
        r
        for r in check.run(ctx)
        if r.file is not None and r.file.as_posix() == target_file and r.line == target_line
    ]
    if not results:
        _stderr.print(f"[red]No {check.id} finding at[/red] {target_file}:{target_line}")
        raise typer.Exit(1)

    result = results[0]
    from forge_doctor.analyzers.index import project_index
    from forge_doctor.core.fingerprint import resolve_symbol

    symbol = result.symbol or resolve_symbol(result, ctx) or "<module>"
    module = project_index(ctx).module(Path(target_file))
    receiver = None
    chain: list[dict[str, object]] = []
    imports: list[str] = []
    if module is not None:
        receiver = (
            result.evidence.split(".")[0] if result.evidence and "." in result.evidence else None
        )
        if receiver:
            chain = [
                {
                    "target": a.target,
                    "line": a.line,
                    "source": a.value_call or a.value_root or "?",
                }
                for a in module.assigns
                if a.target == receiver
            ]
        imports = [
            (f"from {i.module} import {i.name}" if i.is_from else f"import {i.module}")
            for i in module.imports
        ]

    if as_json:
        typer.echo(
            _json.dumps(
                {
                    "check_id": result.check_id,
                    "title": result.title,
                    "file": target_file,
                    "line": target_line,
                    "symbol": symbol,
                    "evidence": result.evidence,
                    "evidence_kind": result.evidence_kind.value if result.evidence_kind else None,
                    "receiver": receiver,
                    "confidence": result.confidence.value if result.confidence else None,
                    "assignment_chain": chain,
                    "imports": imports,
                    "message": result.message,
                    "recommendation": result.recommendation,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console = Console()
    console.print(f"\n[bold]{result.check_id}[/bold] {result.title}")
    console.print(f"[dim]{target_file}:{target_line} in {symbol}[/dim]\n")
    if result.evidence:
        console.print(f"  [cyan]evidence[/cyan]  {result.evidence}")
    if result.evidence_kind:
        console.print(f"  [cyan]kind[/cyan]      {result.evidence_kind.value}")
    if receiver:
        conf = result.confidence.value if result.confidence else "?"
        console.print(f"  [cyan]receiver[/cyan]  {receiver} (confidence: {conf})")
    console.print(f"  [cyan]message[/cyan]   {result.message}")
    if chain:
        console.print("  [cyan]assignments[/cyan]")
        for step in chain:
            console.print(f"    line {step['line']}: {step['target']} = {step['source']}")
    if imports:
        console.print("  [cyan]imports[/cyan]")
        for entry in imports[:8]:
            console.print(f"    {entry}")
    if result.recommendation:
        console.print(f"\n[green]fix[/green] {result.recommendation}")
    console.print()
