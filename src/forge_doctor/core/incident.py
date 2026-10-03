"""Causal / dependency-aware incident intelligence (spec 244).

An ``IncidentEpisode`` groups co-occurring regression episodes into one
explainable window and attaches candidate causes with an explicit
evidence path — never a bare verdict.  Extends ``core.diagnosis``
(reuses ``PromotionLevel``) with temporal evidence, runtime trends and
change correlation; it does not duplicate the root-cause engine —
``cluster_findings`` keeps owning static finding chains.

Language discipline: ``confirmed`` is reserved for a deterministic
full evidence chain (change + temporal precedence + entity/graph link
+ metric relevance + persistent breach); anything weaker is
``strongly_supported`` at best — usually ``possible``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from forge_doctor.core.change_correlation import (
    ChangeClass,
    ChangeEvent,
    ChangeRuntimeCorrelation,
    correlate,
)
from forge_doctor.core.diagnosis import PromotionLevel
from forge_doctor.core.execution_history import ExecutionSeries
from forge_doctor.core.regression import (
    PersistenceClass,
    RegressionDimension,
    RegressionEpisode,
    RegressionPolicy,
    episodes,
)

if TYPE_CHECKING:
    from forge_doctor.core.platform_graph import DataPlatformGraph


class CauseCategory(Enum):
    """Spec §PHASE4 cause taxonomy."""

    CONFIGURATION = "configuration"
    DATA_SHAPE = "data_shape"
    CAPACITY = "capacity"
    DEPENDENCY = "dependency"
    SCHEMA = "schema"
    ORCHESTRATION = "orchestration"
    NETWORK = "network"
    STORAGE_LAYOUT = "storage_layout"
    QUERY_PLAN = "query_plan"
    RUNTIME_VERSION = "runtime_version"
    UNKNOWN = "unknown"


_CLASS_TO_CAUSE: dict[ChangeClass, CauseCategory] = {
    ChangeClass.CONFIG_CHANGE: CauseCategory.CONFIGURATION,
    ChangeClass.SECURITY_CHANGE: CauseCategory.CONFIGURATION,
    ChangeClass.RUNTIME_UPGRADE: CauseCategory.RUNTIME_VERSION,
    ChangeClass.SCHEMA_CHANGE: CauseCategory.SCHEMA,
    ChangeClass.PARTITION_CHANGE: CauseCategory.STORAGE_LAYOUT,
    ChangeClass.DISTRIBUTION_CHANGE: CauseCategory.STORAGE_LAYOUT,
    ChangeClass.MATERIALIZATION_CHANGE: CauseCategory.STORAGE_LAYOUT,
    ChangeClass.CAPACITY_CHANGE: CauseCategory.CAPACITY,
    ChangeClass.ORCHESTRATION_CHANGE: CauseCategory.ORCHESTRATION,
    ChangeClass.QUERY_CHANGE: CauseCategory.QUERY_PLAN,
    ChangeClass.DEPENDENCY_CHANGE: CauseCategory.DEPENDENCY,
}


@dataclass(frozen=True)
class CandidateCause:
    """One proposed cause with its explicit evidence path.

    ``evidence_path`` lists the links actually confirmed; ``limitations``
    lists what could not be shown.  Both are mandatory — a candidate
    cause always answers "why is this related?" *and* "what is missing?".
    """

    entity: str
    change: ChangeEvent | None
    category: CauseCategory
    evidence_path: tuple[str, ...]
    confirmed_links: int
    expected_links: int
    temporal_match: bool
    runtime_match: bool  # a real regression episode exists downstream
    graph_match: bool
    confidence: PromotionLevel
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "change_id": self.change.id if self.change else None,
            "category": self.category.value,
            "evidence_path": (
                f"{self.confirmed_links}/{self.expected_links} expected links confirmed"
            ),
            "links": list(self.evidence_path),
            "temporal_match": self.temporal_match,
            "runtime_match": self.runtime_match,
            "graph_match": self.graph_match,
            "confidence": self.confidence.value,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class SymptomPropagation:
    """Upstream issue -> intermediate hops -> downstream symptom."""

    upstream_entity: str
    downstream_symptom: str
    intermediate: tuple[str, ...]  # "a --KIND--> b" hops
    path_found: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "upstream_entity": self.upstream_entity,
            "downstream_symptom": self.downstream_symptom,
            "intermediate": list(self.intermediate),
            "path_found": self.path_found,
        }


@dataclass(frozen=True)
class RecoveryEvent:
    """A resolved incident can record its recovery deterministically."""

    incident_id: str
    timestamp: float | None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class IncidentEpisode:
    """One explainable window of co-occurring regressions."""

    id: str
    start: float | None
    end: float | None
    symptoms: tuple[str, ...]  # "<subject>.<dimension>" regression labels
    affected_entities: tuple[str, ...]
    regressions: tuple[RegressionEpisode, ...]
    correlated_changes: tuple[ChangeRuntimeCorrelation, ...]
    candidate_causes: tuple[CandidateCause, ...]
    propagations: tuple[SymptomPropagation, ...]
    downstream_effects: tuple[str, ...]
    owners: tuple[str, ...]  # ownership routing — display only
    unknowns: tuple[str, ...]
    recoveries: tuple[RecoveryEvent, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "symptoms": list(self.symptoms),
            "affected_entities": list(self.affected_entities),
            "correlated_changes": [c.to_dict() for c in self.correlated_changes],
            "candidate_causes": [c.to_dict() for c in self.candidate_causes],
            "propagations": [p.to_dict() for p in self.propagations],
            "downstream_effects": list(self.downstream_effects),
            "owners": list(self.owners),
            "unknowns": list(self.unknowns),
        }


# Expected evidence links for a full deterministic chain (spec §76).
EXPECTED_LINKS = 5


def _propagation_path(
    graph: DataPlatformGraph | None, src_id: str, dst_names: set[str]
) -> tuple[str, ...]:
    """Directed BFS src -> dst over relationship kinds; returns hop labels."""
    if graph is None or not dst_names:
        return ()
    from forge_doctor.core.change_correlation import _graph_ids

    srcs = _graph_ids(graph, {src_id})
    dsts = _graph_ids(graph, dst_names)
    if not srcs or not dsts:
        return ()
    out_map: dict[str, list[tuple[str, str]]] = {}
    for r in graph.relationships():
        out_map.setdefault(r.src, []).append((r.dst, r.kind.value))
    # BFS keeping parent pointers — deterministic via sorted neighbors
    parents: dict[str, tuple[str, str]] = {}
    frontier = sorted(srcs)
    seen = set(frontier)
    hit: str | None = None
    while frontier and hit is None:
        nxt: list[str] = []
        for node in frontier:
            for nbr, kind in sorted(out_map.get(node, [])):
                if nbr in seen:
                    continue
                seen.add(nbr)
                parents[nbr] = (node, kind)
                if nbr in dsts:
                    hit = nbr
                    break
                nxt.append(nbr)
            if hit is not None:
                break
        frontier = sorted(nxt)
    if hit is None:
        return ()
    hops: list[str] = []
    node = hit
    while node in parents:
        prev, kind = parents[node]
        hops.append(f"{prev} --{kind}--> {node}")
        node = prev
    return tuple(reversed(hops))


def _owners_of(graph: DataPlatformGraph | None, entities: set[str]) -> tuple[str, ...]:
    """Ownership routing labels — display only, no notifications."""
    if graph is None:
        return ()
    owners: set[str] = set()
    for e in graph.entities():
        if e.id in entities or e.identifier in entities or e.name in entities:
            owner = e.attr("owner") or e.attr("team")
            owners.add(owner or e.domain)
    return tuple(sorted(owners))


def _downstream(graph: DataPlatformGraph | None, entities: set[str]) -> tuple[str, ...]:
    """Entities depending on the affected ones (directed outbound)."""
    if graph is None or not entities:
        return ()
    from forge_doctor.core.change_correlation import _graph_ids

    starts = _graph_ids(graph, entities)
    out_map: dict[str, list[str]] = {}
    for r in graph.relationships():
        out_map.setdefault(r.src, []).append(r.dst)
    seen = set(starts)
    frontier = list(starts)
    depth = 0
    while frontier and depth < 4:
        nxt: list[str] = []
        for node in frontier:
            for nbr in sorted(out_map.get(node, [])):
                if nbr not in seen:
                    seen.add(nbr)
                    nxt.append(nbr)
        frontier = nxt
        depth += 1
    return tuple(sorted(seen - starts))


def _candidate_from_correlation(
    corr: ChangeRuntimeCorrelation,
    persistent: bool,
) -> CandidateCause:
    """Correlation -> candidate cause with explicit evidence path."""
    change = corr.change
    links: list[str] = []
    limitations: list[str] = []
    confirmed = 0

    if change is not None:
        links.append(f"change event {change.id} ({change.change_class.value})")
        confirmed += 1
    if corr.temporal_match:
        links.append("temporal precedence within window")
        confirmed += 1
    elif corr.temporal_distance_ms is None:
        limitations.append("timestamps missing — temporal link unverifiable")
    else:
        limitations.append("change outside correlation window")
    if corr.entity_overlap:
        links.append(f"entity overlap: {', '.join(corr.shared_entities)}")
        confirmed += 1
    else:
        limitations.append("no shared entities")
    if corr.graph_match:
        links.append(f"graph path within {corr.graph_distance} hop(s)")
        confirmed += 1
    else:
        limitations.append("no graph path between change and subject")
    if corr.metric_relevant:
        dims = ", ".join(d.value for d in corr.matching_dimensions)
        links.append(f"metric relevance: {dims}")
        confirmed += 1
    if not persistent:
        limitations.append("regression not persistent — weak runtime evidence")

    # ``confirmed`` only for a full deterministic chain: all 5 evidence
    # links AND a persistent breach.  Anything less stays supported/possible.
    runtime_match = persistent
    if persistent:
        links.append("persistent regression breach")

    if confirmed >= EXPECTED_LINKS and persistent:
        level = PromotionLevel.CONFIRMED
    elif confirmed >= 3:
        level = PromotionLevel.STRONGLY_SUPPORTED
    else:
        level = PromotionLevel.POSSIBLE
    category = (
        _CLASS_TO_CAUSE.get(change.change_class, CauseCategory.UNKNOWN)
        if change
        else CauseCategory.UNKNOWN
    )
    return CandidateCause(
        entity=change.changed_entities[0] if change else "",
        change=change,
        category=category,
        evidence_path=tuple(links),
        confirmed_links=confirmed,
        expected_links=EXPECTED_LINKS,
        temporal_match=corr.temporal_match,
        runtime_match=runtime_match,
        graph_match=corr.graph_match,
        confidence=level,
        limitations=tuple(limitations),
    )


_DRIFT_CAUSE: dict[str, CauseCategory] = {
    "config_drift": CauseCategory.CONFIGURATION,
    "schema_drift": CauseCategory.SCHEMA,
    "runtime_drift": CauseCategory.RUNTIME_VERSION,
    "security_drift": CauseCategory.CONFIGURATION,
    "implementation_drift": CauseCategory.DEPENDENCY,
    "performance_drift": CauseCategory.QUERY_PLAN,
    "capability_drift": CauseCategory.UNKNOWN,
    "ownership_drift": CauseCategory.UNKNOWN,
}


def structural_causes(
    reconciliations: tuple[Any, ...] | list[Any] = (),
    capability_gaps: dict[str, str] | None = None,
) -> tuple[CandidateCause, ...]:
    """Twin drift + capability gaps as structural candidate causes.

    No change event, no temporal leg — these are always POSSIBLE and
    honest about it.
    """
    out: list[CandidateCause] = []
    for rec in reconciliations:
        drift = getattr(rec, "drift_type", None)
        category = _DRIFT_CAUSE.get(getattr(drift, "value", ""), CauseCategory.UNKNOWN)
        out.append(
            CandidateCause(
                entity=getattr(rec, "entity", ""),
                change=None,
                category=category,
                evidence_path=(
                    f"twin drift: {getattr(drift, 'value', 'unknown')} "
                    f"on {getattr(rec, 'property', '?')}",
                ),
                confirmed_links=1,
                expected_links=EXPECTED_LINKS,
                temporal_match=False,
                runtime_match=False,
                graph_match=False,
                confidence=PromotionLevel.POSSIBLE,
                limitations=(
                    "structural cause — no change event, no temporal link",
                    f"difference: {getattr(rec, 'difference', '?')}",
                ),
            )
        )
    for key in sorted(capability_gaps or {}):
        out.append(
            CandidateCause(
                entity=key,
                change=None,
                category=CauseCategory.UNKNOWN,
                evidence_path=(f"capability gap: {key} = {(capability_gaps or {})[key]}",),
                confirmed_links=1,
                expected_links=EXPECTED_LINKS,
                temporal_match=False,
                runtime_match=False,
                graph_match=False,
                confidence=PromotionLevel.POSSIBLE,
                limitations=("structural cause — capability state, not a change",),
            )
        )
    return tuple(out)


def build_incidents(
    series_map: dict[str, ExecutionSeries],
    changes: tuple[ChangeEvent, ...] | list[ChangeEvent] = (),
    graph: DataPlatformGraph | None = None,
    *,
    policy: RegressionPolicy | None = None,
    window_ms: float = 7_200_000,
    reconciliations: tuple[Any, ...] | list[Any] = (),
    capability_gaps: dict[str, str] | None = None,
) -> list[IncidentEpisode]:
    """Group co-occurring regression episodes into incidents.

    Episodes whose time windows overlap (or are within ``window_ms`` of
    each other) share an incident.  With no timestamps at all each
    episode stands alone — clustering is never guessed.
    """
    pol = policy or RegressionPolicy.defaults()
    all_eps: list[tuple[RegressionEpisode, ExecutionSeries]] = []
    for sid in sorted(series_map):
        series = series_map[sid]
        for dim in RegressionDimension:
            for ep in episodes(series, dim, pol):
                all_eps.append((ep, series))
    if not all_eps:
        return []

    # deterministic ordering: by start (None last), then subject
    all_eps.sort(key=lambda t: (t[0].start is None, t[0].start or 0.0, t[0].subject))
    groups: list[list[RegressionEpisode]] = []
    for ep, _s in all_eps:
        placed = False
        for g in groups:
            starts = [e.start for e in g if e.start is not None]
            ends = [e.end for e in g if e.end is not None]
            if ep.start is None or not starts:
                continue
            lo = min(starts)
            hi = max(ends or starts)
            if lo - window_ms <= ep.start <= hi + window_ms:
                g.append(ep)
                placed = True
                break
        if not placed:
            groups.append([ep])

    corrs = correlate(changes, series_map, graph, window_ms=window_ms)
    incidents: list[IncidentEpisode] = []
    for i, g in enumerate(groups):
        subjects = {e.subject for e in g}
        inc_corrs = tuple(c for c in corrs if c.subject in subjects)
        persistent = any(e.persistence is PersistenceClass.PERSISTENT for e in g)
        causes = [_candidate_from_correlation(c, persistent) for c in inc_corrs]
        causes.extend(structural_causes(reconciliations, capability_gaps))
        causes.sort(key=lambda c: (-c.confirmed_links, c.entity, c.category.value))

        from forge_doctor.core.change_correlation import _entity_names

        affected = sorted(subjects)
        affected_names = _entity_names(tuple(affected))
        props: list[SymptomPropagation] = []
        for c in inc_corrs:
            for ent in c.change.changed_entities:
                hops = _propagation_path(graph, ent, _entity_names((c.subject,)))
                props.append(
                    SymptomPropagation(
                        upstream_entity=ent,
                        downstream_symptom=c.subject,
                        intermediate=hops,
                        path_found=bool(hops),
                    )
                )
        unknowns: list[str] = []
        if not any(e.start is not None for e in g):
            unknowns.append("no timestamps — incident window unverifiable")
        if graph is None:
            unknowns.append("no platform graph — dependency paths unverifiable")
        if not inc_corrs:
            unknowns.append("no correlated changes — cause space open")
        if not persistent:
            unknowns.append("regression not persistent — may be transient")

        stamp = int(min((e.start for e in g if e.start is not None), default=0) or 0)
        incidents.append(
            IncidentEpisode(
                id=f"inc-{i:04d}-{stamp}",
                start=min((e.start for e in g if e.start is not None), default=None),
                end=max((e.end for e in g if e.end is not None), default=None),
                symptoms=tuple(sorted({f"{e.subject}.{e.dimension.value}" for e in g})),
                affected_entities=tuple(affected),
                regressions=tuple(sorted(g, key=lambda e: (e.subject, e.dimension.value))),
                correlated_changes=inc_corrs,
                candidate_causes=tuple(causes),
                propagations=tuple(props),
                downstream_effects=_downstream(graph, affected_names),
                owners=_owners_of(graph, affected_names),
                unknowns=tuple(unknowns),
            )
        )
    return incidents
