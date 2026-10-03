"""Reliability & SLA intelligence (spec 238, program P phase 4).

Models the platform's reliability *behavior* — retries, idempotency,
checkpointing, delivery semantics, service objectives, freshness
paths, failure domains — from declared config and exported runtime
evidence. Never incident management, never invented guarantees:
exactly-once is never asserted without full-path evidence, and
incomplete freshness timestamps produce PARTIAL, not an end-to-end
number.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from forge_doctor_data.core.execution_model import QueryExecution
from forge_doctor_data.core.knowledge import load_pack
from forge_doctor_data.core.models import Confidence, EvidenceKind, Severity
from forge_doctor_data.core.platform_graph import RelKind


class EvidenceState(Enum):
    DECLARED = "declared"  # config/DDL states it
    OBSERVED = "observed"  # runtime export shows it
    ABSENT = "absent"  # surface exists, feature not configured
    UNKNOWN = "unknown"  # no evidence either way


class ObjectiveMetric(Enum):
    AVAILABILITY = "availability"
    LATENCY = "latency"
    FRESHNESS = "freshness"
    THROUGHPUT = "throughput"
    ERROR_RATE = "error_rate"
    RPO = "rpo"
    RTO = "rto"


class DeliverySemantics(Enum):
    AT_MOST_ONCE = "at_most_once"
    AT_LEAST_ONCE = "at_least_once"
    EFFECTIVELY_ONCE = "effectively_once"  # at-least-once + dedup/idempotent sink
    EXACTLY_ONCE_CLAIMED = "exactly_once_claimed"  # declared end-to-end, full-path evidence
    UNKNOWN = "unknown"


_MECHANISMS = (
    "retries",
    "idempotency",
    "checkpointing",
    "deduplication",
    "timeout",
    "dlq",
    "backup",
    "restore",
    "replication",
    "failover",
    "health_checks",
    "recovery",
    "delivery",
)


@dataclass(frozen=True)
class Mechanism:
    """One reliability feature's evidence state on a subject."""

    name: str  # one of _MECHANISMS
    state: EvidenceState
    detail: str = ""  # declared value ("retries=3", "dlq=events-dlq")
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "detail": self.detail,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ReliabilityModel:
    """Reliability feature surface for one subject (entity/workload)."""

    subject: str
    engine: str
    mechanisms: tuple[Mechanism, ...]

    def mechanism(self, name: str) -> Mechanism | None:
        return next((m for m in self.mechanisms if m.name == name), None)

    def state_of(self, name: str) -> EvidenceState:
        m = self.mechanism(name)
        return m.state if m else EvidenceState.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "engine": self.engine,
            "mechanisms": [m.to_dict() for m in self.mechanisms],
        }


@dataclass(frozen=True)
class ServiceObjective:
    """A declared SLO/SLA — from contract, policy, or config evidence."""

    metric: ObjectiveMetric
    target: float
    unit: str  # ms | % | seconds | count
    window: str = ""  # "30d", "1h"
    scope: str = ""  # entity/workload the objective covers
    source: str = ""  # contract | policy | config
    criticality: str = ""  # e.g. tier label when declared
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.value,
            "target": self.target,
            "unit": self.unit,
            "window": self.window,
            "scope": self.scope,
            "source": self.source,
            "criticality": self.criticality,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class FreshnessPath:
    """Event-time → serving lag; PARTIAL when any hop lacks evidence."""

    subject: str
    source_event_time: float | None = None
    ingestion_time: float | None = None
    transformation_time: float | None = None
    serving_time: float | None = None
    complete: bool = False

    @property
    def total_lag(self) -> float | None:
        if not self.complete or self.source_event_time is None or self.serving_time is None:
            return None
        return self.serving_time - self.source_event_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "source_event_time": self.source_event_time,
            "ingestion_time": self.ingestion_time,
            "transformation_time": self.transformation_time,
            "serving_time": self.serving_time,
            "status": "complete" if self.complete else "partial",
            "total_lag": self.total_lag,
        }


@dataclass(frozen=True)
class FailureDomain:
    """A blast-radius boundary — only where topology evidence exists."""

    kind: str  # cloud | region | az | cluster | account | workspace
    identifier: str
    members: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "members": list(self.members),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ReliabilityFinding:
    """REL### finding — reliability evidence, never an incident claim."""

    check_id: str
    title: str
    severity: Severity
    message: str
    observed: str
    derived: str
    evidence: tuple[str, ...]
    confidence: Confidence
    evidence_kind: EvidenceKind = EvidenceKind.STATIC

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "severity": self.severity.value,
            "message": self.message,
            "observed": self.observed,
            "derived": self.derived,
            "confidence": self.confidence.value,
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# Extraction — reliability surface from declared entity attrs
# ---------------------------------------------------------------------------

_ATTR_MAP: dict[str, tuple[str, ...]] = {
    "retries": ("retries", "retry", "max_retries", "retry_policy"),
    "idempotency": ("idempotent", "idempotency", "idempotency_key"),
    "checkpointing": (
        "checkpoint",
        "checkpointing",
        "checkpoint_location",
        "checkpoint_dynamic",
    ),
    "deduplication": ("dedup", "deduplication", "dedup_key"),
    "timeout": ("timeout", "timeout_seconds", "query_timeout"),
    "dlq": ("dlq", "dead_letter", "dead_letter_queue", "error_topic"),
    "backup": ("backup", "backup_enabled", "snapshot", "snapshot_policy"),
    "restore": ("restore", "restore_tested", "pitr", "point_in_time"),
    "replication": ("replication", "replicas", "number_of_replicas", "cross_region"),
    "failover": ("failover", "multi_az", "multi_region", "ha"),
    "health_checks": ("health_check", "healthcheck", "liveness", "readiness"),
    "recovery": ("recovery", "recovery_procedure", "runbook", "rto_config"),
    "delivery": ("delivery", "exactly_once", "delivery_semantics"),
}


def extract_reliability(graph: Any) -> list[ReliabilityModel]:
    """Per-entity reliability surface from declared attrs.

    Attr present and truthy -> DECLARED; present and explicitly falsy
    ("false"/"0"/"none") -> ABSENT; missing -> UNKNOWN (not emitted —
    only known states are listed so absence stays honest).
    """
    out: list[ReliabilityModel] = []
    if graph is None:
        return out
    for e in graph.entities():
        mechanisms: list[Mechanism] = []
        ev: list[str] = [f"entity:{e.id}"]
        if e.file is not None:
            ev.append(f"file:{e.file.as_posix()}" + (f":{e.line}" if e.line else ""))
        for name, keys in _ATTR_MAP.items():
            val = ""
            for k in keys:
                v = e.attr(k)
                if v:
                    val = f"{k}={v}"
                    break
            if not val:
                continue
            negated = val.split("=", 1)[1].lower() in ("false", "0", "none", "disabled")
            mechanisms.append(
                Mechanism(
                    name=name,
                    state=EvidenceState.ABSENT if negated else EvidenceState.DECLARED,
                    detail=val,
                    evidence=tuple(ev),
                )
            )
        if mechanisms:
            out.append(
                ReliabilityModel(
                    subject=e.name or e.identifier,
                    engine=e.domain,
                    mechanisms=tuple(mechanisms),
                )
            )
    return sorted(out, key=lambda m: (m.engine, m.subject))


def observe_reliability(
    models: list[ReliabilityModel],
    executions: list[QueryExecution],
) -> list[ReliabilityModel]:
    """Upgrade mechanisms to OBSERVED where runtime exports prove them.

    A retried/failed-then-succeeded execution is runtime evidence for
    retries; a completed run after a failed one on the same fingerprint
    is recovery evidence. Nothing else is upgraded — observation needs
    a demonstrable runtime fact.
    """
    by_fp: dict[str, list[QueryExecution]] = {}
    for ex in executions:
        if ex.query_fingerprint:
            by_fp.setdefault(ex.query_fingerprint, []).append(ex)
    recovered_fps = {
        fp
        for fp, runs in by_fp.items()
        if any(r.status.value == "failed" for r in runs)
        and any(r.status.value == "completed" for r in runs)
    }
    out: list[ReliabilityModel] = []
    for m in models:
        mechanisms = list(m.mechanisms)
        for i, mech in enumerate(mechanisms):
            if mech.name in ("retries", "recovery") and m.subject in recovered_fps:
                mechanisms[i] = Mechanism(
                    name=mech.name,
                    state=EvidenceState.OBSERVED,
                    detail=mech.detail or "runtime:failed-then-completed",
                    evidence=(*mech.evidence, f"fingerprint:{m.subject}"),
                )
        out.append(
            ReliabilityModel(subject=m.subject, engine=m.engine, mechanisms=tuple(mechanisms))
        )
    return out


# ---------------------------------------------------------------------------
# Objectives + freshness paths
# ---------------------------------------------------------------------------

_OBJ_KEYS: dict[str, ObjectiveMetric] = {
    "sla_availability": ObjectiveMetric.AVAILABILITY,
    "sla_latency": ObjectiveMetric.LATENCY,
    "sla_freshness": ObjectiveMetric.FRESHNESS,
    "sla_throughput": ObjectiveMetric.THROUGHPUT,
    "sla_error_rate": ObjectiveMetric.ERROR_RATE,
    "rpo": ObjectiveMetric.RPO,
    "rto": ObjectiveMetric.RTO,
}
_OBJ_UNITS = {
    ObjectiveMetric.AVAILABILITY: "%",
    ObjectiveMetric.LATENCY: "ms",
    ObjectiveMetric.FRESHNESS: "seconds",
    ObjectiveMetric.THROUGHPUT: "rows/s",
    ObjectiveMetric.ERROR_RATE: "%",
    ObjectiveMetric.RPO: "seconds",
    ObjectiveMetric.RTO: "seconds",
}


def extract_objectives(graph: Any) -> list[ServiceObjective]:
    """Declared objectives from entity attrs (sla_*/rpo/rto keys)."""
    out: list[ServiceObjective] = []
    if graph is None:
        return out
    for e in graph.entities():
        ev: list[str] = [f"entity:{e.id}"]
        if e.file is not None:
            ev.append(f"file:{e.file.as_posix()}" + (f":{e.line}" if e.line else ""))
        for key, metric in _OBJ_KEYS.items():
            raw = e.attr(key)
            if not raw:
                continue
            try:
                target = float(raw)
            except ValueError:
                continue
            out.append(
                ServiceObjective(
                    metric=metric,
                    target=target,
                    unit=_OBJ_UNITS[metric],
                    scope=e.name or e.identifier,
                    source="config",
                    criticality=e.attr("criticality") or e.attr("tier"),
                    evidence=tuple(ev),
                )
            )
    return sorted(out, key=lambda o: (o.scope, o.metric.value))


def freshness_paths(
    graph: Any,
    executions: list[QueryExecution] | None = None,
) -> list[FreshnessPath]:
    """Build freshness paths over evidenced graph edges + runtime times.

    Path shape: producer -> transport -> compute -> storage -> consumer.
    Timestamp evidence comes from entity attrs (event_time/ingested_at/
    transformed_at/served_at) or execution start/end. Any missing hop
    -> complete=False (PARTIAL); total_lag stays None rather than
    summing incompatible metrics.
    """
    out: list[FreshnessPath] = []
    if graph is None:
        return out
    flows = graph.relationships(RelKind.WRITES) + graph.relationships(RelKind.PRODUCES)
    seen: set[str] = set()
    for rel in flows:
        src = graph.entity(rel.src) if hasattr(graph, "entity") else None
        dst = graph.entity(rel.dst) if hasattr(graph, "entity") else None
        subject = (dst.name or dst.identifier) if dst else rel.dst
        if subject in seen:
            continue
        seen.add(subject)
        ev_t = _time_attr(src, ("event_time", "source_event_time"))
        in_t = _time_attr(src, ("ingested_at", "ingestion_time")) or _time_attr(
            dst, ("ingested_at", "ingestion_time")
        )
        tr_t = _time_attr(dst, ("transformed_at", "transformation_time"))
        sv_t = _time_attr(dst, ("served_at", "serving_time"))
        complete = all(t is not None for t in (ev_t, in_t, sv_t))
        out.append(
            FreshnessPath(
                subject=subject,
                source_event_time=ev_t,
                ingestion_time=in_t,
                transformation_time=tr_t,
                serving_time=sv_t,
                complete=bool(complete),
            )
        )
    return sorted(out, key=lambda p: p.subject)


def _time_attr(e: Any, keys: tuple[str, ...]) -> float | None:
    if e is None:
        return None
    for k in keys:
        v = e.attr(k)
        if v:
            try:
                return float(v)
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# Delivery semantics — composed from path evidence, never asserted
# ---------------------------------------------------------------------------


def delivery_semantics(model: ReliabilityModel) -> DeliverySemantics:
    """Compose delivery semantics from the mechanism surface.

    - no retry and no dedup evidence            -> AT_MOST_ONCE-ish -> UNKNOWN
      unless at-most-once is declared explicitly
    - retries declared, no dedup/idempotency    -> AT_LEAST_ONCE
    - retries + dedup or idempotent sink        -> EFFECTIVELY_ONCE
    - exactly-once                              -> only when declared
      *and* checkpointing + idempotency + dedup all evidenced; a bare
      "exactly_once=true" claim without the path stays UNKNOWN.
    """
    st = {m.name: m.state for m in model.mechanisms}
    retry = st.get("retries") in (EvidenceState.DECLARED, EvidenceState.OBSERVED)
    idem = st.get("idempotency") in (EvidenceState.DECLARED, EvidenceState.OBSERVED)
    dedup = st.get("deduplication") in (EvidenceState.DECLARED, EvidenceState.OBSERVED)
    chk = st.get("checkpointing") in (EvidenceState.DECLARED, EvidenceState.OBSERVED)
    claimed = any(m.name == "delivery" and "exactly" in m.detail for m in model.mechanisms) or any(
        "exactly_once" in m.detail and m.state is EvidenceState.DECLARED for m in model.mechanisms
    )
    if claimed and retry and idem and dedup and chk:
        return DeliverySemantics.EXACTLY_ONCE_CLAIMED
    if retry and (idem or dedup):
        return DeliverySemantics.EFFECTIVELY_ONCE
    if retry:
        return DeliverySemantics.AT_LEAST_ONCE
    if any("at_most_once" in m.detail for m in model.mechanisms):
        return DeliverySemantics.AT_MOST_ONCE
    if st:
        return DeliverySemantics.UNKNOWN
    return DeliverySemantics.UNKNOWN


# ---------------------------------------------------------------------------
# Failure domains — topology evidence only
# ---------------------------------------------------------------------------


def failure_domains(graph: Any) -> list[FailureDomain]:
    """Group entities by declared topology (region/az/cluster/account)."""
    out: list[FailureDomain] = []
    if graph is None:
        return out
    groups: dict[tuple[str, str], list[str]] = {}
    for e in graph.entities():
        for kind in ("cloud", "region", "az", "cluster", "account", "workspace"):
            v = e.attr(kind) or (
                e.domain if kind == "cloud" and e.domain in ("aws", "azure", "gcp") else ""
            )
            if v:
                groups.setdefault((kind, v), []).append(e.id)
    for (kind, ident), members in sorted(groups.items()):
        out.append(
            FailureDomain(
                kind=kind,
                identifier=ident,
                members=tuple(sorted(set(members))),
                evidence=tuple(f"entity:{m}" for m in sorted(set(members))[:8]),
            )
        )
    return out


# ---------------------------------------------------------------------------
# REL findings
# ---------------------------------------------------------------------------


def rel_findings(
    models: list[ReliabilityModel],
    objectives: list[ServiceObjective] | None = None,
    freshness: list[FreshnessPath] | None = None,
    executions: list[QueryExecution] | None = None,
    graph: Any = None,
) -> list[ReliabilityFinding]:
    """REL001-REL010 over models + objectives + freshness evidence."""
    out: list[ReliabilityFinding] = []
    declared = EvidenceState.DECLARED
    observed = EvidenceState.OBSERVED
    on = (declared, observed)

    for m in models:
        ev = tuple(ev for mech in m.mechanisms for ev in mech.evidence)[:8]
        retry = m.state_of("retries") in on
        idem = m.state_of("idempotency") in on
        dedup = m.state_of("deduplication") in on
        chk = m.state_of("checkpointing") in on
        dlq = m.state_of("dlq") in on
        backup = m.state_of("backup") in on
        restore = m.state_of("restore") in on
        failover = m.state_of("failover") in on
        replication = m.state_of("replication") in on

        # REL001 — retry without idempotency
        if retry and not idem:
            out.append(
                _f(
                    "REL001",
                    "Retry without idempotency",
                    m,
                    "retries declared but no idempotency evidence",
                    "replay may duplicate side effects",
                    ev,
                )
            )
        # REL007 — missing DLQ on a retrying event path
        if retry and m.state_of("dlq") is EvidenceState.ABSENT:
            out.append(
                _f(
                    "REL007",
                    "Missing DLQ on retrying path",
                    m,
                    "retries declared, dlq explicitly disabled/absent",
                    "poison messages have nowhere to land",
                    ev,
                    severity=Severity.WARNING,
                )
            )
        elif retry and not dlq:
            out.append(
                _f(
                    "REL007",
                    "Missing DLQ on retrying path",
                    m,
                    "retries declared, no dlq evidence",
                    "poison-message handling unverified",
                    ev,
                )
            )
        # REL009 — duplicated delivery without dedup evidence
        if retry and not dedup and delivery_semantics(m) in (DeliverySemantics.AT_LEAST_ONCE,):
            out.append(
                _f(
                    "REL009",
                    "At-least-once without dedup evidence",
                    m,
                    "at-least-once semantics, no deduplication evidence",
                    "downstream must tolerate duplicates",
                    ev,
                )
            )
        # REL002 — stateful stream without checkpoint (stream-ish subjects)
        if _is_stream(m) and not chk:
            out.append(
                _f(
                    "REL002",
                    "Stateful stream without checkpoint",
                    m,
                    "stream subject, no checkpointing evidence",
                    "state lost on restart",
                    ev,
                    severity=Severity.WARNING,
                )
            )
        # REL008 — failover declared nowhere but replication exists
        if replication and not failover:
            out.append(
                _f(
                    "REL008",
                    "Failover topology unresolved",
                    m,
                    "replication declared, no failover mechanism evidence",
                    "replica promotion path unverified",
                    ev,
                )
            )
        # REL010 — backup/restore evidence incomplete
        if backup and not restore:
            out.append(
                _f(
                    "REL010",
                    "Backup without restore evidence",
                    m,
                    "backup declared, no restore/pitr evidence",
                    "recoverability unproven",
                    ev,
                )
            )

    # REL003 — SLA freshness mismatch vs observed path lag
    for obj in objectives or []:
        if obj.metric is not ObjectiveMetric.FRESHNESS:
            continue
        for fp in freshness or []:
            if fp.subject != obj.scope:
                continue
            if not fp.complete or fp.total_lag is None:
                out.append(
                    _fo(
                        "REL003",
                        "Freshness objective unverifiable",
                        obj,
                        "freshness path PARTIAL — hops missing timestamps",
                        "cannot compare lag to objective",
                        obj.evidence,
                    )
                )
            elif fp.total_lag > obj.target:
                out.append(
                    _fo(
                        "REL003",
                        "SLA freshness mismatch",
                        obj,
                        f"total_lag={fp.total_lag:.0f}s exceeds objective={obj.target:.0f}s",
                        "observed freshness violates declared objective",
                        (*obj.evidence, f"path:{fp.subject}"),
                        severity=Severity.WARNING,
                    )
                )
    # REL005 — RPO declared vs scoped backup/checkpoint evidence
    for obj in objectives or []:
        if obj.metric is not ObjectiveMetric.RPO:
            continue
        scope_models = [m for m in models if m.subject == obj.scope]
        covered = any(
            m.state_of("backup") in on or m.state_of("checkpointing") in on for m in scope_models
        )
        if not covered:
            out.append(
                _fo(
                    "REL005",
                    "RPO mismatch",
                    obj,
                    f"rpo={obj.target:.0f}s declared, no backup/checkpoint evidence in scope",
                    "point-in-time recovery capability unverified",
                    obj.evidence,
                )
            )

    # REL004 — observed latency exceeds objective (runtime evidence)
    for obj in objectives or []:
        if obj.metric is not ObjectiveMetric.LATENCY:
            continue
        for ex in executions or []:
            if ex.duration_ms is not None and ex.duration_ms > obj.target:
                out.append(
                    _fo(
                        "REL004",
                        "Observed latency exceeds objective",
                        obj,
                        f"duration_ms={ex.duration_ms:.0f} > target={obj.target:.0f}ms",
                        "runtime evidence over declared objective",
                        obj.evidence + tuple(ex.evidence)[:4],
                        severity=Severity.WARNING,
                    )
                )
                break

    # REL006 — RTO: objective exists but no recovery/failover evidence in scope
    for obj in objectives or []:
        if obj.metric is not ObjectiveMetric.RTO:
            continue
        scope_models = [m for m in models if m.subject == obj.scope]
        if scope_models and not any(
            m.state_of("recovery") in on or m.state_of("failover") in on for m in scope_models
        ):
            out.append(
                _fo(
                    "REL006",
                    "RTO path incomplete",
                    obj,
                    f"rto={obj.target:.0f}s declared, no recovery/failover evidence",
                    "recovery mechanics unverified for the scoped subject",
                    obj.evidence,
                )
            )
    return sorted(
        set(out), key=lambda f: (f.severity is not Severity.WARNING, f.check_id, f.message)
    )


def _is_stream(m: ReliabilityModel) -> bool:
    return any(
        k in m.engine or k in m.subject.lower()
        for k in ("stream", "kinesis", "kafka", "pubsub", "eventhubs", "eventhub")
    )


def _f(
    check_id: str,
    title: str,
    m: ReliabilityModel,
    observed: str,
    derived: str,
    evidence: tuple[str, ...],
    severity: Severity = Severity.INFO,
) -> ReliabilityFinding:
    return ReliabilityFinding(
        check_id=check_id,
        title=title,
        severity=severity,
        message=f"{m.subject} ({m.engine}): {observed}",
        observed=observed,
        derived=derived,
        evidence=evidence,
        confidence=Confidence.MEDIUM,
    )


def _fo(
    check_id: str,
    title: str,
    obj: ServiceObjective,
    observed: str,
    derived: str,
    evidence: tuple[str, ...],
    severity: Severity = Severity.INFO,
) -> ReliabilityFinding:
    return ReliabilityFinding(
        check_id=check_id,
        title=title,
        severity=severity,
        message=f"{obj.scope}: {observed}",
        observed=observed,
        derived=derived,
        evidence=evidence,
        confidence=Confidence.MEDIUM,
        evidence_kind=EvidenceKind.CONFIG,
    )


def engine_delivery_notes(engine: str) -> dict[str, Any]:
    """Delivery/retry/checkpoint semantics for an engine from the pack."""
    pack = load_pack("reliability", "engines") or {}
    engines = pack.get("engines", {})
    entry = engines.get(engine, {})
    return entry if isinstance(entry, dict) else {}
