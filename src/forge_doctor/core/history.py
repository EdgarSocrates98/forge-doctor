"""Historical intelligence: point-in-time snapshots + series analysis.

``scan --record`` appends a compact snapshot to
``.forge-doctor/history/<utc-timestamp>.json`` — summary counts, finding
fingerprints (the stable join key, same scheme baselines use), entity
census, capability states, and contract-drift findings. ``history`` /
``history diff`` / ``history trend`` read snapshots only — they never
re-scan. Snapshots are additive records; ``--keep N`` prunes oldest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import ScanReport

SNAPSHOT_FORMAT = "forge-doctor/history@1"
HISTORY_DIRNAME = "history"

_DRIFT_PREFIX = "ARCH"


class HistoryError(ValueError):
    """Raised when a snapshot cannot be read or resolved."""


@dataclass(frozen=True)
class HistorySnapshot:
    """One recorded scan state."""

    path: Path
    created: str
    summary: dict[str, int]
    findings: tuple[dict[str, Any], ...]
    entity_ids: tuple[str, ...]
    entity_counts: dict[str, dict[str, int]]  # {"by_kind": {...}, "by_domain": {...}}
    capabilities: dict[str, str]  # "platform/CAP" -> status
    drift_ids: tuple[str, ...]  # ARCH* finding keys present

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class SnapshotDiff:
    """Deterministic delta between two snapshots."""

    older: str
    newer: str
    new_findings: tuple[dict[str, Any], ...] = ()
    resolved_findings: tuple[dict[str, Any], ...] = ()
    entities_added: tuple[str, ...] = ()
    entities_removed: tuple[str, ...] = ()
    capability_transitions: tuple[dict[str, str], ...] = ()
    drift_added: tuple[str, ...] = ()
    drift_resolved: tuple[str, ...] = ()


def history_dir(root: Path) -> Path:
    return root / ".forge-doctor" / HISTORY_DIRNAME


def record_snapshot(report: ScanReport, root: Path, ctx: ProjectContext) -> Path:
    """Append a snapshot; returns the written path. Never runs outside
    explicit ``--record`` — normal scans leave history untouched."""
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor.core.baseline import result_key
    from forge_doctor.core.capabilities import (
        CapabilityContext,
        capability_registry,
    )

    graph = build_platform_graph(ctx)
    findings: list[dict[str, Any]] = [
        {
            "check_id": r.check_id,
            "key": result_key(r),
            "severity": r.severity.value,
            "file": r.file.as_posix() if r.file else None,
            "category": r.category,
        }
        for r in report.results
        if r.fingerprint
    ]
    by_kind: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    entity_ids: list[str] = []
    for ent in graph.entities():
        entity_ids.append(ent.id)
        by_kind[ent.kind.value] = by_kind.get(ent.kind.value, 0) + 1
        by_domain[ent.domain] = by_domain.get(ent.domain, 0) + 1

    registry = capability_registry()
    capabilities: dict[str, str] = {}
    for platform in sorted(by_domain):
        for cap in registry.capabilities_for(platform):
            result = registry.evaluate(cap, CapabilityContext(platform=platform))
            capabilities[f"{platform}/{cap}"] = result.status.value

    summary: dict[str, int] = {"errors": 0, "warnings": 0, "infos": 0, "passed": 0}
    for r in report.results:
        key = {"error": "errors", "warning": "warnings", "info": "infos"}.get(
            r.severity.value, "passed"
        )
        summary[key] += 1
    summary["total"] = len(report.results)

    drift_ids = tuple(
        sorted({f["key"] for f in findings if f["check_id"].startswith(_DRIFT_PREFIX)})
    )
    payload = {
        "format": SNAPSHOT_FORMAT,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "tool_version": report.version,
        "project": report.project.name,
        "summary": summary,
        "findings": sorted(findings, key=lambda f: f["key"]),
        "entities": {
            "ids": sorted(entity_ids),
            "by_kind": dict(sorted(by_kind.items())),
            "by_domain": dict(sorted(by_domain.items())),
        },
        "capabilities": capabilities,
        "drift_ids": sorted(drift_ids),
    }
    target_dir = history_dir(root)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = target_dir / f"{stamp}.json"
    # Same-second collision (tests / --watch loops): disambiguate with a
    # suffix that sorts *after* the base name ('_' > '.' in ASCII).
    n = 1
    while path.exists():
        path = target_dir / f"{stamp}_{n}.json"
        n += 1
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def list_snapshots(root: Path) -> list[Path]:
    """Snapshot paths oldest→newest (timestamp-named, sorted)."""
    d = history_dir(root)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix == ".json" and p.is_file())


def load_snapshot(path: Path) -> HistorySnapshot:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoryError(f"cannot read snapshot {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != SNAPSHOT_FORMAT:
        raise HistoryError(f"{path}: not a {SNAPSHOT_FORMAT} snapshot")
    entities = data.get("entities", {})
    return HistorySnapshot(
        path=path,
        created=str(data.get("created", "")),
        summary={str(k): int(v) for k, v in data.get("summary", {}).items()},
        findings=tuple(data.get("findings", [])),
        entity_ids=tuple(entities.get("ids", [])),
        entity_counts={
            "by_kind": entities.get("by_kind", {}),
            "by_domain": entities.get("by_domain", {}),
        },
        capabilities={str(k): str(v) for k, v in data.get("capabilities", {}).items()},
        drift_ids=tuple(data.get("drift_ids", [])),
    )


def resolve_snapshot(root: Path, ref: str) -> Path:
    """Resolve a snapshot by filename, name without suffix, or list index
    (0-based as shown by ``forge-doctor history``, oldest first)."""
    snaps = list_snapshots(root)
    if ref.isdigit():
        idx = int(ref)
        if 0 <= idx < len(snaps):
            return snaps[idx]
        raise HistoryError(f"snapshot index {idx} out of range (0-{len(snaps) - 1})")
    for p in snaps:
        if ref in (p.name, p.stem):
            return p
    raise HistoryError(f"no snapshot '{ref}' - run `forge-doctor history` to list them")


def diff_snapshots(older: HistorySnapshot, newer: HistorySnapshot) -> SnapshotDiff:
    """New/resolved findings by fingerprint key, entity adds/removals,
    capability transitions, and drift changes — oldest→newest."""
    old_by_key = {f["key"]: f for f in older.findings}
    new_by_key = {f["key"]: f for f in newer.findings}
    new_findings = [new_by_key[k] for k in sorted(new_by_key.keys() - old_by_key.keys())]
    resolved = [old_by_key[k] for k in sorted(old_by_key.keys() - new_by_key.keys())]

    old_ids = set(older.entity_ids)
    new_ids = set(newer.entity_ids)

    transitions = [
        {"capability": cap, "from": older.capabilities[cap], "to": newer.capabilities[cap]}
        for cap in sorted(set(older.capabilities) & set(newer.capabilities))
        if older.capabilities[cap] != newer.capabilities[cap]
    ]
    old_drift, new_drift = set(older.drift_ids), set(newer.drift_ids)

    return SnapshotDiff(
        older=older.name,
        newer=newer.name,
        new_findings=tuple(new_findings),
        resolved_findings=tuple(resolved),
        entities_added=tuple(sorted(new_ids - old_ids)),
        entities_removed=tuple(sorted(old_ids - new_ids)),
        capability_transitions=tuple(transitions),
        drift_added=tuple(sorted(new_drift - old_drift)),
        drift_resolved=tuple(sorted(old_drift - new_drift)),
    )


def trend(snapshots: list[HistorySnapshot]) -> list[dict[str, Any]]:
    """Per-snapshot series row: counts by severity + per-category totals."""
    rows: list[dict[str, Any]] = []
    for snap in snapshots:
        by_category: dict[str, int] = {}
        for f in snap.findings:
            cat = str(f.get("category", "-"))
            by_category[cat] = by_category.get(cat, 0) + 1
        rows.append(
            {
                "snapshot": snap.name,
                "created": snap.created,
                "total": snap.summary.get("total", len(snap.findings)),
                "errors": snap.summary.get("errors", 0),
                "warnings": snap.summary.get("warnings", 0),
                "entities": len(snap.entity_ids),
                "by_category": dict(sorted(by_category.items())),
            }
        )
    return rows


def prune(root: Path, keep: int) -> list[Path]:
    """Delete oldest snapshots beyond ``keep``; returns removed paths."""
    snaps = list_snapshots(root)
    removed: list[Path] = []
    for p in snaps[: max(0, len(snaps) - keep)]:
        p.unlink(missing_ok=True)
        removed.append(p)
    return removed
