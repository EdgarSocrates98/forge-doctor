"""Golden repositories - full-output snapshots for regression protection.

Unlike Forge Lab (partial ground-truth assertions), a golden repo stores
the *complete* deterministic engine output: findings, platform graph,
root-cause clusters, remediation plans, migration plans. Any semantic
regression shows up as a diff in the snapshot.

Layout::

    golden/<name>/
      repo/                  # the mini-project (files, IaC, runtime/)
      expected/
        findings.json        # [{check_id,severity,file,line,fingerprint}]
        graph.json           # {entities: [...], relationships: [...]}
        root_causes.json     # [{id, title, confidence}]
        remediations.json    # [{id, title, targets, actions}]
        migrations.json      # [{id, target, blockers, warnings}]

``golden update`` regenerates snapshots; a human review of the diff is
what blesses them. ``golden run`` fails on any drift. Deterministic:
sorted keys/items everywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge_doctor.core.models import Severity

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

GOLDEN_ROOT = "golden"
REPO_DIR = "repo"
EXPECTED_DIR = "expected"
ARTIFACTS = ("findings", "graph", "root_causes", "remediations", "migrations")
_FINDING_SEVERITIES = frozenset({Severity.INFO, Severity.WARNING, Severity.ERROR})


@dataclass(frozen=True)
class ArtifactDiff:
    """Diff between stored snapshot and current engine output."""

    artifact: str
    added: tuple[str, ...] = ()  # present now, absent from snapshot
    removed: tuple[str, ...] = ()  # in snapshot, absent now

    @property
    def ok(self) -> bool:
        return not self.added and not self.removed


@dataclass
class GoldenReport:
    name: str
    path: Path
    diffs: list[ArtifactDiff] = field(default_factory=list)
    missing_snapshot: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(d.ok for d in self.diffs) and not self.missing_snapshot and not self.errors


def _finding_row(r: Any) -> dict[str, Any]:
    return {
        "check_id": r.check_id,
        "severity": r.severity.value,
        "file": r.file.as_posix() if r.file else None,
        "line": r.line,
        "fingerprint": r.fingerprint,
    }


def _row_key(row: Any) -> str:
    return json.dumps(row, sort_keys=True)


def snapshot_findings(results: list[Any]) -> list[dict[str, Any]]:
    rows = [_finding_row(r) for r in results if r.severity in _FINDING_SEVERITIES]
    return sorted(rows, key=_row_key)


def snapshot_graph(ctx: ProjectContext) -> dict[str, Any]:
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    g = build_platform_graph(ctx)
    entities = [
        {
            "kind": e.kind.value,
            "domain": e.domain,
            "id": e.id,
            "file": e.file.as_posix() if e.file else None,
            "line": e.line,
        }
        for e in g.entities()
    ]
    rels = [
        {"kind": rel.kind.value.lower(), "src": rel.src, "dst": rel.dst}
        for rel in g.relationships()
    ]
    return {
        "entities": sorted(entities, key=_row_key),
        "relationships": sorted(rels, key=_row_key),
    }


def snapshot_root_causes(results: list[Any], models: list[Any]) -> list[dict[str, Any]]:
    from forge_doctor.core.diagnosis import cluster_findings

    clusters = cluster_findings(results, models)
    rows = [
        {
            "id": c.id,
            "title": c.title,
            "confidence": c.confidence.value,
            "affected_entities": sorted(c.affected_entities),
        }
        for c in clusters
    ]
    return sorted(rows, key=_row_key)


def snapshot_remediations(results: list[Any], models: list[Any]) -> list[dict[str, Any]]:
    from forge_doctor.core.diagnosis import cluster_findings
    from forge_doctor.core.remediation import plan_remediation

    clusters = cluster_findings(results, models)
    plans = plan_remediation(results, clusters)
    rows = [
        {
            "id": p.id,
            "check_id": p.check_id,
            "problem": p.problem,
            "targets": sorted(p.targets),
            "actions": [a.description for a in p.actions],
        }
        for p in plans
    ]
    return sorted(rows, key=_row_key)


def snapshot_migrations(ctx: ProjectContext) -> list[dict[str, Any]]:
    from forge_doctor.core.migration import plan_migrations

    rows = [
        {
            "path_id": p.path_id,
            "source": p.source_environment,
            "target": p.target_environment,
            "affected_entities": sorted(p.affected_entities),
            "blockers": sorted(p.blockers),
            "warnings": sorted(p.warnings),
        }
        for p in plan_migrations(ctx)
    ]
    return sorted(rows, key=_row_key)


def snapshot_project(ctx: ProjectContext, repo_dir: Path) -> dict[str, list[Any] | dict[str, Any]]:
    """Full deterministic engine output for one golden repo."""
    from forge_doctor.checks import builtin_checks
    from forge_doctor.core.lab import _runtime_models

    results: list[Any] = []
    for check in builtin_checks():
        results.extend(check.run(ctx))
    models = _runtime_models(repo_dir)
    return {
        "findings": snapshot_findings(results),
        "graph": snapshot_graph(ctx),
        "root_causes": snapshot_root_causes(results, models),
        "remediations": snapshot_remediations(results, models),
        "migrations": snapshot_migrations(ctx),
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _diff(artifact: str, expected: Any, actual: Any) -> ArtifactDiff:
    exp_rows = {_row_key(r) for r in (expected if isinstance(expected, list) else [])}
    if isinstance(expected, dict) and isinstance(actual, dict):
        diff = ArtifactDiff(artifact)
        for key in sorted(set(expected) | set(actual)):
            sub = _diff(f"{artifact}.{key}", expected.get(key, []), actual.get(key, []))
            diff = ArtifactDiff(
                artifact=diff.artifact,
                added=diff.added + sub.added,
                removed=diff.removed + sub.removed,
            )
        return diff
    act_rows = {_row_key(r) for r in (actual if isinstance(actual, list) else [])}
    return ArtifactDiff(
        artifact=artifact,
        added=tuple(sorted(act_rows - exp_rows)),
        removed=tuple(sorted(exp_rows - act_rows)),
    )


def discover_golden(golden_root: Path) -> list[Path]:
    """``golden/<name>/repo`` dirs — the repo dir itself, never its parent.

    (Scanning the parent would let ``expected/`` snapshot text contaminate
    the evidence scan — snapshot JSON mentions domain tokens.)
    """
    if not golden_root.is_dir():
        return []
    return sorted(p for p in golden_root.rglob(REPO_DIR) if p.is_dir())


def run_golden(repo_dir: Path) -> GoldenReport:
    from forge_doctor.core.context import ProjectContext, ScanOptions

    name_dir = repo_dir.parent
    expected_dir = name_dir / EXPECTED_DIR
    report = GoldenReport(name=name_dir.name, path=repo_dir)
    ctx = ProjectContext(root=repo_dir.resolve(), options=ScanOptions(hermetic=True))
    try:
        actual = snapshot_project(ctx, repo_dir)
    except Exception as exc:
        report.errors.append(f"snapshot failed: {exc}")
        return report
    for artifact in ARTIFACTS:
        snap_path = expected_dir / f"{artifact}.json"
        if not snap_path.is_file():
            report.missing_snapshot.append(artifact)
            continue
        try:
            expected = _load_json(snap_path)
        except (OSError, json.JSONDecodeError) as exc:
            report.errors.append(f"{artifact}: unreadable snapshot: {exc}")
            continue
        diff = _diff(artifact, expected, actual[artifact])
        if not diff.ok:
            report.diffs.append(diff)
    return report


def update_golden(repo_dir: Path) -> Path:
    """Regenerate all snapshot artifacts for one golden repo."""
    from forge_doctor.core.context import ProjectContext, ScanOptions

    expected_dir = repo_dir.parent / EXPECTED_DIR
    expected_dir.mkdir(parents=True, exist_ok=True)
    ctx = ProjectContext(root=repo_dir.resolve(), options=ScanOptions(hermetic=True))
    snapshot = snapshot_project(ctx, repo_dir)
    for artifact, data in snapshot.items():
        (expected_dir / f"{artifact}.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return expected_dir


def run_all_golden(golden_root: Path, name: str | None = None) -> list[GoldenReport]:
    reports = []
    for repo_dir in discover_golden(golden_root):
        if name and repo_dir.parent.name != name:
            continue
        reports.append(run_golden(repo_dir))
    return reports
