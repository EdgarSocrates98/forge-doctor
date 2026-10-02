"""Semantic diff: entity-level change and blast-radius between two graphs.

Where ``forge-doctor diff`` compares findings, ``semantic_diff`` compares
platform *entities*: which were added, removed, or modified, which
changed files touched them, and what depends on them downstream
(``impact_reachable`` on the base graph). The output is a deterministic
``risk`` classification plus human-readable reasons - PR review signal
without an LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    EntityKind,
    RelKind,
)

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# Blast-radius direction for PR review: src depends on dst for these
# kinds, so a changed/removal dst affects the src (inbound). For
# DEFINES/GOVERNS the container owns the entity, so impact flows
# outbound only. INVOKES is dual-purpose (workflow->task containment AND
# task->platform-target dependency) so it appears in both directions.
_IMPACT_INBOUND = {
    RelKind.DEPENDS_ON,
    RelKind.READS,
    RelKind.CONSUMES,
    RelKind.STORED_IN,
    RelKind.INVOKES,
    RelKind.TRIGGERS,
}
_IMPACT_OUTBOUND = {
    RelKind.DEFINES,
    RelKind.GOVERNS,
    RelKind.INVOKES,
    RelKind.TRIGGERS,
    RelKind.WRITES,
    RelKind.PRODUCES,
}


def blast_radius(graph: DataPlatformGraph, entity_id: str) -> set[str]:
    """Everything that can break or lose a producer when the entity
    changes - wider than ``impact_reachable`` because callers (inbound
    INVOKES/DEPENDS_ON/READS...) count as dependents in PR review."""
    seen: set[str] = set()
    frontier = [entity_id]
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        hits = {r.src for r in graph.inbound(cur) if r.kind in _IMPACT_INBOUND}
        hits |= {r.dst for r in graph.outbound(cur) if r.kind in _IMPACT_OUTBOUND}
        frontier.extend(hits - seen)
    return seen - {entity_id}


# Kinds whose modification/removal is treated as structurally significant.
_STRUCTURAL_KINDS = {
    EntityKind.TABLE,
    EntityKind.STREAM,
    EntityKind.DATASET,
    EntityKind.CATALOG,
    EntityKind.WORKFLOW,
    EntityKind.COMPUTE_JOB,
}


@dataclass(frozen=True)
class EntityChange:
    """One entity observed to differ between base and head."""

    entity_id: str
    change: str  # added | removed | modified | touched
    via_files: tuple[str, ...] = ()  # changed files that touch the entity
    attr_diffs: tuple[str, ...] = ()  # attr keys whose values differ
    impacted: tuple[str, ...] = ()  # blast radius in the base graph


@dataclass
class SemanticDiff:
    """Entity-level diff between a base and head platform graph."""

    changed_files: tuple[str, ...]
    changes: tuple[EntityChange, ...]
    unmapped_files: tuple[str, ...] = ()
    risk: str = RISK_LOW
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def changes_of(self, kind: str) -> tuple[EntityChange, ...]:
        return tuple(c for c in self.changes if c.change == kind)

    @property
    def impacted_entities(self) -> tuple[str, ...]:
        seen: set[str] = set()
        for c in self.changes:
            seen.update(c.impacted)
        return tuple(sorted(seen))


def _entity_files(entity_id: str, graph: DataPlatformGraph) -> str | None:
    ent = graph.entity(entity_id)
    if ent is None or ent.file is None:
        return None
    return ent.file.as_posix()


def _attr_diffs(
    base: DataPlatformGraph, head: DataPlatformGraph, entity_id: str
) -> tuple[str, ...]:
    ea, eb = base.entity(entity_id), head.entity(entity_id)
    a = dict(ea.attrs) if ea else {}
    b = dict(eb.attrs) if eb else {}
    return tuple(sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k)))


def diff_graphs(
    base: DataPlatformGraph,
    head: DataPlatformGraph,
    changed_files: frozenset[str],
    impact_fn: Callable[[DataPlatformGraph, str], set[str]] | None = None,
) -> SemanticDiff:
    """Diff two platform graphs, mapping changed files to entities.

    ``impact_fn(graph, id) -> set[str]`` computes blast radius; defaults
    to :func:`blast_radius`. ``touched`` = entity still exists and its
    source file changed but extracted attributes are identical (content
    moved without semantic drift).
    """
    if impact_fn is None:
        impact_fn = blast_radius

    base_ids = {e.id for e in base.entities()}
    head_ids = {e.id for e in head.entities()}

    touched_files: set[str] = set()
    changes: list[EntityChange] = []

    for eid in sorted(base_ids - head_ids):
        f = _entity_files(eid, base)
        via = (f,) if f in changed_files else ()
        if f:
            touched_files.add(f)
        changes.append(
            EntityChange(
                entity_id=eid,
                change="removed",
                via_files=via,
                impacted=tuple(sorted(impact_fn(base, eid))),
            )
        )

    for eid in sorted(head_ids - base_ids):
        f = _entity_files(eid, head)
        via = (f,) if f in changed_files else ()
        if f:
            touched_files.add(f)
        changes.append(EntityChange(entity_id=eid, change="added", via_files=via))

    for eid in sorted(base_ids & head_ids):
        diffs = _attr_diffs(base, head, eid)
        f = _entity_files(eid, head) or _entity_files(eid, base)
        file_changed = bool(f and f in changed_files)
        if file_changed and f:
            touched_files.add(f)
        if diffs:
            changes.append(
                EntityChange(
                    entity_id=eid,
                    change="modified",
                    via_files=(f,) if file_changed and f else (),
                    attr_diffs=diffs,
                    impacted=tuple(sorted(impact_fn(base, eid))),
                )
            )
        elif file_changed:
            changes.append(
                EntityChange(
                    entity_id=eid,
                    change="touched",
                    via_files=(f,) if f else (),
                    impacted=tuple(sorted(impact_fn(base, eid))),
                )
            )

    unmapped = tuple(
        sorted(
            f for f in changed_files if f not in touched_files and not f.startswith((".", "docs/"))
        )
    )
    risk, reasons = _classify(changes, base)
    return SemanticDiff(
        changed_files=tuple(sorted(changed_files)),
        changes=tuple(changes),
        unmapped_files=unmapped,
        risk=risk,
        reasons=reasons,
    )


def _classify(changes: list[EntityChange], base: DataPlatformGraph) -> tuple[str, tuple[str, ...]]:
    """Deterministic risk rubric - highest triggered level wins."""
    risk = RISK_LOW
    reasons: list[str] = []

    def bump(level: str, reason: str) -> None:
        nonlocal risk
        order = (RISK_LOW, RISK_MEDIUM, RISK_HIGH)
        if order.index(level) > order.index(risk):
            risk = level
        reasons.append(reason)

    for c in changes:
        ent = base.entity(c.entity_id)
        kind = ent.kind if ent else None
        n = len(c.impacted)
        if c.change == "removed" and n:
            bump(
                RISK_HIGH,
                f"removed {c.entity_id} impacts {n} entit{'y' if n == 1 else 'ies'}",
            )
        elif c.change == "removed":
            bump(RISK_MEDIUM, f"removed {c.entity_id} (no dependents)")
        elif c.change == "modified" and n and kind in _STRUCTURAL_KINDS:
            bump(
                RISK_HIGH,
                f"modified structural entity {c.entity_id} impacts {n} dependents",
            )
        elif c.change == "modified" and n:
            bump(
                RISK_MEDIUM,
                f"modified {c.entity_id} ({', '.join(c.attr_diffs)}) impacts {n}",
            )
        elif c.change == "touched" and n:
            bump(RISK_MEDIUM, f"touched {c.entity_id} has {n} dependents")
    if not reasons:
        reasons.append("no platform entity changed")
    return risk, tuple(reasons)
