"""`forge-doctor search` - search platform inspection (spec 220)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor.analyzers.search_model import search_model
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext

search_app = typer.Typer(
    name="search",
    help="Search platforms: OpenSearch/Elasticsearch indices, policies, domains.",
)
app.add_typer(search_app, name="search")


@search_app.command(name="inspect")
def search_inspect(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
) -> None:
    """Print the search model: indices/templates, policies, pipelines, domains."""
    ctx = ProjectContext(root=path.resolve())
    model = search_model(ctx)
    console = Console()
    console.print()
    console.print("[bold]search platform[/bold]")
    if not model.has_evidence:
        console.print("  no search-platform evidence detected")
        return

    if model.indices:
        console.print("\n[bold]Indices / templates[/bold]")
        for i in model.indices:
            bits = [f"vendor={i.vendor}", f"kind={i.kind}"]
            if i.index_patterns:
                bits.append(f"patterns={','.join(i.index_patterns)}")
            if i.shards or i.replicas:
                bits.append(f"shards={i.shards or '-'} reps={i.replicas or '-'}")
            console.print(f"  {i.name}  {'  '.join(bits)}")
    if model.policies:
        console.print("\n[bold]Lifecycle policies[/bold]")
        for p in model.policies:
            console.print(
                f"  {p.name}  {p.kind.upper()}  "
                f"rollover={'yes' if p.has_rollover else 'no'}  "
                f"retention={'yes' if p.has_retention else 'no'}"
            )
    if model.pipelines:
        console.print("\n[bold]Ingest pipelines[/bold]")
        for pl in model.pipelines:
            console.print(f"  {pl.name}  processors={','.join(pl.processors) or '-'}")
    if model.domains:
        console.print("\n[bold]Terraform domains[/bold]")
        for d in model.domains:
            console.print(
                f"  {d.name}  {d.vendor}  "
                f"encrypt_at_rest={d.encryption_at_rest or 'unset'}  "
                f"node_to_node={d.node_to_node or 'unset'}  "
                f"tls={d.https_tls or 'unset'}"
            )
    if model.observed:
        by_vendor: dict[str, int] = {}
        for r in model.observed:
            by_vendor[r.vendor] = by_vendor.get(r.vendor, 0) + 1
        console.print(
            "\n[bold]Observed[/bold]  "
            + ", ".join(f"{v}: {n} rows" for v, n in sorted(by_vendor.items()))
        )
    if model.unparsed:
        console.print("\n[bold]Unparsed[/bold]")
        for f in model.unparsed:
            console.print(f"  {f}")
    console.print()
