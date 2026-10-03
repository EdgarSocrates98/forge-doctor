"""Formal digital twin: the platform graph plus its invariant suite.

The twin is the trusted snapshot that decision (228) and optimization
(229) intelligence stand on. Invariants are checked deterministically
and *reported* — never auto-repaired:

- ``I1`` no dangling relationship endpoints;
- ``I2`` entity ids match ``kind:domain:identifier`` with ``kind`` in
  the ontology vocabulary;
- ``I3`` relationship ``evidence_kind`` is a declared evidence plane
  when set;
- ``I4`` findings' files resolve inside the scanned root;
- ``I5`` entity attr completeness (informational — missing ``file``/
  ``line`` counted per domain).

Hard violations (I1-I3) mean the twin cannot be trusted; informational
gaps are honest absences.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from forge_doctor_data.core.models import EvidenceKind
from forge_doctor_data.core.ontology import validate_graph
from forge_doctor_data.core.platform_graph import DataPlatformGraph, EntityKind

if TYPE_CHECKING:
    from pathlib import Path

    from forge_doctor_data.core.models import ScanReport


@dataclass(frozen=True)
class TwinViolation:
    invariant: str
    detail: str


@dataclass(frozen=True)
class AttrGap:
    domain: str
    missing_name: int
    missing_file: int
    missing_line: int


@dataclass(frozen=True)
class TwinReport:
    entity_count: int
    relationship_count: int
    entities_by_kind: tuple[tuple[str, int], ...]
    entities_by_domain: tuple[tuple[str, int], ...]
    rels_by_kind: tuple[tuple[str, int], ...]
    violations: tuple[TwinViolation, ...] = ()
    attr_gaps: tuple[AttrGap, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, object]:
        return {
            "entities": self.entity_count,
            "relationships": self.relationship_count,
            "entities_by_kind": dict(self.entities_by_kind),
            "entities_by_domain": dict(self.entities_by_domain),
            "relationships_by_kind": dict(self.rels_by_kind),
            "violations": [{"invariant": v.invariant, "detail": v.detail} for v in self.violations],
            "attr_gaps": [
                {
                    "domain": g.domain,
                    "missing_name": g.missing_name,
                    "missing_file": g.missing_file,
                    "missing_line": g.missing_line,
                }
                for g in self.attr_gaps
            ],
            "ok": self.ok,
        }


@dataclass(frozen=True)
class Twin:
    """Assembled twin: graph + optional scan report + invariant report."""

    graph: DataPlatformGraph
    report: TwinReport
    scan_findings: int = 0


def _counts(items: list[str]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return tuple(sorted(counts.items()))


def _id_parts_ok(entity_id: str) -> bool:
    parts = entity_id.split(":", 2)
    return len(parts) == 3 and all(parts)


def validate_twin(
    graph: DataPlatformGraph,
    report: ScanReport | None = None,
    root: Path | None = None,
) -> TwinReport:
    """Check the twin's invariants over a built graph (and scan report)."""
    violations: list[TwinViolation] = []

    # I1 — no dangling endpoints.
    known_ids = {e.id for e in graph.entities()}
    for rel in graph.relationships():
        for endpoint, label in ((rel.src, "src"), (rel.dst, "dst")):
            if endpoint not in known_ids:
                violations.append(
                    TwinViolation("I1", f"dangling {label}: {endpoint} ({rel.kind.value})")
                )

    # I2 — id format + kind in ontology.
    kind_names = {k.value for k in EntityKind}
    for ent in graph.entities():
        if not _id_parts_ok(ent.id) or ent.id.split(":", 1)[0] not in kind_names:
            violations.append(TwinViolation("I2", f"malformed entity id: {ent.id!r}"))

    # I3 — relationship evidence planes are ontology-bound when set.
    planes = {k.value for k in EvidenceKind}
    for rel in graph.relationships():
        if rel.evidence_kind is not None:
            plane = getattr(rel.evidence_kind, "value", rel.evidence_kind)
            if plane not in planes:
                violations.append(
                    TwinViolation("I3", f"{rel.src} -> {rel.dst}: unknown plane {plane!r}")
                )

    # I4 — finding files resolve to a scanned file inside the root.
    if report is not None and root is not None:
        resolved_root = root.resolve()
        for r in report.results:
            if r.file is None:
                continue
            f = r.file if r.file.is_absolute() else (root / r.file)
            resolved = f.resolve()
            if not resolved.exists() or not resolved.is_relative_to(resolved_root):
                violations.append(TwinViolation("I4", f"finding file unresolved: {r.file}"))

    # I5 — attr completeness (informational gaps, not violations).
    gaps: dict[str, AttrGap] = {}
    for ent in graph.entities():
        cur = gaps.get(ent.domain)
        if cur is None:
            cur = AttrGap(ent.domain, 0, 0, 0)
            gaps[ent.domain] = cur
        gaps[ent.domain] = AttrGap(
            cur.domain,
            cur.missing_name + (not ent.name),
            cur.missing_file + (ent.file is None),
            cur.missing_line + (ent.line is None),
        )

    # Ontology producer-domain conformance folds in as violations.
    for detail in validate_graph(graph):
        violations.append(TwinViolation("ontology", detail))

    return TwinReport(
        entity_count=len(graph.entities()),
        relationship_count=len(graph.relationships()),
        entities_by_kind=_counts([e.kind.value for e in graph.entities()]),
        entities_by_domain=_counts([e.domain for e in graph.entities()]),
        rels_by_kind=_counts([r.kind.value for r in graph.relationships()]),
        violations=tuple(sorted(violations, key=lambda v: (v.invariant, v.detail))),
        attr_gaps=tuple(sorted(gaps.values(), key=lambda g: g.domain)),
    )


def build_twin(
    graph: DataPlatformGraph,
    report: ScanReport | None = None,
    root: Path | None = None,
) -> Twin:
    """Assemble the twin: validate invariants over graph (+ report)."""
    return Twin(
        graph=graph,
        report=validate_twin(graph, report, root),
        scan_findings=len(report.results) if report is not None else 0,
    )


def twin_snapshot(twin: Twin) -> dict[str, object]:
    """Deterministic snapshot artifact for downstream tools."""
    return {
        "invariants_ok": twin.report.ok,
        "summary": twin.report.to_dict(),
        **twin.graph.to_dict(),
    }
