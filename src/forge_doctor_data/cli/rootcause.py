"""``forge-doctor-data root-cause`` - promote findings with runtime evidence and
cluster them into causal chains."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from forge_doctor_data.analyzers.runtime_evidence import ingest_artifact
from forge_doctor_data.cli.app import app
from forge_doctor_data.cli.common import _build_registry
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.diagnosis import (
    FindingCluster,
    FindingPromotion,
    PromotionLevel,
    cluster_findings,
    promote_findings,
)
from forge_doctor_data.core.runner import CheckRunner

console = Console()

_LEVEL_COLOR = {
    PromotionLevel.CONFIRMED: "red",
    PromotionLevel.STRONGLY_SUPPORTED: "yellow",
    PromotionLevel.POSSIBLE: "dim",
}


@app.command(name="root-cause")
def root_cause(
    path: Annotated[Path, typer.Argument(help="Project root.")] = Path("."),
    runtime: Annotated[
        list[Path], typer.Option("--runtime", help="Exported runtime artifact(s).")
    ] = [],
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Correlate scan findings with runtime evidence into causal clusters."""
    ctx = ProjectContext(root=path.resolve())
    registry, _ = _build_registry(config=ctx.config)
    report = CheckRunner(registry).run(ctx)
    models = [ingest_artifact(p) for p in runtime]
    promotions = promote_findings(report.results, models)
    clusters = cluster_findings(report.results, models)

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "promotions": [_promotion_dict(p) for p in promotions],
                    "clusters": [_cluster_dict(c) for c in clusters],
                },
                indent=2,
            )
        )
        return

    console.print()
    console.print("[bold]Root Cause[/bold]")
    if not runtime:
        console.print(
            "  [dim]no --runtime artifact given - promotions need runtime evidence;"
            " clusters reflect static findings only[/dim]"
        )
    if not promotions and not clusters:
        console.print("  no promotions or causal clusters")
        return

    if promotions:
        console.print("\n  [bold]Promotions[/bold]")
        for p in promotions:
            color = _LEVEL_COLOR[p.level]
            console.print(f"    [{color}]{p.level.value}[/{color}] {p.check_id} {p.title}")
            console.print(
                f"      base={p.base_fingerprint} promotion={p.promotion_id}"
                f" -> {p.resulting_confidence.value}/{p.resulting_severity.value}"
            )
            for ev in p.confirming_evidence:
                console.print(f"      + {ev}")
            console.print(f"      {p.explanation}")

    if clusters:
        console.print("\n  [bold]Causal clusters[/bold]")
        for c in clusters:
            color = _LEVEL_COLOR[c.confidence]
            console.print(f"    [{color}]{c.confidence.value}[/{color}] {c.id}")
            console.print(f"      {c.title}")
            for edge in c.causal_edges:
                console.print(f"        {edge.source} --{edge.label}--> {edge.target}")
            for ev in c.evidence:
                console.print(f"      evidence: {ev}")
            if c.affected_entities:
                console.print(f"      entities: {', '.join(c.affected_entities)}")
    console.print()


def _promotion_dict(p: FindingPromotion) -> dict[str, object]:
    return {
        "base_fingerprint": p.base_fingerprint,
        "promotion_id": p.promotion_id,
        "check_id": p.check_id,
        "title": p.title,
        "level": p.level.value,
        "resulting_confidence": p.resulting_confidence.value,
        "resulting_severity": p.resulting_severity.value,
        "confirming_evidence": list(p.confirming_evidence),
        "explanation": p.explanation,
    }


def _cluster_dict(c: FindingCluster) -> dict[str, object]:
    return {
        "id": c.id,
        "title": c.title,
        "confidence": c.confidence.value,
        "root_causes": list(c.root_causes),
        "symptoms": list(c.symptoms),
        "related_findings": list(c.related_findings),
        "affected_entities": list(c.affected_entities),
        "evidence": list(c.evidence),
        "causal_edges": [
            {"source": e.source, "target": e.target, "label": e.label} for e in c.causal_edges
        ],
    }
