"""Migration Intelligence 2.0 (spec 234).

Concept-level mapping driven by the ontology + capability layer instead
of vendor x vendor matrices: a source service resolves to a *logical
concept* (PlatformKind-shaped), the concept declares its required
capability set, and each target implementation is graded on how its
pack answers those requirements.

Fact vs conclusion (spec §11): pack statuses are facts; a mapping's
lossiness is a conclusion drawn from declared semantic/operational
gaps. Absent evidence resolves to UNKNOWN, never to a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from forge_doctor.core.platform_ontology import PlatformKind

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext


class MappingKind(Enum):
    """How faithfully a target implementation covers the source."""

    DIRECT = "direct"  # same concept, no declared gap
    APPROXIMATE = "approximate"  # same concept, declared gap(s)
    REDESIGN_REQUIRED = "redesign_required"  # concept exists, semantics differ structurally
    NO_EQUIVALENT = "no_equivalent"  # nothing on target covers the concept
    UNKNOWN = "unknown"  # missing evidence — never guessed


class Lossiness(Enum):
    """What the mapping can cost, in ascending order of review burden."""

    LOSSLESS = "lossless"
    SEMANTIC_CHANGE = "semantic_change"
    OPERATIONAL_CHANGE = "operational_change"
    PERFORMANCE_CHANGE = "performance_change"
    SECURITY_CHANGE = "security_change"
    MANUAL_REDESIGN = "manual_redesign"
    UNKNOWN = "unknown"


class ReadinessStatus(Enum):
    READY = "ready"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class MigrationConcept:
    """One logical concept mapped source -> target implementation."""

    logical_concept: str  # e.g. managed_stream | columnar_mpp_warehouse
    source_implementation: str  # detected service (kinesis, eventhubs, ...)
    target_implementation: str  # "" when nothing on target covers it
    mapping: MappingKind
    lossiness: Lossiness
    semantic_gap: str = ""  # behavior delta (ordering, semantics, ...)
    operational_gap: str = ""  # ops delta (retry, scaling, topology, ...)
    confidence: str = "low"  # high | medium | low — detector certainty
    capability_gaps: tuple[str, ...] = ()  # required caps unsupported on target
    missing_evidence: tuple[str, ...] = ()  # caps whose status is unknown
    evidence: tuple[str, ...] = ()  # source-side facts that anchored this


@dataclass(frozen=True)
class MigrationReadiness:
    """Aggregate readiness + the unknown budget."""

    status: ReadinessStatus
    known_count: int
    unknown_count: int
    unknowns: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    runtime_informed: bool = False


# ---------------------------------------------------------------------------
# Concept registry — logical concept -> per-service implementations.
#
# Each implementation declares the capability-pack platform that answers for
# it ("" = no pack; status lookups then yield unknown), the concept's
# required capability ids, and honest declared gaps. Gaps are documentation,
# not findings.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Impl:
    pack_platform: str
    required_caps: tuple[str, ...] = ()
    semantic_gap: str = ""
    operational_gap: str = ""


# logical concept -> PlatformKind -> {service: impl}
_CONCEPTS: dict[str, tuple[PlatformKind, dict[str, _Impl]]] = {
    "columnar_mpp_warehouse": (
        PlatformKind.WAREHOUSE,
        {
            "redshift": _Impl(
                "redshift",
                ("REDSHIFT_MPP_STORAGE",),
                operational_gap="dist/sort keys -> target clustering model",
            ),
            "snowflake": _Impl(
                "snowflake",
                ("SNOWFLAKE_MICRO_PARTITIONS",),
                operational_gap="credits-based compute; result cache differs",
            ),
            "bigquery": _Impl(
                "bigquery",
                ("BIGQUERY_SLOTS_MODEL",),
                semantic_gap="nested/repeated native; no secondary indexes",
                operational_gap="slot reservations -> compute units",
            ),
            "synapse_sql": _Impl(
                "synapse",
                ("SYNAPSE_SERVERLESS_SQL",),
                semantic_gap="serverless vs dedicated pools behave differently",
            ),
            "databricks_sql": _Impl(
                "databricks",
                (),
                semantic_gap="Delta table semantics; Photon runtime",
            ),
        },
    ),
    "object_store": (
        PlatformKind.OBJECT_STORAGE,
        {
            "s3": _Impl("aws", (), operational_gap="bucket model -> container/bucket equivalent"),
            "adls_gen2": _Impl(
                "adls",
                ("ADLS_HIERARCHICAL_NAMESPACE",),
                semantic_gap="hierarchical namespace (dirs/ACLs) vs flat object store",
            ),
            "gcs": _Impl("gcs", (), operational_gap="single-tier namespace, no POSIX"),
            "dbfs": _Impl(
                "databricks",
                (),
                semantic_gap="workspace-local mount, not a general-purpose store",
            ),
            "snowflake_stage": _Impl(
                "snowflake",
                (),
                semantic_gap="stage is an object reference, not a store of record",
            ),
        },
    ),
    "managed_stream": (
        PlatformKind.STREAM,
        {
            "kinesis": _Impl(
                "kinesis",
                ("KINESIS_REPLAYABLE_SOURCE",),
                operational_gap="shard topology -> target partitions",
            ),
            "msk": _Impl("kafka", (), semantic_gap="full Kafka protocol surface"),
            "kafka": _Impl("kafka", (), semantic_gap="full Kafka protocol surface"),
            "eventhubs": _Impl(
                "eventhubs",
                ("EVENTHUBS_KAFKA_ENDPOINT", "EVENTHUBS_CAPTURE"),
                semantic_gap="Kafka-compatible endpoint, not full Kafka semantics",
            ),
            "pubsub": _Impl(
                "pubsub",
                ("PUBSUB_EXACTLY_ONCE", "PUBSUB_SEEK"),
                semantic_gap="no partitions; ordering is per-key, not global",
            ),
            "firehose": _Impl(
                "aws",
                (),
                semantic_gap="delivery stream, not a consumer-facing topic",
            ),
            "snowpipe": _Impl(
                "snowflake",
                (),
                semantic_gap="ingest-only pipe, not a pub/sub log",
            ),
        },
    ),
    "managed_spark": (
        PlatformKind.COMPUTE,
        {
            "glue": _Impl(
                "glue",
                ("GLUE_ELT_SPARK",),
                operational_gap="serverless jobs -> cluster/workspace compute",
            ),
            "emr": _Impl("emr", (), operational_gap="cluster lifecycle vs workspace"),
            "dataproc": _Impl(
                "dataproc",
                ("DATAPROC_AUTOSCALING",),
                operational_gap="per-job clusters vs workspace",
            ),
            "databricks": _Impl(
                "databricks",
                (),
                operational_gap="interactive workspaces; job clusters",
            ),
            "synapse": _Impl(
                "synapse",
                ("SYNAPSE_SPARK_AUTOSCALE",),
                operational_gap="pool-bound Spark, not standalone jobs",
            ),
            "dataflow": _Impl(
                "dataflow",
                ("DATAFLOW_AUTOSCALING",),
                semantic_gap="Beam model, not Spark — code must be rewritten",
            ),
        },
    ),
    "orchestrator": (
        PlatformKind.ORCHESTRATOR,
        {
            "adf": _Impl(
                "azure_fabric",
                (),
                semantic_gap="pipeline/activity model, not DAG operators",
            ),
            "composer": _Impl(
                "gcp",
                (),
                operational_gap="managed Airflow — closest for Airflow sources",
            ),
            "airflow": _Impl(
                "airflow",
                (),
                semantic_gap="self-managed DAGs vs managed scheduler surfaces",
            ),
            "stepfunctions": _Impl(
                "stepfunctions",
                (),
                semantic_gap="state machines, not data-pipeline DAGs",
            ),
            "snowflake_task": _Impl(
                "snowflake",
                (),
                semantic_gap="scheduled SQL/stream task, not a general orchestrator",
            ),
        },
    ),
    "catalog_governance": (
        PlatformKind.CATALOG,
        {
            "glue": _Impl("glue", (), semantic_gap="Hive-metastore flavored catalog"),
            "purview": _Impl(
                "purview",
                ("PURVIEW_LINEAGE", "PURVIEW_AUTOMATED_SCAN"),
            ),
            "datacatalog": _Impl("gcp", ()),
            "dataplex": _Impl(
                "dataplex",
                ("DATAPLEX_LAKE_ZONES", "DATAPLEX_DATA_QUALITY"),
                semantic_gap="lake/zone governance model, not just a metastore",
            ),
            "unity": _Impl("databricks", ()),
            "lakeformation": _Impl("lakeformation", ()),
        },
    ),
    "operational_kv": (
        PlatformKind.OPERATIONAL_STORE,
        {
            "dynamodb": _Impl(
                "dynamodb",
                ("DYNAMODB_GSI", "DYNAMODB_STREAMS"),
                operational_gap="RCU/WCU model -> target request units",
            ),
            "cosmosdb": _Impl("azure", (), semantic_gap="multi-model API surface"),
            "bigtable": _Impl("gcp", (), semantic_gap="wide-column store, not document"),
        },
    ),
    "lakehouse_format": (
        PlatformKind.TABLE_FORMAT,
        {
            "delta": _Impl(
                "delta",
                (),
                semantic_gap="Delta log + UniForm; Databricks-first features",
            ),
            "iceberg": _Impl(
                "iceberg",
                (),
                semantic_gap="snapshot/manifest model; engine-neutral",
            ),
            "hudi": _Impl("hudi", (), semantic_gap="upsert-first table format"),
        },
    ),
}

# abstraction (spec 223) -> logical concept, so the abstraction layer feeds
# concept resolution without duplicating the service list.
_ABSTRACTION_CONCEPT: dict[str, str] = {
    "object_storage": "object_store",
    "stream": "managed_stream",
    "compute_engine": "managed_spark",
    "catalog": "catalog_governance",
    "operational_store": "operational_kv",
    "warehouse": "columnar_mpp_warehouse",
    "orchestrator": "orchestrator",
}


def _service_concept(service: str) -> str:
    for concept, (_, impls) in _CONCEPTS.items():
        if service in impls:
            return concept
    return ""


def concept_for_abstraction(abstraction: str) -> str:
    """Vendor-neutral abstraction -> logical concept ("" if unmapped)."""
    return _ABSTRACTION_CONCEPT.get(abstraction, "")


def concept_implementations(concept: str) -> tuple[str, ...]:
    impls = _CONCEPTS.get(concept)
    return tuple(sorted(impls[1])) if impls else ()


def platform_kind_for(service_or_concept: str) -> PlatformKind | None:
    """Resolve a service or concept to its ontology PlatformKind."""
    for concept, (kind, impls) in _CONCEPTS.items():
        if service_or_concept == concept or service_or_concept in impls:
            return kind
    return None


def map_service(
    source_service: str, target_service: str, *, abstraction: str = ""
) -> MigrationConcept:
    """Map one source service onto a target implementation via concepts.

    ``target_service`` may be "" (caller had no candidate) — the concept
    registry then picks nothing and the mapping reports NO_EQUIVALENT /
    UNKNOWN honestly.
    """
    concept = _service_concept(source_service) or _ABSTRACTION_CONCEPT.get(abstraction, "")
    if not concept:
        return MigrationConcept(
            logical_concept=abstraction or "unmapped",
            source_implementation=source_service,
            target_implementation=target_service,
            mapping=MappingKind.UNKNOWN,
            lossiness=Lossiness.UNKNOWN,
            confidence="low",
            missing_evidence=(f"no concept registered for {source_service}",),
            evidence=("abstraction view",),
        )
    kind, impls = _CONCEPTS[concept]
    src = impls.get(source_service, _Impl(""))

    if source_service == target_service:
        # staying on the same implementation is never a migration gap —
        # declared gaps describe arriving at a service, not remaining on it.
        return MigrationConcept(
            logical_concept=concept,
            source_implementation=source_service,
            target_implementation=target_service,
            mapping=MappingKind.DIRECT,
            lossiness=Lossiness.LOSSLESS,
            confidence="high",
            evidence=(f"platform_kind={kind.value}", "identity mapping"),
        )

    tgt = impls.get(target_service)

    if not target_service or tgt is None:
        has_any_target = bool(impls)
        return MigrationConcept(
            logical_concept=concept,
            source_implementation=source_service,
            target_implementation=target_service,
            mapping=MappingKind.NO_EQUIVALENT if has_any_target else MappingKind.UNKNOWN,
            lossiness=Lossiness.MANUAL_REDESIGN,
            operational_gap=f"no {concept} implementation on target"
            if has_any_target
            else "concept registry empty",
            confidence="low" if not has_any_target else "medium",
            evidence=(f"platform_kind={kind.value}", "concept registry"),
        )

    # Capability-backed grading: required caps on the *target* pack.
    gaps: list[str] = []
    missing: list[str] = []
    for cap in src.required_caps:
        from forge_doctor.core.capabilities import CapabilityContext, capability_registry

        reg = capability_registry()
        if tgt.pack_platform:
            res = reg.evaluate(cap, CapabilityContext(platform=tgt.pack_platform))
            status = res.status.value
            if status == "unsupported":
                gaps.append(cap)
            elif status == "unknown":
                missing.append(cap)
        else:
            missing.append(cap)

    semantic = tgt.semantic_gap
    operational = tgt.operational_gap
    if gaps:
        mapping = MappingKind.REDESIGN_REQUIRED
        lossiness = Lossiness.MANUAL_REDESIGN
    elif semantic:
        mapping = MappingKind.APPROXIMATE
        lossiness = Lossiness.SEMANTIC_CHANGE
    elif operational:
        mapping = MappingKind.APPROXIMATE
        lossiness = Lossiness.OPERATIONAL_CHANGE
    else:
        mapping = MappingKind.DIRECT
        lossiness = Lossiness.LOSSLESS
    if missing and mapping == MappingKind.DIRECT:
        mapping = MappingKind.APPROXIMATE
        lossiness = Lossiness.UNKNOWN
    confidence = "high" if mapping == MappingKind.DIRECT else "medium"
    if missing:
        confidence = "low" if len(missing) > len(gaps) else confidence
    return MigrationConcept(
        logical_concept=concept,
        source_implementation=source_service,
        target_implementation=target_service,
        mapping=mapping,
        lossiness=lossiness,
        semantic_gap=semantic,
        operational_gap=operational,
        confidence=confidence,
        capability_gaps=tuple(sorted(gaps)),
        missing_evidence=tuple(sorted(missing)),
        evidence=(f"platform_kind={kind.value}", "concept registry", "capability packs"),
    )


def explain_concept(concept: MigrationConcept) -> list[str]:
    """`migrate explain` rows: why mapped / what gaps / what evidence."""
    lines = [
        f"{concept.source_implementation} -> "
        f"{concept.target_implementation or 'UNMAPPED'} "
        f"[{concept.mapping.value}/{concept.lossiness.value}, "
        f"{concept.confidence}] concept={concept.logical_concept}",
    ]
    if concept.semantic_gap:
        lines.append(f"  semantic gap: {concept.semantic_gap}")
    if concept.operational_gap:
        lines.append(f"  operational gap: {concept.operational_gap}")
    for cap in concept.capability_gaps:
        lines.append(f"  capability missing on target: {cap}")
    for cap in concept.missing_evidence:
        lines.append(f"  unknown capability evidence: {cap}")
    for ev in concept.evidence:
        lines.append(f"  evidence: {ev}")
    return lines


# ---------------------------------------------------------------------------
# Readiness


def assess_readiness(
    concepts: list[MigrationConcept],
    lost_capabilities: list[str] | tuple[str, ...],
    *,
    runtime_sources: tuple[str, ...] = (),
) -> MigrationReadiness:
    """Aggregate per-concept mappings into one readiness verdict.

    READY: every concept DIRECT and nothing lost.
    PARTIAL: approximates/reviews present, no blockers.
    BLOCKED: a NO_EQUIVALENT/REDESIGN concept or a lost capability.
    INSUFFICIENT_EVIDENCE: nothing mapped or unknowns dominate.
    """
    if not concepts:
        return MigrationReadiness(
            status=ReadinessStatus.INSUFFICIENT_EVIDENCE,
            known_count=0,
            unknown_count=0,
            unknowns=("no source services detected for the migration",),
            required_evidence=("terraform/config evidence of source services",),
        )
    unknowns: list[str] = []
    required: list[str] = []
    blocked = False
    partial = False
    for c in concepts:
        if c.mapping in (MappingKind.NO_EQUIVALENT, MappingKind.REDESIGN_REQUIRED):
            blocked = True
        elif c.mapping in (MappingKind.APPROXIMATE,):
            partial = True
        elif c.mapping == MappingKind.UNKNOWN:
            unknowns.append(f"{c.source_implementation}: concept unknown")
        for cap in c.missing_evidence:
            unknowns.append(f"{c.source_implementation}->{c.target_implementation}: {cap} unknown")
            required.append(f"capability evidence for {cap} on {c.target_implementation}")
    if lost_capabilities:
        blocked = True
    if blocked:
        status = ReadinessStatus.BLOCKED
    elif unknowns and not partial:
        status = ReadinessStatus.INSUFFICIENT_EVIDENCE
    elif partial or unknowns:
        status = ReadinessStatus.PARTIAL
    else:
        status = ReadinessStatus.READY
    known = sum(1 for c in concepts if c.mapping not in (MappingKind.UNKNOWN,))
    return MigrationReadiness(
        status=status,
        known_count=known,
        unknown_count=len(unknowns),
        unknowns=tuple(sorted(set(unknowns))),
        required_evidence=tuple(sorted(set(required))),
        runtime_informed=bool(runtime_sources),
    )


def detect_runtime_sources(ctx: ProjectContext) -> tuple[str, ...]:
    """Names of runtime adapters whose committed artifacts matched —
    files only, no calls."""
    from forge_doctor.analyzers.runtime_evidence import ADAPTERS

    hits: list[str] = []
    for rel in sorted(ctx.files):
        if rel.suffix.lower() not in (".json", ".jsonl", ".ndjson"):
            continue
        path = ctx.root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:60000]
        except OSError:
            continue
        for adapter in ADAPTERS:
            if adapter.matches(path, text):
                hits.append(adapter.name)
                break
    return tuple(sorted(set(hits)))
