"""Change → runtime correlation (spec 243, program Q phase 3).

Connects semantic/architecture/config/deployment change with runtime
behavior change.  Reuses ``change_intel`` (version moves, capability
transitions) and ``semantic_diff`` (``EntityChange``) as evidence
sources — never a parallel taxonomy.

correlation != causation: a correlation is reported only when the
evidence breakdown supports it, and the language stays "correlated
with" / "preceded by".  Without clock alignment (``TimestampQuality``)
no high-confidence temporal claim is made.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge_doctor_data.core.execution_history import (
    ExecutionSeries,
)
from forge_doctor_data.core.regression import (
    RegressionDimension,
    RegressionEpisode,
    RegressionPolicy,
    episodes,
)

if TYPE_CHECKING:
    from forge_doctor_data.core.execution_model import QueryExecution
    from forge_doctor_data.core.platform_graph import DataPlatformGraph
    from forge_doctor_data.core.semantic_diff import SemanticDiff


class ChangeClass(Enum):
    """Semantic class of an observed change (spec §3.2)."""

    RUNTIME_UPGRADE = "runtime_upgrade"
    SCHEMA_CHANGE = "schema_change"
    PARTITION_CHANGE = "partition_change"
    DISTRIBUTION_CHANGE = "distribution_change"
    CONFIG_CHANGE = "config_change"
    SECURITY_CHANGE = "security_change"
    ORCHESTRATION_CHANGE = "orchestration_change"
    CAPACITY_CHANGE = "capacity_change"
    QUERY_CHANGE = "query_change"
    MATERIALIZATION_CHANGE = "materialization_change"
    DEPENDENCY_CHANGE = "dependency_change"


@dataclass(frozen=True)
class ChangeEvent:
    """One localized, evidence-backed change."""

    id: str
    timestamp: float | None  # epoch ms when known
    source: str  # semantic_diff | deployment_export | git | ...
    changed_entities: tuple[str, ...]
    changed_properties: tuple[str, ...]
    change_class: ChangeClass
    commit: str = ""
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "source": self.source,
            "changed_entities": list(self.changed_entities),
            "changed_properties": list(self.changed_properties),
            "change_class": self.change_class.value,
            "commit": self.commit,
            "evidence": list(self.evidence),
        }


# attr-key fragments -> change class.  First match wins, ordered by
# specificity so `glue_version` lands RUNTIME_UPGRADE not CONFIG.
_CLASSIFY: tuple[tuple[tuple[str, ...], ChangeClass], ...] = (
    (("field.", "schema", "column"), ChangeClass.SCHEMA_CHANGE),
    (
        ("_version", "version", "runtime", "dbr", "databricks_runtime"),
        ChangeClass.RUNTIME_UPGRADE,
    ),
    (("partition", "bucket"), ChangeClass.PARTITION_CHANGE),
    (("distkey", "sortkey", "distribution", "shuffle"), ChangeClass.DISTRIBUTION_CHANGE),
    (
        ("replicas", "instances", "workers", "executors", "concurrency", "slots"),
        ChangeClass.CAPACITY_CHANGE,
    ),
    (("schedule", "retries", "trigger", "cron", "interval"), ChangeClass.ORCHESTRATION_CHANGE),
    (("materializ", "table_type", "view"), ChangeClass.MATERIALIZATION_CHANGE),
    (("encryption", "kms", "tls", "public", "auth"), ChangeClass.SECURITY_CHANGE),
    (("query", "sql", "statement"), ChangeClass.QUERY_CHANGE),
    (("depends_on", "input", "output", "upstream", "downstream"), ChangeClass.DEPENDENCY_CHANGE),
)


def classify_change(attr_keys: list[str]) -> ChangeClass:
    """Attr-diff keys -> change class; fallback CONFIG_CHANGE."""
    for keys, klass in _CLASSIFY:
        if any(any(k in a for k in keys) for a in attr_keys):
            return klass
    return ChangeClass.CONFIG_CHANGE


def change_events_from_diff(
    diff: SemanticDiff,
    *,
    timestamp: float | None = None,
    commit: str = "",
) -> tuple[ChangeEvent, ...]:
    """Change events from a semantic diff — only entity-mapped changes.

    Files that map to no platform entity (docs, README, CI yaml) never
    produce change events, so a docs-only commit cannot correlate with
    a regression.
    """
    events: list[ChangeEvent] = []
    for c in diff.changes:
        if c.change == "touched":
            # file content moved but extracted attrs identical — no
            # semantic change evidence, no event.
            continue
        props = tuple(sorted(set(c.attr_diffs) | {c.change}))
        klass = classify_change(list(c.attr_diffs))
        events.append(
            ChangeEvent(
                id=f"{source_id(commit)}:{c.entity_id}",
                timestamp=timestamp,
                source="semantic_diff",
                changed_entities=(c.entity_id,),
                changed_properties=props,
                change_class=klass,
                commit=commit,
                evidence=tuple(f"file:{f}" for f in c.via_files),
            )
        )
    return tuple(sorted(events, key=lambda e: e.id))


def source_id(commit: str) -> str:
    return commit[:12] if commit else "diff"


def change_events_from_json(path: Path) -> tuple[ChangeEvent, ...]:
    """Deployment-exported events (CI summary / TF apply / dbt run).

    Expected shape — a list of objects with at least ``entities`` or
    ``resources`` plus an optional ``timestamp``/``class``/``commit``.
    Files without those keys yield zero events, never guessed ones.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    rows = data if isinstance(data, list) else data.get("events", [])
    out: list[ChangeEvent] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        entities = row.get("entities") or row.get("resources") or ()
        if not entities:
            continue
        ts = row.get("timestamp")
        props = tuple(str(p) for p in row.get("properties", ()))
        out.append(
            ChangeEvent(
                id=str(row.get("id") or f"{path.name}:{i}"),
                timestamp=float(ts) if isinstance(ts, int | float) else None,
                source=str(row.get("source") or "deployment_export"),
                changed_entities=tuple(sorted(str(e) for e in entities)),
                changed_properties=props,
                change_class=ChangeClass(str(row.get("class") or ChangeClass.CONFIG_CHANGE.value))
                if str(row.get("class") or "") in {c.value for c in ChangeClass}
                else classify_change(list(props)),
                commit=str(row.get("commit") or ""),
                evidence=(f"file:{path.name}",),
            )
        )
    return tuple(sorted(out, key=lambda e: e.id))


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

# change class -> dimensions it plausibly moves (evidence-based gating)
_RELEVANT: dict[ChangeClass, frozenset[RegressionDimension]] = {
    ChangeClass.DISTRIBUTION_CHANGE: frozenset(
        {RegressionDimension.SHUFFLE, RegressionDimension.DURATION}
    ),
    ChangeClass.PARTITION_CHANGE: frozenset(
        {RegressionDimension.SCAN, RegressionDimension.DURATION}
    ),
    ChangeClass.SCHEMA_CHANGE: frozenset(
        {RegressionDimension.ERROR_RATE, RegressionDimension.DURATION}
    ),
    ChangeClass.CAPACITY_CHANGE: frozenset(
        {
            RegressionDimension.QUEUE,
            RegressionDimension.DURATION,
            RegressionDimension.MEMORY,
            RegressionDimension.THROUGHPUT,
        }
    ),
    ChangeClass.ORCHESTRATION_CHANGE: frozenset(
        {RegressionDimension.QUEUE, RegressionDimension.FRESHNESS}
    ),
    ChangeClass.QUERY_CHANGE: frozenset(
        {
            RegressionDimension.DURATION,
            RegressionDimension.SCAN,
            RegressionDimension.SHUFFLE,
            RegressionDimension.SPILL,
        }
    ),
    ChangeClass.MATERIALIZATION_CHANGE: frozenset(
        {
            RegressionDimension.SCAN,
            RegressionDimension.DURATION,
            RegressionDimension.SPILL,
        }
    ),
    ChangeClass.DEPENDENCY_CHANGE: frozenset(
        {RegressionDimension.ERROR_RATE, RegressionDimension.DURATION}
    ),
    ChangeClass.SECURITY_CHANGE: frozenset({RegressionDimension.ERROR_RATE}),
    ChangeClass.RUNTIME_UPGRADE: frozenset(RegressionDimension),
    ChangeClass.CONFIG_CHANGE: frozenset(RegressionDimension),
}


class CorrelationConfidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


@dataclass(frozen=True)
class ChangeRuntimeCorrelation:
    """One change ↔ subject regression with exposed evidence breakdown."""

    change: ChangeEvent
    subject: str
    matching_dimensions: tuple[RegressionDimension, ...]
    temporal_distance_ms: float | None  # change -> nearest regression start
    graph_distance: int | None  # BFS hops between entities
    shared_entities: tuple[str, ...]
    temporal_match: bool
    entity_overlap: bool
    graph_match: bool
    metric_relevant: bool
    confidence: CorrelationConfidence
    explanation: str

    @property
    def dimension(self) -> RegressionDimension:
        """First matching dimension (deterministic enum order)."""
        return self.matching_dimensions[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change.id,
            "change_class": self.change.change_class.value,
            "subject": self.subject,
            "matching_dimensions": [d.value for d in self.matching_dimensions],
            "temporal_distance_ms": self.temporal_distance_ms,
            "graph_distance": self.graph_distance,
            "shared_entities": list(self.shared_entities),
            "breakdown": {
                "temporal_match": self.temporal_match,
                "entity_overlap": self.entity_overlap,
                "graph_match": self.graph_match,
                "metric_relevant": self.metric_relevant,
            },
            "confidence": self.confidence.value,
            "explanation": self.explanation,
        }


def _entity_names(entities: tuple[str, ...]) -> set[str]:
    """Both full ids and terminal identifiers ('table:aws:t' -> 't')."""
    out = set(entities)
    for e in entities:
        out.add(e.rpartition(":")[2] or e)
    return out


def _series_entities(series: ExecutionSeries) -> set[str]:
    """Names a series demonstrably touches — subject id, fingerprint,
    and evidence sources.  Compact samples carry no input/output list,
    so linkage is only claimed where these names intersect."""
    return _entity_names((series.subject_id, series.fingerprint, *series.evidence_sources))


def _graph_ids(graph: DataPlatformGraph, names: set[str]) -> set[str]:
    """Map loose names to real entity ids — id, identifier, or name."""
    return {
        e.id for e in graph.entities() if e.id in names or e.identifier in names or e.name in names
    }


def _graph_distance(graph: DataPlatformGraph | None, src: set[str], dst: set[str]) -> int | None:
    """Undirected BFS hops between any src entity id and any dst id."""
    if graph is None or not src or not dst:
        return None
    adjacency: dict[str, set[str]] = {}
    for r in graph.relationships():
        adjacency.setdefault(r.src, set()).add(r.dst)
        adjacency.setdefault(r.dst, set()).add(r.src)
    src_ids = _graph_ids(graph, src)
    dst_ids = _graph_ids(graph, dst)
    if not src_ids or not dst_ids:
        return None
    seen = set(src_ids)
    frontier = set(src_ids)
    dist = 0
    while frontier and dist < 8:
        if frontier & dst_ids:
            return dist
        nxt: set[str] = set()
        for node in frontier:
            nxt |= adjacency.get(node, set()) - seen
        seen |= nxt
        frontier = nxt
        dist += 1
    return None


def correlate(
    changes: tuple[ChangeEvent, ...] | list[ChangeEvent],
    series_map: dict[str, ExecutionSeries],
    graph: DataPlatformGraph | None = None,
    *,
    window_ms: float = 7_200_000,  # ±2h default window (§11)
    policy: RegressionPolicy | None = None,
) -> list[ChangeRuntimeCorrelation]:
    """Pair changes with regression episodes — evidence-gated.

    Emitted only when the change demonstrably *can* reach the subject —
    entity overlap or graph path — plus at least one evidence leg
    (temporal precedence or metric relevance).  A change that touches
    nothing the series knows about cannot correlate, no matter the
    timing.
    """
    pol = policy or RegressionPolicy.defaults()
    out: list[ChangeRuntimeCorrelation] = []
    # episodes per series x dimension, regrouped per subject
    by_subject: dict[str, list[RegressionEpisode]] = {}
    subjects: dict[str, set[str]] = {}
    for sid in sorted(series_map):
        series = series_map[sid]
        subj = _series_entities(series)
        for dim in RegressionDimension:
            for ep in episodes(series, dim, pol):
                by_subject.setdefault(ep.subject, []).append(ep)
                subjects[ep.subject] = subj
    for change in changes:
        change_names = _entity_names(change.changed_entities)
        for subject in sorted(by_subject):
            ep_list = by_subject[subject]
            subj = subjects[subject]
            relevant = _RELEVANT.get(change.change_class, frozenset())
            matching = tuple(
                d
                for d in RegressionDimension
                if d in relevant and any(e.dimension is d for e in ep_list)
            )
            metric_relevant = bool(matching)

            shared = sorted(change_names & subj)
            entity_overlap = bool(shared)

            gdist = _graph_distance(graph, set(change.changed_entities), subj)
            graph_match = gdist is not None and gdist <= 3

            # temporal leg: nearest matching-dim episode start after change
            dist_ms: float | None = None
            temporal_match = False
            if change.timestamp is not None:
                deltas = [
                    e.start - change.timestamp
                    for e in ep_list
                    if e.dimension in matching and e.start is not None
                ]
                if deltas:
                    dist_ms = min(deltas, key=abs)
                    temporal_match = any(0 <= d <= window_ms for d in deltas)

            legs = sum([temporal_match, entity_overlap, graph_match, metric_relevant])
            locality = entity_overlap or graph_match
            # Emission gate: the change must reach the subject (locality)
            # and touch a dimension it can plausibly move (matching).
            # A docs-only commit has no locality — it cannot correlate.
            if not matching or not locality:
                continue
            confidence = {
                4: CorrelationConfidence.HIGH,
                3: CorrelationConfidence.MEDIUM,
            }.get(legs, CorrelationConfidence.LOW)
            explanation = (
                f"correlated with {change.change_class.value} on "
                f"{','.join(change.changed_entities)}; breakdown: "
                f"temporal={temporal_match} entity={entity_overlap} "
                f"graph={graph_match} metric={metric_relevant}"
            )
            out.append(
                ChangeRuntimeCorrelation(
                    change=change,
                    subject=subject,
                    matching_dimensions=matching,
                    temporal_distance_ms=dist_ms,
                    graph_distance=gdist,
                    shared_entities=tuple(shared),
                    temporal_match=temporal_match,
                    entity_overlap=entity_overlap,
                    graph_match=graph_match,
                    metric_relevant=metric_relevant,
                    confidence=confidence,
                    explanation=explanation,
                )
            )
    order = list(CorrelationConfidence)
    return sorted(
        out,
        key=lambda c: (order.index(c.confidence), c.change.id, c.subject),
    )


# ---------------------------------------------------------------------------
# Plan fingerprints & data-shape history (§13-16)
# ---------------------------------------------------------------------------


class PlanChangeKind(Enum):
    JOIN_STRATEGY_CHANGED = "join_strategy_changed"
    SCAN_PATH_CHANGED = "scan_path_changed"
    EXCHANGE_ADDED = "exchange_added"
    EXCHANGE_REMOVED = "exchange_removed"
    PARALLELISM_CHANGED = "parallelism_changed"
    MATERIALIZATION_CHANGED = "materialization_changed"


@dataclass(frozen=True)
class PlanFingerprint:
    """Structural plan identity — stage kinds + join strategies."""

    fingerprint: str
    stage_kinds: tuple[str, ...]
    join_strategies: tuple[str, ...]


def plan_fingerprint(ex: QueryExecution) -> PlanFingerprint:
    """Deterministic structural fingerprint of an execution plan."""
    import hashlib

    kinds = tuple(sorted({s.kind.value for s in ex.stages}))
    strategies = tuple(sorted({j.strategy.value for s in ex.stages for j in s.joins}))
    digest = hashlib.sha256("|".join(kinds + strategies).encode()).hexdigest()[:16]
    return PlanFingerprint(fingerprint=digest, stage_kinds=kinds, join_strategies=strategies)


def plan_changes(before: PlanFingerprint, after: PlanFingerprint) -> tuple[PlanChangeKind, ...]:
    """Structural diff between two plan fingerprints."""
    out: list[PlanChangeKind] = []
    if before.join_strategies != after.join_strategies:
        out.append(PlanChangeKind.JOIN_STRATEGY_CHANGED)
    b, a = set(before.stage_kinds), set(after.stage_kinds)
    if "exchange" in a - b:
        out.append(PlanChangeKind.EXCHANGE_ADDED)
    if "exchange" in b - a:
        out.append(PlanChangeKind.EXCHANGE_REMOVED)
    if (a - b) - {"exchange"} or (b - a) - {"exchange"}:
        out.append(PlanChangeKind.SCAN_PATH_CHANGED)
    return tuple(sorted(out, key=lambda k: k.value))


@dataclass(frozen=True)
class DataShapeSnapshot:
    """Data-shape facts at a point in time (§15) — no code blame."""

    row_count: float | None = None
    byte_size: float | None = None
    file_count: float | None = None
    partition_count: float | None = None
    key_cardinality: float | None = None
    skew: float | None = None


def shape_delta(before: DataShapeSnapshot, after: DataShapeSnapshot) -> dict[str, float]:
    """Field-wise ratio after/before — only where both sides measured."""
    out: dict[str, float] = {}
    for f_ in (
        "row_count",
        "byte_size",
        "file_count",
        "partition_count",
        "key_cardinality",
        "skew",
    ):
        b = getattr(before, f_)
        a = getattr(after, f_)
        if b is not None and a is not None and b > 0:
            out[f_] = round(a / b, 6)
    return dict(sorted(out.items()))
