"""End-to-end SLO & critical-path intelligence (spec 245).

Whole-path analysis over ``DataPlatformGraph`` — extends the phase-238
local reliability/SLA surface.  Paths are enumerated only over
data-flow edge semantics (PRODUCES/CONSUMES/READS/WRITES/TRIGGERS/
INVOKES/DEPENDS_ON/READS_FROM/WRITES_TO) — no arbitrary hops.

Latency segments come from comparable evidence only (execution
durations keyed by job/query identity, or explicit entity attrs);
unknown segments are listed, never inferred.  Bottleneck = largest
*observed* contributor.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median
from typing import TYPE_CHECKING, Any

from forge_doctor.core.reliability import ObjectiveMetric, ServiceObjective

if TYPE_CHECKING:
    from forge_doctor.core.execution_model import QueryExecution
    from forge_doctor.core.platform_graph import DataPlatformGraph


def _path_kinds() -> frozenset[Any]:
    from forge_doctor.core.platform_graph import RelKind

    return frozenset(
        {
            RelKind.PRODUCES,
            RelKind.CONSUMES,
            RelKind.READS,
            RelKind.WRITES,
            RelKind.TRIGGERS,
            RelKind.INVOKES,
            RelKind.DEPENDS_ON,
            RelKind.READS_FROM,
            RelKind.WRITES_TO,
        }
    )


_MAX_DEPTH = 16


@dataclass(frozen=True)
class PathSegment:
    """One entity on a critical path with its measured contribution."""

    entity: str
    latency_ms: float | None = None
    freshness_s: float | None = None
    evidence: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.latency_ms is not None or self.freshness_s is not None


@dataclass(frozen=True)
class CriticalPath:
    """A source→sink data-flow path with per-segment evidence."""

    source: str
    destination: str
    entities: tuple[str, ...]
    latency_segments: tuple[PathSegment, ...]
    executions: tuple[str, ...] = ()  # execution ids contributing evidence
    evidence: tuple[str, ...] = ()

    @property
    def total_latency(self) -> float | None:
        """Sum over comparable segments; None when nothing measured."""
        vals = [s.latency_ms for s in self.latency_segments if s.latency_ms is not None]
        return sum(vals) if vals else None

    @property
    def freshness(self) -> float | None:
        vals = [s.freshness_s for s in self.latency_segments if s.freshness_s is not None]
        return sum(vals) if vals else None

    @property
    def bottleneck(self) -> str | None:
        """Largest observed contributor — never an inferred one."""
        known = [s for s in self.latency_segments if s.latency_ms is not None]
        if not known:
            return None
        return max(known, key=lambda s: s.latency_ms or 0.0).entity

    @property
    def unknown_segments(self) -> tuple[str, ...]:
        return tuple(s.entity for s in self.latency_segments if not s.known)

    def coverage(self) -> str:
        known = sum(1 for s in self.latency_segments if s.known)
        return f"{known} known / {len(self.latency_segments) - known} unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "entities": list(self.entities),
            "executions": list(self.executions),
            "segments": [
                {
                    "entity": s.entity,
                    "latency_ms": s.latency_ms,
                    "freshness_s": s.freshness_s,
                    "evidence": list(s.evidence),
                }
                for s in self.latency_segments
            ],
            "total_latency_ms": self.total_latency,
            "freshness_s": self.freshness,
            "bottleneck": self.bottleneck,
            "unknown_segments": list(self.unknown_segments),
            "coverage": self.coverage(),
        }


@dataclass(frozen=True)
class SLOBudget:
    """One objective evaluated over one critical path."""

    objective: ServiceObjective
    path: str  # source -> destination
    total_budget: float
    consumed: float | None
    remaining: float | None
    violating_segments: tuple[str, ...]
    known_segments: int
    unknown_segments: int
    evidence: tuple[str, ...] = ()

    @property
    def exhausted(self) -> bool:
        return self.remaining is not None and self.remaining < 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective.to_dict(),
            "path": self.path,
            "total_budget": self.total_budget,
            "consumed": self.consumed,
            "remaining": self.remaining,
            "exhausted": self.exhausted,
            "violating_segments": list(self.violating_segments),
            "coverage": f"{self.known_segments} known / {self.unknown_segments} unknown",
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# Path discovery — bounded DFS over data-flow edges only
# ---------------------------------------------------------------------------


def _flow_adjacency(graph: DataPlatformGraph) -> dict[str, list[str]]:
    """Data-flow adjacency.  WRITES/PRODUCES/TRIGGERS/INVOKES point in
    flow direction; CONSUMES/READS/READS_FROM are drawn consumer->source
    and DEPENDS_ON points dependent->dependency, so those are reversed to
    recover the direction data actually moves."""
    from forge_doctor.core.platform_graph import RelKind

    reverse = {RelKind.CONSUMES, RelKind.READS, RelKind.READS_FROM, RelKind.DEPENDS_ON}
    kinds = _path_kinds()
    out: dict[str, list[str]] = {}
    for r in graph.relationships():
        if r.kind not in kinds:
            continue
        if r.kind in reverse:
            out.setdefault(r.dst, []).append(r.src)
        else:
            out.setdefault(r.src, []).append(r.dst)
    return {k: sorted(set(v)) for k, v in out.items()}


def _enumerate_paths(adj: dict[str, list[str]]) -> list[list[str]]:
    """All directed source->sink paths, bounded; deterministic order."""
    all_nodes = set(adj) | {d for v in adj.values() for d in v}
    sinks = {n for n in all_nodes if not adj.get(n)}
    sources = {n for n in all_nodes if n not in {d for v in adj.values() for d in v}}
    paths: list[list[str]] = []
    for src in sorted(sources):
        stack: list[tuple[str, list[str]]] = [(src, [src])]
        while stack:
            node, path = stack.pop()
            if node in sinks or len(path) >= _MAX_DEPTH:
                paths.append(path)
                continue
            nbrs = [n for n in adj.get(node, []) if n not in path]
            if not nbrs:
                paths.append(path)
                continue
            for nbr in reversed(nbrs):
                stack.append((nbr, [*path, nbr]))
    return sorted(paths, key=lambda p: (p[0], p[-1], len(p), p))


# ---------------------------------------------------------------------------
# Segment evidence — comparable units only
# ---------------------------------------------------------------------------


def _exec_latency(executions: list[QueryExecution]) -> dict[str, list[float]]:
    """identity -> durations.  Keys: query_id + fingerprint (hashed SQL
    is never a plausible entity name — kept for completeness)."""
    by_key: dict[str, list[float]] = {}
    for ex in executions:
        if ex.duration_ms is None:
            continue
        for key in (ex.query_id, ex.query_fingerprint, ex.execution_id):
            if key:
                by_key.setdefault(key, []).append(ex.duration_ms)
    return by_key


def _entity_names_of(entity: Any) -> set[str]:
    names = {entity.id, entity.identifier, entity.name}
    return {n for n in names if n}


def _freshness_attr(entity: Any) -> float | None:
    for k in ("freshness_s", "lag_s", "lag_ms"):
        v = entity.attr(k)
        if v:
            try:
                val = float(v)
            except ValueError:
                continue
            return val / 1000.0 if k == "lag_ms" else val
    return None


def _latency_attr(entity: Any) -> float | None:
    for k in ("latency_ms", "duration_ms", "avg_duration_ms"):
        v = entity.attr(k)
        if v:
            try:
                return float(v)
            except ValueError:
                continue
    return None


def critical_paths(
    graph: DataPlatformGraph | None,
    executions: list[QueryExecution] | None = None,
) -> list[CriticalPath]:
    """Enumerate flow paths and attach per-segment measured latency.

    Segment latency comes from executions whose query_id/fingerprint
    names the entity (e.g. a job-named series), else explicit entity
    attrs.  Nothing measured -> segment unknown.
    """
    if graph is None:
        return []
    adj = _flow_adjacency(graph)
    lat = _exec_latency(list(executions or []))
    out: list[CriticalPath] = []
    for path in _enumerate_paths(adj):
        segments: list[PathSegment] = []
        exec_ids: set[str] = set()
        for eid in path:
            ent = graph.entity(eid)
            names = _entity_names_of(ent) if ent else {eid}
            durs = [d for n in names for d in lat.get(n, [])]
            latency = median(durs) if durs else _latency_attr(ent) if ent else None
            if durs:
                ids = {
                    ex.execution_id
                    for ex in (executions or [])
                    if ex.duration_ms is not None
                    and (ex.query_id in names or ex.query_fingerprint in names)
                }
                exec_ids |= ids
            segments.append(
                PathSegment(
                    entity=eid,
                    latency_ms=latency,
                    freshness_s=_freshness_attr(ent) if ent else None,
                    evidence=((f"exec:{eid}" if durs else "",) if durs else ()),
                )
            )
        out.append(
            CriticalPath(
                source=path[0],
                destination=path[-1],
                entities=tuple(path),
                latency_segments=tuple(segments),
                executions=tuple(sorted(exec_ids)),
            )
        )
    return out


# ---------------------------------------------------------------------------
# SLO budgets over paths
# ---------------------------------------------------------------------------


def slo_budgets(
    objectives: list[ServiceObjective] | tuple[ServiceObjective, ...],
    paths: list[CriticalPath],
) -> list[SLOBudget]:
    """Decompose each latency/freshness objective over matching paths.

    Scope matching: objective scope names the destination (or any path
    entity); empty scope applies to every path.  Budgets never mix
    units — FRESHNESS objectives consume freshness_s segments, LATENCY
    objectives consume latency_ms segments.
    """
    out: list[SLOBudget] = []
    for obj in objectives:
        if obj.metric not in (ObjectiveMetric.FRESHNESS, ObjectiveMetric.LATENCY):
            continue
        use_freshness = obj.metric is ObjectiveMetric.FRESHNESS
        budget = obj.target
        for p in paths:
            if obj.scope and not any(
                obj.scope in (seg.entity,) or obj.scope in seg.entity for seg in p.latency_segments
            ):
                continue
            segs = [
                s
                for s in p.latency_segments
                if (s.freshness_s if use_freshness else s.latency_ms) is not None
            ]
            unknown = len(p.latency_segments) - len(segs)
            consumed = (
                (
                    sum(s.freshness_s or 0.0 for s in segs)
                    if use_freshness
                    else sum(s.latency_ms or 0.0 for s in segs)
                )
                if segs
                else None
            )
            remaining = budget - consumed if consumed is not None else None
            violating: tuple[str, ...] = ()
            if remaining is not None and remaining < 0:
                # biggest observed contributors first — the budget view
                key = (
                    (lambda s: s.freshness_s or 0.0)
                    if use_freshness
                    else (lambda s: s.latency_ms or 0.0)
                )
                violating = tuple(s.entity for s in sorted(segs, key=key, reverse=True))
            out.append(
                SLOBudget(
                    objective=obj,
                    path=f"{p.source} -> {p.destination}",
                    total_budget=budget,
                    consumed=consumed,
                    remaining=remaining,
                    violating_segments=violating,
                    known_segments=len(segs),
                    unknown_segments=unknown,
                    evidence=(f"objective:{obj.scope or 'all'}",),
                )
            )
    return out


# ---------------------------------------------------------------------------
# SLO001-006 findings
# ---------------------------------------------------------------------------


class SLOCheckId(Enum):
    SLO001 = "SLO001"  # e2e freshness violation
    SLO002 = "SLO002"  # latency budget exhausted
    SLO003 = "SLO003"  # unknown critical segment
    SLO004 = "SLO004"  # RPO mismatch
    SLO005 = "SLO005"  # RTO mismatch
    SLO006 = "SLO006"  # critical dependency without failover evidence


@dataclass(frozen=True)
class SLOFinding:
    check_id: str
    severity: str  # warning | info
    path: str
    message: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
            "evidence": list(self.evidence),
        }


def _availability_attrs(entity: Any) -> tuple[str, ...]:
    """Failover evidence on a path entity."""
    found = []
    for k in ("replicas", "failover", "dlq", "multi_az", "standby"):
        if entity.attr(k):
            found.append(k)
    return tuple(found)


def slo_findings(
    paths: list[CriticalPath],
    budgets: list[SLOBudget],
    graph: DataPlatformGraph | None,
) -> list[SLOFinding]:
    """SLO001-006 — only where topology/objective evidence exists."""
    out: list[SLOFinding] = []
    for b in budgets:
        label = b.path
        if b.exhausted and b.objective.metric is ObjectiveMetric.FRESHNESS:
            out.append(
                SLOFinding(
                    SLOCheckId.SLO001.value,
                    "warning",
                    label,
                    f"end-to-end freshness exceeded budget "
                    f"({b.consumed}s > {b.total_budget}s); "
                    f"segments: {', '.join(b.violating_segments)}",
                    b.evidence,
                )
            )
        elif b.exhausted:
            out.append(
                SLOFinding(
                    SLOCheckId.SLO002.value,
                    "warning",
                    label,
                    f"latency budget exhausted "
                    f"({b.consumed}ms > {b.total_budget}ms); "
                    f"top consumers: {', '.join(b.violating_segments)}",
                    b.evidence,
                )
            )
    for p in paths:
        label = f"{p.source} -> {p.destination}"
        if p.unknown_segments:
            out.append(
                SLOFinding(
                    SLOCheckId.SLO003.value,
                    "info",
                    label,
                    f"unknown segment(s) on critical path: "
                    f"{', '.join(p.unknown_segments)} ({p.coverage()})",
                    (),
                )
            )
    if graph is not None:
        objectives_by_scope = [b.objective for b in budgets]
        for p in paths:
            for seg in p.latency_segments:
                ent = graph.entity(seg.entity)
                if ent is None:
                    continue
                label = f"{p.source} -> {p.destination}"
                # RPO/RTO: declared target vs observed replication/failover
                rpo = ent.attr("rpo") or ent.attr("rpo_s")
                rto = ent.attr("rto") or ent.attr("rto_s")
                failover = _availability_attrs(ent)
                if rpo and not failover:
                    out.append(
                        SLOFinding(
                            SLOCheckId.SLO004.value,
                            "warning",
                            label,
                            f"{seg.entity}: declared RPO {rpo}s but no "
                            f"replication/failover evidence",
                            (f"entity:{seg.entity}",),
                        )
                    )
                if rto and not failover:
                    out.append(
                        SLOFinding(
                            SLOCheckId.SLO005.value,
                            "warning",
                            label,
                            f"{seg.entity}: declared RTO {rto}s but no "
                            f"replication/failover evidence",
                            (f"entity:{seg.entity}",),
                        )
                    )
                # SLO006: entity under an SLO scope with no failover attrs
                scoped = any(
                    o.scope and (o.scope in seg.entity or seg.entity in o.scope)
                    for o in objectives_by_scope
                )
                if scoped and not failover:
                    out.append(
                        SLOFinding(
                            SLOCheckId.SLO006.value,
                            "info",
                            label,
                            f"{seg.entity}: critical dependency under SLO "
                            f"without failover evidence",
                            (f"entity:{seg.entity}",),
                        )
                    )
    return out
