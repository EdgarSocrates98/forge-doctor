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
    RelKind.READS_FROM,
    RelKind.WRITES_TO,
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
    RelKind.WRITES_TO,
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
    breaking: tuple[str, ...] = ()  # contract-field breaks (spec 217)


# Contract-governed relations carry declared fields as ``field.<name>``
# attrs (platform_graph_builder._contracts). A removed field or a
# narrowed type is a breaking schema change (DCTR004 surface).

_FIELD_ATTR = "field."


def _type_split(decl: str) -> tuple[str, tuple[int, ...] | None]:
    import re

    m = re.fullmatch(r"\s*([a-z0-9_ ]+?)\s*(?:\(([\d,\s]+)\))?\s*", decl.lower())
    if m is None:
        return decl.strip().lower(), None
    params = tuple(int(p) for p in m.group(2).split(",") if p.strip()) if m.group(2) else None
    return m.group(1).strip(), params


_NUMERIC_ORDER = {
    "tinyint": 1,
    "byteint": 1,
    "smallint": 2,
    "int": 3,
    "integer": 3,
    "bigint": 4,
    "decimal": 5,
    "numeric": 5,
    "number": 5,
    "float": 6,
    "real": 6,
    "double": 7,
    "double precision": 7,
}
_TEXT_FAMILY = {"char", "varchar", "character", "string", "text"}


def _type_relation(old: str, new: str) -> str:
    """``same|widened|narrowed|changed`` — deterministic type lattice."""
    if old == new:
        return "same"
    ob, op = _type_split(old)
    nb, np = _type_split(new)
    if ob == nb:
        if op == np:
            return "same"
        if op and np and len(op) == len(np):
            if all(a <= b for a, b in zip(op, np, strict=True)):
                return "widened"
            if all(a >= b for a, b in zip(op, np, strict=True)):
                return "narrowed"
        return "changed"  # params appear/disappear or diverge — honest
    if ob in _NUMERIC_ORDER and nb in _NUMERIC_ORDER:
        return "widened" if _NUMERIC_ORDER[nb] > _NUMERIC_ORDER[ob] else "narrowed"
    if ob in _TEXT_FAMILY and nb in _TEXT_FAMILY:
        # bounded -> unbounded widens; unbounded -> bounded narrows;
        # both bounded is covered by the same-base-name branch only when
        # the names match, so "varchar" vs "string" params compare here.
        if np is not None and op is not None and len(np) == len(op) == 1:
            return "widened" if np[0] >= op[0] else "narrowed"
        if np is None and ob != "text":
            return "widened"
        if op is None and nb != "text":
            return "narrowed"
        return "changed"
    if ob == "date" and nb == "timestamp":
        return "widened"
    return "changed"


def _field_breaks(base: dict[str, str], head: dict[str, str]) -> tuple[str, ...]:
    """Breaking schema changes among ``field.*`` attrs on one entity."""
    out: list[str] = []
    for key in sorted(set(base) | set(head)):
        if not key.startswith(_FIELD_ATTR):
            continue
        name = key[len(_FIELD_ATTR) :]
        old, new = base.get(key), head.get(key)
        if old is not None and new is None:
            out.append(f"{name} removed")
        elif old is not None and new is not None and old != new:
            rel = _type_relation(old, new)
            if rel in {"narrowed", "changed"}:
                out.append(f"{name}: {old} -> {new} ({rel})")
    return tuple(out)


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
            ea_ent, eb_ent = base.entity(eid), head.entity(eid)
            ea_attrs = dict(ea_ent.attrs) if ea_ent is not None else {}
            eb_attrs = dict(eb_ent.attrs) if eb_ent is not None else {}
            changes.append(
                EntityChange(
                    entity_id=eid,
                    change="modified",
                    via_files=(f,) if file_changed and f else (),
                    attr_diffs=diffs,
                    impacted=tuple(sorted(impact_fn(base, eid))),
                    breaking=_field_breaks(ea_attrs, eb_attrs),
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
        if c.change == "modified" and c.breaking:
            bump(
                RISK_HIGH,
                f"breaking schema change on {c.entity_id}: {'; '.join(c.breaking)}",
            )
        elif c.change == "removed" and n:
            bump(
                RISK_HIGH,
                f"removed {c.entity_id} impacts {n} entit{'y' if n == 1 else 'ies'}",
            )
        elif c.change == "removed" and (
            kind == EntityKind.DATA_CONTRACT
            or (ent is not None and any(k.startswith(_FIELD_ATTR) for k, _ in ent.attrs))
        ):
            bump(
                RISK_HIGH,
                f"removed contracted relation {c.entity_id} (declared schema lost)",
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
