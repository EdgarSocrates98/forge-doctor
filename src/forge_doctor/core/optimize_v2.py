"""Optimization intelligence 2.0 (spec 239, program P phase 5).

Multi-objective optimization opportunities grounded in the behavioral
evidence planes from specs 235-238: performance signals/findings, cost
drivers/findings, reliability models/objectives, physical designs, and
the platform graph. Extends ``core/optimize.py`` — the v1 finding→
candidate pipeline is untouched; v2 is the opportunity layer.

Every opportunity shows benefit AND tradeoff, names its protected
constraints (SLA/criticality/capability), and carries an experiment
plan — the engine proposes, labs measure; nothing deploys.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

from forge_doctor.core.cost_drivers import CostDriver, CostFinding
from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.core.performance import PerformanceFinding, PerformanceSignal
from forge_doctor.core.physical_design import PhysicalDesign
from forge_doctor.core.reliability import (
    EvidenceState,
    ObjectiveMetric,
    ReliabilityModel,
    ServiceObjective,
)


class Objective(Enum):
    PERFORMANCE = "performance"
    COST_DRIVER = "cost_driver"
    RELIABILITY = "reliability"
    FRESHNESS = "freshness"
    COMPLEXITY = "complexity"
    PORTABILITY = "portability"


class GuardrailStatus(Enum):
    CLEAR = "clear"  # no protected constraint at risk
    REVIEW = "review"  # constraint potentially affected — human decides
    BLOCKED = "blocked"  # required capability provably unsupported
    NOT_APPLICABLE = "not_applicable"  # evidence context does not apply


class OptimizationFamily(Enum):
    PARTITION_PRUNING = "partition_pruning"
    DISTRIBUTION_ALIGNMENT = "distribution_alignment"
    JOIN_STRATEGY = "join_strategy"
    PARALLELISM = "parallelism"
    FILE_SIZING = "file_sizing"
    STREAM_TRIGGER = "stream_trigger"
    WAREHOUSE_SIZING = "warehouse_sizing"
    SLOT_RESERVATION = "slot_reservation"
    WLM_RESOURCE_GROUPS = "wlm_resource_groups"
    MATERIALIZATION = "materialization"
    REPLICATION = "replication"
    INDEX_SHARD_LAYOUT = "index_shard_layout"
    RETENTION = "retention"
    CACHE = "cache"


@dataclass(frozen=True)
class ExperimentPlan:
    """What a lab would measure — never executed by the engine."""

    hypothesis: str
    workload: str = ""
    baseline_metrics: tuple[str, ...] = ()
    expected_metrics: tuple[str, ...] = ()
    validation_method: str = "lab experiment"
    unknowns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "workload": self.workload,
            "baseline_metrics": list(self.baseline_metrics),
            "expected_metrics": list(self.expected_metrics),
            "validation_method": self.validation_method,
            "unknowns": list(self.unknowns),
        }


@dataclass(frozen=True)
class OptimizationOpportunity:
    """One multi-objective opportunity — benefit + tradeoff, always."""

    id: str
    target: str  # entity / execution / subject
    objective: Objective
    family: OptimizationFamily
    classification: str = "opportunity"  # OPPORTUNITY — never ERROR
    evidence: tuple[str, ...] = ()
    expected_effects: tuple[str, ...] = ()
    negative_tradeoffs: tuple[str, ...] = ()
    protected_constraints: tuple[str, ...] = ()
    confidence: Confidence = Confidence.LOW
    prerequisites: tuple[str, ...] = ()
    guardrail_status: GuardrailStatus = GuardrailStatus.CLEAR
    guardrail_note: str = ""
    experiment_plan: ExperimentPlan | None = None
    unknowns: tuple[str, ...] = ()
    evidence_kind: EvidenceKind = EvidenceKind.DERIVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "objective": self.objective.value,
            "family": self.family.value,
            "classification": self.classification,
            "evidence": list(self.evidence),
            "expected_effects": list(self.expected_effects),
            "negative_tradeoffs": list(self.negative_tradeoffs),
            "protected_constraints": list(self.protected_constraints),
            "confidence": self.confidence.value,
            "prerequisites": list(self.prerequisites),
            "guardrail_status": self.guardrail_status.value,
            "guardrail_note": self.guardrail_note,
            "experiment_plan": (self.experiment_plan.to_dict() if self.experiment_plan else None),
            "unknowns": list(self.unknowns),
        }


# ---------------------------------------------------------------------------
# Family catalog — effects/tradeoffs are declared, never numbers
# ---------------------------------------------------------------------------

# family -> (objective, expected_effects, negative_tradeoffs)
_FAMILY_TABLE: dict[OptimizationFamily, tuple[Objective, tuple[str, ...], tuple[str, ...]]] = {
    OptimizationFamily.PARTITION_PRUNING: (
        Objective.PERFORMANCE,
        ("scan bytes ↓", "query latency ↓", "read cost driver ↓"),
        ("write path complexity ↑", "partition key rigidity"),
    ),
    OptimizationFamily.DISTRIBUTION_ALIGNMENT: (
        Objective.PERFORMANCE,
        ("shuffle/exchange volume ↓", "join time ↓"),
        ("distribution redesign migration", "key skew risk if mischosen"),
    ),
    OptimizationFamily.JOIN_STRATEGY: (
        Objective.PERFORMANCE,
        ("exchange amplification ↓", "join time ↓"),
        ("broadcast memory pressure", "plan fragility on data growth"),
    ),
    OptimizationFamily.PARALLELISM: (
        Objective.PERFORMANCE,
        ("stage duration ↓",),
        ("small-task overhead ↑", "scheduler churn"),
    ),
    OptimizationFamily.FILE_SIZING: (
        Objective.PERFORMANCE,
        ("per-file overhead ↓", "scan latency ↓"),
        ("compaction cost ↑", "larger files reduce parallelism"),
    ),
    OptimizationFamily.STREAM_TRIGGER: (
        Objective.COST_DRIVER,
        ("commit frequency ↓", "file size ↑", "compute overhead ↓"),
        ("freshness lag ↑ — protected by freshness objectives",),
    ),
    OptimizationFamily.WAREHOUSE_SIZING: (
        Objective.COST_DRIVER,
        ("warehouse cost driver ↓",),
        ("queue time ↑", "latency ↑ under load"),
    ),
    OptimizationFamily.SLOT_RESERVATION: (
        Objective.COST_DRIVER,
        ("slot contention ↓", "predictable capacity"),
        ("idle reservation spend", "lower burst flexibility"),
    ),
    OptimizationFamily.WLM_RESOURCE_GROUPS: (
        Objective.PERFORMANCE,
        ("queue wait ↓ for priority workloads",),
        ("starvation risk for deprioritized queues", "config complexity"),
    ),
    OptimizationFamily.MATERIALIZATION: (
        Objective.COMPLEXITY,
        ("duplicate compute ↓", "single source of truth"),
        ("coupling between consumers", "shared-failure blast radius"),
    ),
    OptimizationFamily.REPLICATION: (
        Objective.COST_DRIVER,
        ("storage multiplier ↓",),
        ("durability/DR posture ↓", "failover capacity ↓"),
    ),
    OptimizationFamily.INDEX_SHARD_LAYOUT: (
        Objective.PERFORMANCE,
        ("per-request fan-out ↓", "small-shard overhead ↓"),
        ("reindex downtime", "fewer shards reduce write parallelism"),
    ),
    OptimizationFamily.RETENTION: (
        Objective.COST_DRIVER,
        ("storage volume ↓",),
        ("time-travel/history window ↓", "compliance risk"),
    ),
    OptimizationFamily.CACHE: (
        Objective.PERFORMANCE,
        ("repeat-scan latency ↓",),
        ("staleness risk", "cache storage cost ↑"),
    ),
}

# Evidence -> opportunity mappings (spec §example mappings)
_PERF_TO_FAMILY: dict[str, tuple[OptimizationFamily, str]] = {
    "PERF004": (
        OptimizationFamily.DISTRIBUTION_ALIGNMENT,
        "confirmed skew -> repartition/salting experiment",
    ),
    "PERF001": (
        OptimizationFamily.PARTITION_PRUNING,
        "high scan amplification -> partition/filter pruning",
    ),
    "PERF002": (
        OptimizationFamily.PARTITION_PRUNING,
        "poor pruning -> partition key review",
    ),
    "PERF003": (
        OptimizationFamily.JOIN_STRATEGY,
        "exchange amplification -> join/distribution strategy",
    ),
    "PERF005": (
        OptimizationFamily.JOIN_STRATEGY,
        "spill pressure -> join/memory strategy",
    ),
    "PERF006": (
        OptimizationFamily.WAREHOUSE_SIZING,
        "queue pressure -> sizing/concurrency experiment",
    ),
    "PERF009": (
        OptimizationFamily.FILE_SIZING,
        "small-file penalty -> compaction/file sizing",
    ),
    "PERF010": (
        OptimizationFamily.MATERIALIZATION,
        "repeated materialization -> consolidation review",
    ),
}

# queue mechanisms that point at the engine-specific family
_QUEUE_FAMILY: dict[str, OptimizationFamily] = {
    "snowflake": OptimizationFamily.WAREHOUSE_SIZING,
    "redshift": OptimizationFamily.WLM_RESOURCE_GROUPS,
    "bigquery": OptimizationFamily.SLOT_RESERVATION,
    "trino": OptimizationFamily.WLM_RESOURCE_GROUPS,
}

# Which objective families each constraint metric protects
_CONSTRAINT_FOR_FAMILY: dict[OptimizationFamily, ObjectiveMetric] = {
    OptimizationFamily.STREAM_TRIGGER: ObjectiveMetric.FRESHNESS,
    OptimizationFamily.WAREHOUSE_SIZING: ObjectiveMetric.LATENCY,
    OptimizationFamily.REPLICATION: ObjectiveMetric.RPO,
    OptimizationFamily.RETENTION: ObjectiveMetric.RPO,
}


def opportunities(
    signals: list[PerformanceSignal] | None = None,
    perf: list[PerformanceFinding] | None = None,
    drivers: list[CostDriver] | None = None,
    cost: list[CostFinding] | None = None,
    models: list[ReliabilityModel] | None = None,
    objectives: list[ServiceObjective] | None = None,
    designs: list[PhysicalDesign] | None = None,
    graph: Any = None,
    registry: Any = None,
) -> list[OptimizationOpportunity]:
    """Enumerate multi-objective opportunities over behavioral evidence."""
    out: list[OptimizationOpportunity] = []
    objs = list(objectives or [])

    # --- from PERF findings -------------------------------------------------
    finding_subject = {s.subject: s.engine for s in signals or []}
    for f in perf or []:
        if f.severity is not Severity.WARNING:
            continue
        mapping = _PERF_TO_FAMILY.get(f.check_id)
        if mapping is None:
            continue
        family, reason = mapping
        target = _finding_target(f, finding_subject)
        engine = finding_subject.get(target, "")
        if f.check_id == "PERF006" and engine in _QUEUE_FAMILY:
            family = _QUEUE_FAMILY[engine]
        out.append(
            _opp(
                family=family,
                target=target,
                reason=reason,
                evidence=(*f.evidence, f.check_id),
                confidence=f.confidence,
                objectives=objs,
                registry=registry,
                experiment=ExperimentPlan(
                    hypothesis=reason,
                    workload=target,
                    baseline_metrics=("duration_ms", "bytes_read"),
                    expected_metrics=(f"{f.check_id} signal ↓",),
                    unknowns=("workload representativeness", "data skew drift"),
                ),
            )
        )

    # --- from COST findings -------------------------------------------------
    for cf in cost or []:
        fam = _COST_TO_FAMILY.get(cf.check_id)
        if fam is None:
            continue
        out.append(
            _opp(
                family=fam,
                target=_cost_target(cf),
                reason=f"{cf.check_id}: {cf.title}",
                evidence=(*cf.evidence, cf.check_id),
                confidence=cf.confidence,
                objectives=objs,
                registry=registry,
                experiment=ExperimentPlan(
                    hypothesis=f"{cf.title} -> {fam.value}",
                    baseline_metrics=("driver units",),
                    expected_metrics=("driver value ↓",),
                ),
            )
        )

    # --- from reliability models -------------------------------------------
    for m in models or []:
        if m.state_of("dlq") is EvidenceState.ABSENT and m.state_of("retries") in (
            EvidenceState.DECLARED,
            EvidenceState.OBSERVED,
        ):
            out.append(
                _opp(
                    family=OptimizationFamily.REPLICATION,
                    target=m.subject,
                    reason="retrying path with DLQ disabled — add dead-letter sink",
                    evidence=tuple(ev for mech in m.mechanisms for ev in mech.evidence)[:4],
                    confidence=Confidence.MEDIUM,
                    objectives=objs,
                    registry=registry,
                    override_objective=Objective.RELIABILITY,
                    override_effects=("poison-message containment",),
                    override_tradeoffs=("extra topic/storage", "ops surface"),
                )
            )

    # --- complexity: same subject materialized on many engines --------------
    by_subject: dict[str, set[str]] = {}
    for d in designs or []:
        by_subject.setdefault(d.subject, set()).add(d.engine)
    for subject, engines in sorted(by_subject.items()):
        if len(engines) >= 3:
            out.append(
                _opp(
                    family=OptimizationFamily.MATERIALIZATION,
                    target=subject,
                    reason=(
                        f"same subject on {len(engines)} engines "
                        f"({', '.join(sorted(engines))}) — consolidation review"
                    ),
                    evidence=(f"subject:{subject}",),
                    confidence=Confidence.LOW,
                    objectives=objs,
                    registry=registry,
                )
            )
    return sorted(
        set(out),
        key=lambda o: (o.guardrail_status is GuardrailStatus.BLOCKED, o.id),
    )


_COST_TO_FAMILY: dict[str, OptimizationFamily] = {
    "COST001": OptimizationFamily.WAREHOUSE_SIZING,
    "COST002": OptimizationFamily.PARTITION_PRUNING,
    "COST004": OptimizationFamily.REPLICATION,
    "COST005": OptimizationFamily.MATERIALIZATION,
    "COST006": OptimizationFamily.JOIN_STRATEGY,
    "COST007": OptimizationFamily.FILE_SIZING,
}


def _finding_target(f: PerformanceFinding, subjects: dict[str, str]) -> str:
    for s in subjects:
        if s and s in f.message:
            return s
    return f.message.split(":")[0]


def _cost_target(f: CostFinding) -> str:
    return f.message.split(":")[0].split(",")[0]


def _opp(
    *,
    family: OptimizationFamily,
    target: str,
    reason: str,
    evidence: tuple[str, ...],
    confidence: Confidence,
    objectives: list[ServiceObjective],
    registry: Any,
    experiment: ExperimentPlan | None = None,
    override_objective: Objective | None = None,
    override_effects: tuple[str, ...] | None = None,
    override_tradeoffs: tuple[str, ...] | None = None,
) -> OptimizationOpportunity:
    objective, effects, tradeoffs = _FAMILY_TABLE[family]
    if override_objective is not None:
        objective = override_objective
    if override_effects is not None:
        effects = override_effects
    if override_tradeoffs is not None:
        tradeoffs = override_tradeoffs

    # Protected constraints: declared objectives the family could press on
    protected: list[str] = []
    guard = GuardrailStatus.CLEAR
    note = ""
    metric = _CONSTRAINT_FOR_FAMILY.get(family)
    if metric is not None:
        for obj in objectives:
            if obj.metric is metric:
                protected.append(f"{metric.value}<={obj.target:.0f}{obj.unit} on {obj.scope}")
                guard = GuardrailStatus.REVIEW
                note = f"{metric.value} objective may bound this change"

    # Capability guardrail: when a prerequisite capability is declared and
    # the registry can evaluate it, a provably-blocked dep blocks the opp.
    if registry is not None:
        for pre in _family_capability_prereqs(family):
            try:
                from forge_doctor.core.capability_deps import (
                    CapabilityReadiness,
                    evaluate_dependencies,
                )

                ev = evaluate_dependencies(registry, pre[1], platform=pre[0])
                if ev.readiness is CapabilityReadiness.BLOCKED:
                    guard = GuardrailStatus.BLOCKED
                    note = f"required capability {pre[1]} blocked on {pre[0]}"
            except Exception:
                continue

    digest = hashlib.sha1(f"{family.value}|{target}|{reason}".encode()).hexdigest()
    oid = f"OPP-{int(digest[:6], 16) % 10000:04d}"
    return OptimizationOpportunity(
        id=oid,
        target=target,
        objective=objective,
        family=family,
        evidence=evidence,
        expected_effects=effects,
        negative_tradeoffs=tradeoffs,
        protected_constraints=tuple(protected),
        confidence=confidence,
        guardrail_status=guard,
        guardrail_note=note,
        experiment_plan=experiment,
        unknowns=("workload representativeness", "upstream data drift"),
    )


def _family_capability_prereqs(family: OptimizationFamily) -> list[tuple[str, str]]:
    """(platform, capability) prerequisites a family may depend on."""
    return {
        OptimizationFamily.PARTITION_PRUNING: [("bigquery", "partitioned_tables")],
        OptimizationFamily.STREAM_TRIGGER: [("spark", "structured_streaming")],
    }.get(family, [])


# ---------------------------------------------------------------------------
# Five-state twin integration — opportunities are HYPOTHETICAL facts
# ---------------------------------------------------------------------------


def opportunity_facts(
    opps: list[OptimizationOpportunity],
) -> tuple[Any, ...]:
    """TwinFacts tagging each opportunity as a HYPOTHETICAL proposal."""
    from forge_doctor.core.twin_states import TwinFact, TwinState

    return tuple(
        TwinFact(
            entity=o.target,
            property=f"proposed_{o.family.value}",
            value=o.id,
            state=TwinState.HYPOTHETICAL,
            evidence_kind=EvidenceKind.DERIVED,
            source="optimize_v2",
            confidence="hypothetical",
        )
        for o in opps
    )
