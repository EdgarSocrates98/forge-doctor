"""Utility commands: init, info, explain, checks, cache, suppressions,
lineage, schema, knowledge, sbom, mcp, lsp, doctor, graph, diagnose, trace."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from forge_doctor.core.schema import SchemaChange

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.api import SCHEMA_VERSION
from forge_doctor.cli.app import app
from forge_doctor.cli.common import (
    PathArg,
    _build_registry,
    _ref_schemas,
    _stderr,
)
from forge_doctor.core.cache import ScanCache
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.project_info import collect_info
from forge_doctor.core.runner import CheckRunner
from forge_doctor.core.scaffold import scaffold
from forge_doctor.output.summary import INTERNAL_ERROR_EXIT


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
            f"[green]{s.approved_by}[/]" if s.approved_by else "[red]UNAPPROVED[/]",
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


contracts_app = typer.Typer(
    name="contracts", help="Published artifact contracts (verify handoff bundles, schemas)."
)
app.add_typer(contracts_app, name="contracts")


@contracts_app.command(name="list")
def contracts_list() -> None:
    """List the published contract names (see `schema contracts <name>` for a dump)."""
    from forge_doctor.core.schemas import SCHEMAS

    console = Console()
    for key in sorted(SCHEMAS):
        console.print(f"  [bold]{key}[/bold]")


@contracts_app.command(name="verify")
def contracts_verify(
    bundle: Annotated[
        str, typer.Argument(help="JSON artifact to validate, or '-' for stdin.")
    ] = "-",
    contract: Annotated[
        str, typer.Option("--contract", help="Contract name (see `contracts list`).")
    ] = "handoff-bundle",
) -> None:
    """Validate a JSON artifact against a published contract (stdin or file).

    ``forge-doctor export --format handoff`` output validates against
    ``handoff-bundle``; other Forge tools use this in their own tests.
    """
    import sys

    from forge_doctor.core.contract_check import verify_contract

    label = "stdin"
    try:
        if bundle == "-":
            raw = sys.stdin.read()
        else:
            label = Path(bundle).name
            raw = Path(bundle).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        _stderr.print(f"[red]unreadable JSON:[/red] {exc}")
        raise typer.Exit(1) from exc
    errors = verify_contract(payload, contract)
    console = Console()
    if errors:
        for err in errors:
            console.print(f"  [red]invalid[/red] {err}")
        raise typer.Exit(1)
    console.print(f"[green]{label} satisfies contract '{contract}'[/green]")


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


@knowledge_app.command(name="new")
def knowledge_new(
    domain: Annotated[str, typer.Argument(help="Pack domain (e.g. snowflake).")],
    kind: Annotated[
        str, typer.Option("--kind", help="versions|errors|capabilities|compatibility")
    ] = "versions",
    directory: Annotated[
        Path | None, typer.Option("--dir", help="Knowledge root (default: bundled).")
    ] = None,
) -> None:
    """Scaffold a new knowledge pack with provenance fields + examples."""
    from forge_doctor.core.knowledge import SCAFFOLD_KINDS, write_scaffold

    root = directory or _bundled_knowledge_root()
    console = Console()
    try:
        target = write_scaffold(root, domain, kind)
    except FileExistsError as exc:
        _stderr.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except ValueError as exc:
        _stderr.print(f"[red]{exc}[/red]  kinds: {', '.join(SCAFFOLD_KINDS)}")
        raise typer.Exit(1) from exc
    console.print(f"[green]created[/green] {target}")


def _bundled_knowledge_root() -> Path:
    from importlib.resources import files

    return Path(str(files("forge_doctor") / "knowledge"))


@knowledge_app.command(name="diff")
def knowledge_diff(
    a: Annotated[str, typer.Argument(help="Pack ref: <domain>/<name> or a JSON path.")],
    b: Annotated[str, typer.Argument(help="Pack ref: <domain>/<name> or a JSON path.")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Semantic pack diff: entries added/removed/changed (not text diff)."""
    from forge_doctor.core.knowledge import diff_packs, load_pack_ref

    old, new = load_pack_ref(a), load_pack_ref(b)
    if old is None or new is None:
        _stderr.print("[red]could not load a pack ref[/red] (use domain/name or a JSON path)")
        raise typer.Exit(1)
    rows = diff_packs(old, new)
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    console = Console()
    if not rows:
        console.print("[green]no content differences[/green]")
        return
    for row in rows:
        color = {"added": "green", "removed": "red"}.get(row["change"], "yellow")
        console.print(f"  [{color}]{row['change']}[/{color}] {row['section']}:{row['id']}")
        for detail in row["details"]:
            console.print(f"      [dim]{detail}[/dim]")


@knowledge_app.command(name="test")
def knowledge_test(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Pack conformance suite: structure, regexes, examples, capabilities."""
    from forge_doctor.core.knowledge import conformance

    report = conformance()
    if as_json:
        typer.echo(json.dumps(report, indent=2))
        if report["issues"]:
            raise typer.Exit(1)
        return
    console = Console()
    issues, warnings = report["issues"], report["warnings"]
    for issue in issues:
        console.print(f"  [red]issue[/red]   {issue}")
    for warning in warnings:
        console.print(f"  [yellow]warning[/yellow] {warning}")
    console.print(f"\n  {len(issues)} issue(s), {len(warnings)} warning(s)")
    if issues:
        raise typer.Exit(1)


@knowledge_app.command(name="publish")
def knowledge_publish(
    domain: Annotated[str, typer.Argument(help="Pack domain to validate for publish.")],
    bump: Annotated[bool, typer.Option("--bump", help="Rewrite pack_version/verified_at.")] = False,
    directory: Annotated[
        Path | None, typer.Option("--dir", help="Knowledge root (default: bundled).")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Publish checklist: freshness fields valid, conformance clean."""
    from forge_doctor.core.knowledge import bump_pack, publish_checklist

    report = publish_checklist(domain)
    if "error" in report:
        _stderr.print(f"[red]{report['error']}[/red] for domain {domain}")
        raise typer.Exit(1)
    written: list[str] = []
    if bump and report["ready"]:
        root = directory or _bundled_knowledge_root()
        written = [p.as_posix() for p in bump_pack(root, domain)]
    if as_json:
        typer.echo(json.dumps({**report, "written": written}, indent=2))
        if not report["ready"]:
            raise typer.Exit(1)
        return
    console = Console()
    console.print(f"[bold]Publish checklist[/bold]  {domain}")
    for row in report["packs"]:
        status = "[green]ok[/green]" if row["ok"] else "[red]blocked[/red]"
        console.print(
            f"  {status} {row['pack']}  v{row['pack_version']} "
            f"verified {row['verified_at']} sources={row['sources']}"
        )
        for issue in row["issues"]:
            console.print(f"      [dim]{issue}[/dim]")
    console.print(f"  next pack_version: {report['next_pack_version']}")
    if written:
        console.print(f"  [green]bumped[/green] {len(written)} pack(s)")
    console.print(f"  [dim]{report['note']}[/dim]")
    if not report["ready"]:
        raise typer.Exit(1)
