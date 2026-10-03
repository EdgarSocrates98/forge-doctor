"""Cross-platform migration planning (spec 224).

Plans are *reports*, never executable deploys: a deterministic document
mapping the source platform's services/entities onto the target
ecosystem via the abstraction layer (spec 223), grading confidence per
translation, and diffing capability packs for lost/gained/equivalent
facts. Ordered stages follow catalog -> schema -> data -> compute ->
consumers; MIGR### findings flag hard blockers, semantic reviews, and
unmapped consumers. No transpiled DDL claims — notes are advisory with
source-evidence links (spec constraint).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.migration_v2 import MigrationConcept, MigrationReadiness
    from forge_doctor_data.core.models import CheckResult
    from forge_doctor_data.core.sql_portability import SqlPortabilityFinding

# target platform -> ecosystem service per abstraction
_ECOSYSTEM: dict[str, dict[str, str]] = {
    "bigquery": {
        "object_storage": "gcs",
        "stream": "pubsub",
        "compute_engine": "dataproc",
        "catalog": "datacatalog",
        "operational_store": "bigtable",
        "warehouse": "bigquery",
    },
    "redshift": {
        "object_storage": "s3",
        "stream": "kinesis",
        "compute_engine": "emr",
        "catalog": "glue",
        "operational_store": "dynamodb",
        "warehouse": "redshift",
    },
    "synapse": {
        "object_storage": "adls_gen2",
        "stream": "eventhubs",
        "compute_engine": "synapse",
        "catalog": "purview",
        "operational_store": "cosmosdb",
        "warehouse": "synapse_sql",
    },
    "synapse_sql": {
        "object_storage": "adls_gen2",
        "stream": "eventhubs",
        "compute_engine": "synapse",
        "catalog": "purview",
        "operational_store": "cosmosdb",
        "warehouse": "synapse_sql",
    },
    "snowflake": {
        "object_storage": "snowflake_stage",
        "stream": "snowpipe",
        "compute_engine": "snowflake_task",
        "catalog": "snowflake_catalog",
        "warehouse": "snowflake",
    },
    "databricks": {
        "object_storage": "dbfs",
        "stream": "kafka",
        "compute_engine": "databricks",
        "catalog": "unity",
        "warehouse": "databricks_sql",
    },
    # cloud-level targets (spec 234): aws->azure / aws->gcp / azure->gcp
    "azure": {
        "object_storage": "adls_gen2",
        "stream": "eventhubs",
        "compute_engine": "synapse",
        "catalog": "purview",
        "operational_store": "cosmosdb",
        "warehouse": "synapse_sql",
        "orchestrator": "adf",
    },
    "gcp": {
        "object_storage": "gcs",
        "stream": "pubsub",
        "compute_engine": "dataproc",
        "catalog": "dataplex",
        "operational_store": "bigtable",
        "warehouse": "bigquery",
        "orchestrator": "composer",
    },
    "aws": {
        "object_storage": "s3",
        "stream": "kinesis",
        "compute_engine": "emr",
        "catalog": "glue",
        "operational_store": "dynamodb",
        "warehouse": "redshift",
        "orchestrator": "stepfunctions",
    },
}

# capability-pack platform name per target ecosystem
_PACK_PLATFORM = {
    "bigquery": "bigquery",
    "redshift": "redshift",
    "snowflake": "snowflake",
    "databricks": "databricks",
    "synapse": "synapse",
    "synapse_sql": "synapse",
    "azure": "adls",
    "gcp": "pubsub",
    "aws": "glue",
}

# (source_service, abstraction) -> required-change note
_CHANGE_NOTES: dict[tuple[str, str], str] = {
    ("snowflake", "warehouse"): (
        "port DDL — sqlglot dialect notes; review clustering keys, "
        " masking policies, and time-travel window semantics"
    ),
    ("snowflake", "object_storage"): ("external stage -> target object store + external table def"),
    ("snowflake", "stream"): ("Snowflake STREAM object -> target change-capture / pubsub feed"),
    ("snowflake", "compute_engine"): (
        "TASK/PIPE -> scheduled job or orchestrator (composer/airflow)"
    ),
    ("bigquery", "warehouse"): (
        "dataset/table -> target schema; slot reservations -> compute units"
    ),
    ("redshift", "warehouse"): (
        "dist/sort keys -> target partitioning/clustering; STL/SVV observability views don't port"
    ),
    ("kinesis", "stream"): "shard topology -> target partitions/topics",
    ("dynamodb", "operational_store"): ("table + GSIs -> target store; RCU/WCU -> request units"),
    ("glue", "catalog"): "catalog databases/tables -> target metastore",
}

_STAGE_ORDER = ("catalog", "schema", "data", "compute", "consumers")
_STAGE_FOR = {
    "catalog": "catalog",
    "warehouse": "schema",
    "object_storage": "data",
    "stream": "data",
    "operational_store": "data",
    "compute_engine": "compute",
    "orchestrator": "compute",
}


@dataclass(frozen=True)
class EntityMapping:
    """One source service -> target ecosystem mapping."""

    source: str  # service:name
    abstraction: str
    target_service: str  # "" = no equivalent (blocker territory)
    note: str
    confidence: str  # high | medium | low


@dataclass(frozen=True)
class CapabilityDelta:
    capability: str
    source_status: str
    target_status: str
    delta: str  # lost | gained | equivalent | review


@dataclass(frozen=True)
class StagePlan:
    stage: str
    items: tuple[str, ...]


@dataclass
class PlatformMigrationPlan:
    """Deterministic advisory plan — report, not deploy."""

    source: str
    target: str
    entity_map: list[EntityMapping] = field(default_factory=list)
    capability_deltas: list[CapabilityDelta] = field(default_factory=list)
    stages: list[StagePlan] = field(default_factory=list)
    unmapped_consumers: list[str] = field(default_factory=list)
    findings: list[CheckResult] = field(default_factory=list)
    # spec-234 additive fields (JSON serialization stays compatible —
    # new keys only)
    concepts: list[MigrationConcept] = field(default_factory=list)
    readiness: MigrationReadiness | None = None
    sql_findings: list[SqlPortabilityFinding] = field(default_factory=list)

    def lost_capabilities(self) -> list[str]:
        return [d.capability for d in self.capability_deltas if d.delta == "lost"]

    def gained_capabilities(self) -> list[str]:
        return [d.capability for d in self.capability_deltas if d.delta == "gained"]


def _confidence(source_service: str, abstraction: str, target_svc: str) -> str:
    if not target_svc:
        return "low"
    if (source_service, abstraction) in _CHANGE_NOTES:
        return "medium"
    return "high"


def _stage_items(plan: PlatformMigrationPlan) -> list[StagePlan]:
    buckets: dict[str, list[str]] = {s: [] for s in _STAGE_ORDER}
    for m in plan.entity_map:
        tgt = m.target_service or "UNMAPPED"
        buckets[_STAGE_FOR.get(m.abstraction, "consumers")].append(
            f"{m.source} -> {tgt}: {m.note} [{m.confidence}]"
        )
    if plan.unmapped_consumers:
        buckets["consumers"] += [
            f"consumer {c} has no target link — rewire manually" for c in plan.unmapped_consumers
        ]
    return [
        StagePlan(stage=s, items=tuple(sorted(set(buckets[s])))) for s in _STAGE_ORDER if buckets[s]
    ]


def _capability_deltas(source: str, target: str) -> list[CapabilityDelta]:
    from forge_doctor_data.core.capabilities import CapabilityContext, capability_registry

    reg = capability_registry()
    src_pack = _PACK_PLATFORM.get(source, source)
    tgt_pack = _PACK_PLATFORM.get(target, target)
    cap_ids = set(reg.capabilities_for(src_pack)) | set(reg.capabilities_for(tgt_pack))
    deltas: list[CapabilityDelta] = []
    for cap in sorted(cap_ids):
        s = reg.evaluate(cap, CapabilityContext(platform=src_pack)).status.value
        t = reg.evaluate(cap, CapabilityContext(platform=tgt_pack)).status.value
        if s == t == "supported":
            delta = "equivalent"
        elif t == "unsupported" and s == "supported":
            delta = "lost"
        elif t == "supported" and s in ("unsupported", "unknown"):
            delta = "gained"
        else:
            delta = "review"
        deltas.append(
            CapabilityDelta(
                capability=cap,
                source_status=s,
                target_status=t,
                delta=delta,
            )
        )
    return deltas


def _unmapped_consumers(ctx: ProjectContext, source: str, mapped_names: set[str]) -> list[str]:
    """Downstream entities reading migrated tables with no target link."""
    from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph
    from forge_doctor_data.core.platform_graph import RelKind

    try:
        g = build_platform_graph(ctx)
    except Exception:
        return []
    migrated_ids = {
        e.id
        for e in g.entities()
        if e.identifier.lower() in mapped_names
        or e.identifier.rsplit(".", 1)[-1].lower() in mapped_names
    }
    if not migrated_ids:
        return []
    consumer_domains = {"metadata", "quality", "sql", "warehouse", source}
    out: set[str] = set()
    for rel in g.relationships():
        if rel.kind not in (
            RelKind.READS_FROM,
            RelKind.READS,
            RelKind.INVOKES,
            RelKind.DEPENDS_ON,
        ):
            continue
        if rel.dst not in migrated_ids:
            continue
        ent = g.entity(rel.src) if hasattr(g, "entity") else None
        if ent is None or ent.domain in consumer_domains:
            continue
        out.add(f"{ent.id}")
    return sorted(out)


# ---------------------------------------------------------------------------
# MIGR checks — plan-scoped (not project-scoped builtins)


def _migr_findings(plan: PlatformMigrationPlan) -> list[CheckResult]:
    from forge_doctor_data.core.models import CheckResult, EvidenceKind, Severity

    def _r(
        cid: str,
        title: str,
        sev: Severity,
        msg: str,
        fix: str,
        ev: str,
        kind: EvidenceKind,
    ) -> CheckResult:
        return CheckResult(
            check_id=cid,
            title=title,
            severity=sev,
            category="migration",
            message=msg,
            recommendation=fix,
            evidence=ev,
            evidence_kind=kind,
        )

    out: list[CheckResult] = []
    # MIGR001 — feature with no target equivalent (hard blocker)
    for d in plan.capability_deltas:
        if d.delta == "lost":
            out.append(
                _r(
                    "MIGR001",
                    "Feature without target equivalent",
                    Severity.ERROR,
                    f"{d.capability} is supported on {plan.source} "
                    f"but unsupported on {plan.target} — hard blocker",
                    "Choose a compensating pattern or re-scope the migration.",
                    f"{d.source_status} -> {d.target_status}",
                    EvidenceKind.CONFIG,
                )
            )
    for m in plan.entity_map:
        if not m.target_service:
            out.append(
                _r(
                    "MIGR001",
                    "Service without target equivalent",
                    Severity.ERROR,
                    f"{m.source} ({m.abstraction}) has no {plan.target} ecosystem equivalent",
                    "Introduce an equivalent service or drop the dependency.",
                    m.note,
                    EvidenceKind.CONFIG,
                )
            )
    # MIGR002 — semantic difference needing manual review
    for d in plan.capability_deltas:
        if d.delta == "review":
            out.append(
                _r(
                    "MIGR002",
                    "Capability semantics differ",
                    Severity.WARNING,
                    f"{d.capability}: {plan.source}={d.source_status} vs "
                    f"{plan.target}={d.target_status} — semantic review needed",
                    "Review the capability semantics manually before cutover.",
                    f"{d.source_status} -> {d.target_status}",
                    EvidenceKind.CONFIG,
                )
            )
    # MIGR003 — consumers with no target link
    for c in plan.unmapped_consumers:
        out.append(
            _r(
                "MIGR003",
                "Unmapped downstream consumer",
                Severity.WARNING,
                f"downstream consumer {c} has no {plan.target} link",
                "Map the consumer onto the target or plan a parallel run.",
                c,
                EvidenceKind.STATIC,
            )
        )
    return out


def plan_platform_migration(ctx: ProjectContext, source: str, target: str) -> PlatformMigrationPlan:
    """Build the deterministic cross-platform plan."""
    from forge_doctor_data.analyzers.abstractions import abstractions_model

    source = source.lower().replace("-", "_")
    target = target.lower().replace("-", "_")
    ecosystem = _ECOSYSTEM.get(target, {})
    model = abstractions_model(ctx)
    plan = PlatformMigrationPlan(source=source, target=target)

    mapped_names: set[str] = set()
    for s in model.services:
        if s.service != source and s.cloud != source:
            continue
        mapped_names.add(s.name.lower())
        tgt_svc = ecosystem.get(s.abstraction, "")
        note = _CHANGE_NOTES.get(
            (s.service, s.abstraction),
            f"port {s.abstraction} to {tgt_svc or 'UNMAPPED'}",
        )
        plan.entity_map.append(
            EntityMapping(
                source=f"{s.service}:{s.name}",
                abstraction=s.abstraction,
                target_service=tgt_svc,
                note=note,
                confidence=_confidence(s.service, s.abstraction, tgt_svc),
            )
        )

    plan.capability_deltas = _capability_deltas(source, target)
    plan.unmapped_consumers = _unmapped_consumers(ctx, source, mapped_names)
    plan.stages = _stage_items(plan)
    plan.findings = _migr_findings(plan)

    # spec 234 — ontology-driven concept mapping + readiness (additive)
    from forge_doctor_data.core.migration_v2 import (
        assess_readiness,
        detect_runtime_sources,
        map_service,
    )

    for s, m in zip(
        (svc for svc in model.services if svc.service == source or svc.cloud == source),
        plan.entity_map,
        strict=True,
    ):
        plan.concepts.append(map_service(s.service, m.target_service, abstraction=m.abstraction))
    plan.readiness = assess_readiness(
        plan.concepts,
        plan.lost_capabilities(),
        runtime_sources=detect_runtime_sources(ctx),
    )

    # SQL portability when both ends are known dialects — static text
    # analysis over committed .sql files only.
    from forge_doctor_data.core.sql_portability import dialect_capabilities, scan_project_sql

    if dialect_capabilities(source) and dialect_capabilities(target):
        texts: dict[Path, str] = {}
        for rel in sorted(ctx.files):
            if rel.suffix.lower() != ".sql":
                continue
            try:
                texts[rel] = (ctx.root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        plan.sql_findings = scan_project_sql(texts, source, target)
    return plan
