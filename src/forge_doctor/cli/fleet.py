"""``forge-doctor fleet`` - estate intelligence across a manifest of repos."""

from __future__ import annotations

from collections import Counter
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from forge_doctor.api import SCHEMA_VERSION
from forge_doctor.cli.app import app
from forge_doctor.cli.common import _stderr
from forge_doctor.core.change_intel import VERSION_ATTRS as _VERSION_ATTRS
from forge_doctor.core.fleet import (
    FleetManifest,
    FleetManifestError,
    build_fleet_model,
    load_manifest,
)
from forge_doctor.core.models import CheckResult
from forge_doctor.core.workspace import WorkspaceModel
from forge_doctor.output.summary import INTERNAL_ERROR_EXIT

fleet_app = typer.Typer(
    name="fleet", help="Fleet/estate intelligence across many repos.", no_args_is_help=False
)
app.add_typer(fleet_app, name="fleet")

ManifestArg = Annotated[Path, typer.Argument(help="Fleet manifest (yaml/json) or a directory.")]


def _load(spec: Path) -> tuple[FleetManifest, WorkspaceModel]:
    try:
        manifest = load_manifest(spec.resolve())
    except FleetManifestError as exc:
        _stderr.print(f"[red]{exc}[/red]")
        raise typer.Exit(INTERNAL_ERROR_EXIT) from None
    return manifest, build_fleet_model(manifest)


def _scan_repos(model: WorkspaceModel) -> dict[str, list[CheckResult]]:
    """Run built-in checks per repo (deterministic: no plugins)."""
    from forge_doctor.cli.common import _build_registry
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.runner import CheckRunner

    registry, _ = _build_registry(no_plugins=True)
    runner = CheckRunner(registry)
    findings: dict[str, list[CheckResult]] = {}
    for repo in model.repositories:
        repo_dir = model.root if repo.path == "." else model.root / repo.path
        if not repo_dir.is_dir():
            continue
        report = runner.run(ProjectContext(root=repo_dir))
        findings[repo.name] = [r for r in report.results]
    return findings


@fleet_app.callback(invoke_without_command=True)
def fleet_default(ctx: typer.Context) -> None:
    """Fleet/estate intelligence over a manifest of repositories."""
    if ctx.invoked_subcommand is not None:
        return
    _stderr.print("[dim]Usage: forge-doctor fleet inspect|query|report <manifest|dir>[/dim]")
    raise typer.Exit(0)


@fleet_app.command(name="inspect")
def fleet_inspect(spec: ManifestArg) -> None:
    """Estate census: repos, merged platform graph, cross-repo links."""
    manifest, model = _load(spec)
    console = Console()
    summary = model.summary()
    table = Table(title=f"Fleet: {manifest.name}", title_justify="left")
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("repositories", str(summary["repositories"]))
    table.add_row("entities", str(summary["entities"]))
    table.add_row("relationships", str(summary["relationships"]))
    table.add_row("cross_repo_links", str(summary["cross_repo_links"]))
    console.print(table)
    repos = Table(title="Repositories", title_justify="left")
    repos.add_column("Repo", style="bold")
    repos.add_column("Path", style="dim")
    for repo in model.repositories:
        repos.add_row(repo.name, repo.path)
    console.print(repos)


@fleet_app.command(name="query")
def fleet_query(
    spec: ManifestArg,
    what: Annotated[str, typer.Argument(help="runtimes|capability|dependents|findings")],
    target: Annotated[str, typer.Argument(help="capability id, entity glob, or check id")] = "",
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Deterministic estate queries over the merged fleet graph."""
    import json as _json

    _, model = _load(spec)
    console = Console()

    if what == "runtimes":
        counts: Counter[str] = Counter()
        versions: Counter[tuple[str, str, str]] = Counter()
        for ent in model.graph.entities():
            counts[ent.domain] += 1
            for key in _VERSION_ATTRS:
                value = ent.attr(key)
                if value:
                    versions[(ent.domain, key, value)] += 1
        payload = {
            "domains": dict(sorted(counts.items())),
            "versions": [
                {"domain": d, "attr": k, "value": v, "entities": n}
                for (d, k, v), n in sorted(versions.items())
            ],
        }
        if fmt == "json":
            typer.echo(_json.dumps({"schema_version": SCHEMA_VERSION, **payload}, indent=2))
            return
        table = Table(title="Estate runtimes", title_justify="left")
        table.add_column("Domain", style="bold")
        table.add_column("Entities", justify="right")
        for domain, n in sorted(counts.items()):
            table.add_row(domain, str(n))
        console.print(table)
        vtable = Table(title="Versioned entities", title_justify="left")
        vtable.add_column("Domain")
        vtable.add_column("Attr")
        vtable.add_column("Version", style="bold")
        vtable.add_column("Entities", justify="right")
        for (d, k, v), n in sorted(versions.items()):
            vtable.add_row(d, k, v, str(n))
        console.print(vtable)
        return

    if what == "capability":
        if not target:
            _stderr.print("[red]fleet query capability requires a capability id.[/red]")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        from forge_doctor.core.capabilities import (
            CapabilityContext,
            CapabilityStatus,
            capability_registry,
        )

        registry = capability_registry()
        buckets: dict[str, list[str]] = {s.value: [] for s in CapabilityStatus}
        for ent in model.graph.entities():
            version = next((ent.attr(k) for k in _VERSION_ATTRS if ent.attr(k)), None)
            result = registry.evaluate(
                target,
                CapabilityContext(
                    platform=ent.domain,
                    version=version,
                    attributes=ent.attrs,
                ),
            )
            if result.status is CapabilityStatus.UNKNOWN:
                continue  # entity's domain has no facts for this capability
            buckets[result.status.value].append(ent.id)
        if fmt == "json":
            typer.echo(
                _json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "capability": target,
                        **{k: sorted(v) for k, v in buckets.items() if v},
                    },
                    indent=2,
                )
            )
            return
        for status in ("unsupported", "conditional", "supported"):
            ids = sorted(buckets[status])
            if not ids:
                continue
            console.print(f"[bold]{status.upper()}[/bold] ({len(ids)})")
            for eid in ids:
                console.print(f"  {eid}")
        return

    if what == "dependents":
        if not target:
            _stderr.print("[red]fleet query dependents requires an entity id or glob.[/red]")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        from forge_doctor.core.semantic_diff import blast_radius

        matched = {e.id for e in model.graph.entities() if fnmatch(e.id, target)}
        if not matched:
            console.print(f"[dim]No entities match '{target}'.[/dim]")
            return
        dependents: dict[str, set[str]] = {}
        for eid in sorted(matched):
            for dep in blast_radius(model.graph, eid):
                if dep not in matched:
                    dependents.setdefault(dep, set()).add(eid)
        if fmt == "json":
            typer.echo(
                _json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "matched": sorted(matched),
                        "dependents": {k: sorted(v) for k, v in sorted(dependents.items())},
                    },
                    indent=2,
                )
            )
            return
        console.print(f"[bold]{len(matched)}[/bold] entities match '{target}'")
        for eid in sorted(matched):
            console.print(f"  {eid}")
        console.print(f"[bold]{len(dependents)}[/bold] dependents")
        for dep, on in sorted(dependents.items()):
            console.print(f"  {dep} [dim]<- {', '.join(sorted(on))}[/dim]")
        return

    if what == "findings":
        if not target:
            _stderr.print("[red]fleet query findings requires a check id or glob.[/red]")
            raise typer.Exit(INTERNAL_ERROR_EXIT)
        per_repo = _scan_repos(model)
        rows: list[tuple[str, CheckResult]] = []
        for repo_name, results in per_repo.items():
            for r in results:
                if fnmatch(r.check_id, target.upper()) or fnmatch(r.check_id, target):
                    rows.append((repo_name, r))
        if fmt == "json":
            from forge_doctor.output.json_renderer import result_to_dict

            typer.echo(
                _json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "check_id": target,
                        "findings": [{"repo": repo, **result_to_dict(r)} for repo, r in rows],
                    },
                    indent=2,
                )
            )
            return
        table = Table(title=f"Findings matching {target}", title_justify="left")
        table.add_column("Repo", style="bold")
        table.add_column("Severity")
        table.add_column("Location", style="dim")
        table.add_column("Message")
        for repo, r in rows:
            loc = r.file.as_posix() if r.file else "-"
            table.add_row(repo, r.severity.value, loc, r.message[:60])
        console.print(table)
        console.print(f"[dim]{len(rows)} finding(s) across {len(model.repositories)} repos[/dim]")
        return

    _stderr.print(f"[red]Unknown query:[/red] {what} (runtimes|capability|dependents|findings)")
    raise typer.Exit(INTERNAL_ERROR_EXIT)


@fleet_app.command(name="report")
def fleet_report(
    spec: ManifestArg,
    fmt: Annotated[str, typer.Option("--format", "-f", help="text|json")] = "text",
) -> None:
    """Estate census + per-repo findings roll-up (built-in checks only)."""
    import json as _json

    manifest, model = _load(spec)
    per_repo = _scan_repos(model)
    console = Console()

    by_kind: Counter[str] = Counter()
    by_domain: Counter[str] = Counter()
    for ent in model.graph.entities():
        by_kind[ent.kind.value] += 1
        by_domain[ent.domain] += 1
    sev_counts: Counter[str] = Counter()
    cat_counts: Counter[str] = Counter()
    repo_counts: Counter[str] = Counter()
    for repo_name, results in per_repo.items():
        for r in results:
            sev_counts[r.severity.value] += 1
            cat_counts[r.category] += 1
            repo_counts[repo_name] += 1

    if fmt == "json":
        typer.echo(
            _json.dumps(
                {
                    "tool": "forge-doctor",
                    "schema_version": SCHEMA_VERSION,
                    "fleet": manifest.name,
                    "repositories": [{"name": r.name, "path": r.path} for r in model.repositories],
                    "entities": {
                        "by_kind": dict(sorted(by_kind.items())),
                        "by_domain": dict(sorted(by_domain.items())),
                    },
                    "findings": {
                        "by_severity": dict(sorted(sev_counts.items())),
                        "by_category": dict(sorted(cat_counts.items())),
                        "by_repo": dict(sorted(repo_counts.items())),
                    },
                    "cross_repo_links": len(model.links),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    console.print(f"[bold]Fleet:[/bold] {manifest.name} — {len(model.repositories)} repos")
    kt = Table(title="Entities by kind", title_justify="left")
    kt.add_column("Kind")
    kt.add_column("Count", justify="right")
    for kind, n in sorted(by_kind.items()):
        kt.add_row(kind, str(n))
    console.print(kt)
    dt = Table(title="Entities by domain", title_justify="left")
    dt.add_column("Domain")
    dt.add_column("Count", justify="right")
    for domain, n in sorted(by_domain.items()):
        dt.add_row(domain, str(n))
    console.print(dt)
    ft = Table(title="Findings", title_justify="left")
    ft.add_column("Repo", style="bold")
    ft.add_column("Findings", justify="right")
    for repo_name, n in sorted(repo_counts.items()):
        ft.add_row(repo_name, str(n))
    console.print(ft)
    console.print(
        f"[bold]{sum(sev_counts.values())}[/bold] findings "
        f"({dict(sorted(sev_counts.items()))}); "
        f"{len(model.links)} cross-repo links"
    )
