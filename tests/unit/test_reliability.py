"""Spec 238 — reliability & SLA intelligence."""

from __future__ import annotations

from forge_doctor_data.core.execution_model import ExecutionStatus, QueryExecution
from forge_doctor_data.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)
from forge_doctor_data.core.reliability import (
    DeliverySemantics,
    EvidenceState,
    Mechanism,
    ObjectiveMetric,
    ReliabilityModel,
    delivery_semantics,
    extract_objectives,
    extract_reliability,
    failure_domains,
    freshness_paths,
    observe_reliability,
    rel_findings,
)


def _graph() -> DataPlatformGraph:
    return DataPlatformGraph()


def _ent(kind: EntityKind, domain: str, ident: str, **attrs: str) -> Entity:
    return Entity(kind=kind, domain=domain, identifier=ident, attrs=tuple(attrs.items()))


def test_extract_reliability_declared_vs_absent() -> None:
    g = _graph()
    g.add_entity(_ent(EntityKind.COMPUTE_JOB, "spark", "etl", retries="3", dlq="events-dlq"))
    g.add_entity(_ent(EntityKind.COMPUTE_JOB, "spark", "plain"))
    models = extract_reliability(g)
    m = next(x for x in models if x.subject == "etl")
    assert m.state_of("retries") is EvidenceState.DECLARED
    assert m.state_of("dlq") is EvidenceState.DECLARED
    assert m.state_of("checkpointing") is EvidenceState.UNKNOWN
    # entity with no reliability attrs produces no model
    assert not any(x.subject == "plain" for x in models)


def test_negated_attr_is_absent_not_declared() -> None:
    g = _graph()
    g.add_entity(_ent(EntityKind.COMPUTE_JOB, "spark", "etl", dlq="none"))
    m = extract_reliability(g)[0]
    assert m.state_of("dlq") is EvidenceState.ABSENT


def test_delivery_semantics_ladder() -> None:
    retry_only = ReliabilityModel(
        subject="s",
        engine="kafka",
        mechanisms=(Mechanism("retries", EvidenceState.DECLARED, "retries=3"),),
    )
    assert delivery_semantics(retry_only) is DeliverySemantics.AT_LEAST_ONCE

    eff_once = ReliabilityModel(
        subject="s",
        engine="spark",
        mechanisms=(
            Mechanism("retries", EvidenceState.DECLARED, "retries=3"),
            Mechanism("idempotency", EvidenceState.DECLARED, "idempotent=true"),
        ),
    )
    assert delivery_semantics(eff_once) is DeliverySemantics.EFFECTIVELY_ONCE


def test_exactly_once_never_claimed_without_full_path() -> None:
    claimed_only = ReliabilityModel(
        subject="s",
        engine="kafka",
        mechanisms=(Mechanism("delivery", EvidenceState.DECLARED, "exactly_once=true"),),
    )
    assert delivery_semantics(claimed_only) is not DeliverySemantics.EXACTLY_ONCE_CLAIMED

    full = ReliabilityModel(
        subject="s",
        engine="flink",
        mechanisms=(
            Mechanism("delivery", EvidenceState.DECLARED, "exactly_once=true"),
            Mechanism("retries", EvidenceState.DECLARED, "retries=3"),
            Mechanism("idempotency", EvidenceState.DECLARED, "idempotent=true"),
            Mechanism("deduplication", EvidenceState.DECLARED, "dedup_key=k"),
            Mechanism("checkpointing", EvidenceState.DECLARED, "checkpoint=on"),
        ),
    )
    assert delivery_semantics(full) is DeliverySemantics.EXACTLY_ONCE_CLAIMED


def test_freshness_partial_not_invented() -> None:
    g = _graph()
    g.add_entity(_ent(EntityKind.TABLE, "aws", "src", event_time="1000"))
    g.add_entity(_ent(EntityKind.TABLE, "aws", "wh", ingested_at="1060"))
    g.add_relationship(Relationship(src="table:aws:src", dst="table:aws:wh", kind=RelKind.WRITES))
    paths = freshness_paths(g)
    assert len(paths) == 1
    p = paths[0]
    assert not p.complete and p.total_lag is None
    assert p.to_dict()["status"] == "partial"


def test_freshness_complete_lag() -> None:
    g = _graph()
    g.add_entity(_ent(EntityKind.TABLE, "aws", "src", event_time="1000"))
    g.add_entity(_ent(EntityKind.TABLE, "aws", "wh", ingested_at="1060", served_at="1120"))
    g.add_relationship(Relationship(src="table:aws:src", dst="table:aws:wh", kind=RelKind.WRITES))
    p = freshness_paths(g)[0]
    assert p.complete and p.total_lag == 120.0


def test_rel001_retry_without_idempotency() -> None:
    m = ReliabilityModel(
        subject="etl",
        engine="spark",
        mechanisms=(Mechanism("retries", EvidenceState.DECLARED, "retries=3"),),
    )
    ids = {f.check_id for f in rel_findings([m])}
    assert "REL001" in ids and "REL009" in ids and "REL007" in ids


def test_rel002_stateful_stream_without_checkpoint() -> None:
    m = ReliabilityModel(
        subject="click-stream",
        engine="kafka",
        mechanisms=(Mechanism("retries", EvidenceState.DECLARED, "retries=1"),),
    )
    assert any(f.check_id == "REL002" for f in rel_findings([m]))


def test_rel003_freshness_mismatch_and_partial() -> None:
    g = _graph()
    g.add_entity(_ent(EntityKind.TABLE, "aws", "src", event_time="1000"))
    g.add_entity(
        _ent(
            EntityKind.TABLE,
            "aws",
            "wh",
            ingested_at="1060",
            served_at="2000",
            sla_freshness="60",
        )
    )
    g.add_relationship(Relationship(src="table:aws:src", dst="table:aws:wh", kind=RelKind.WRITES))
    objs = extract_objectives(g)
    fps = freshness_paths(g)
    findings = rel_findings([], objs, fps)
    rel3 = [f for f in findings if f.check_id == "REL003"]
    assert rel3 and rel3[0].severity.value == "warning"


def test_rel004_observed_latency_exceeds() -> None:
    from forge_doctor_data.core.reliability import ServiceObjective

    obj = ServiceObjective(
        metric=ObjectiveMetric.LATENCY,
        target=100.0,
        unit="ms",
        scope="q1",
        source="contract",
    )
    ex = QueryExecution(
        execution_id="e1", engine="trino", duration_ms=500.0, status=ExecutionStatus.COMPLETED
    )
    assert any(f.check_id == "REL004" for f in rel_findings([], [obj], [], [ex]))


def test_rel006_rto_path_incomplete() -> None:
    from forge_doctor_data.core.reliability import ServiceObjective

    obj = ServiceObjective(
        metric=ObjectiveMetric.RTO, target=300.0, unit="seconds", scope="etl", source="contract"
    )
    m = ReliabilityModel(
        subject="etl",
        engine="spark",
        mechanisms=(Mechanism("backup", EvidenceState.DECLARED, "backup=true"),),
    )
    out = rel_findings([m], [obj])
    assert any(f.check_id == "REL006" for f in out)
    assert any(f.check_id == "REL010" for f in out)


def test_rel008_failover_unresolved() -> None:
    m = ReliabilityModel(
        subject="db",
        engine="postgres",
        mechanisms=(Mechanism("replication", EvidenceState.DECLARED, "replicas=2"),),
    )
    assert any(f.check_id == "REL008" for f in rel_findings([m]))


def test_observe_reliability_upgrades_on_recovery() -> None:
    g = _graph()
    g.add_entity(_ent(EntityKind.COMPUTE_JOB, "spark", "job1", retries="3"))
    models = extract_reliability(g)
    exs = [
        QueryExecution(
            execution_id="a",
            engine="spark",
            query_fingerprint="job1",
            status=ExecutionStatus.FAILED,
        ),
        QueryExecution(
            execution_id="b",
            engine="spark",
            query_fingerprint="job1",
            status=ExecutionStatus.COMPLETED,
        ),
    ]
    obs = observe_reliability(models, exs)
    assert obs[0].state_of("retries") is EvidenceState.OBSERVED


def test_failure_domains_topology_only() -> None:
    g = _graph()
    g.add_entity(_ent(EntityKind.TABLE, "aws", "a", region="us-east-1"))
    g.add_entity(_ent(EntityKind.TABLE, "aws", "b", region="us-east-1"))
    g.add_entity(_ent(EntityKind.TABLE, "aws", "c"))
    doms = failure_domains(g)
    region = [d for d in doms if d.kind == "region"]
    assert len(region) == 1 and len(region[0].members) == 2
    assert any(d.kind == "cloud" and d.identifier == "aws" for d in doms)


def test_determinism_serialization() -> None:
    g = _graph()
    g.add_entity(_ent(EntityKind.COMPUTE_JOB, "spark", "etl", retries="3", dlq="d"))
    a = [m.to_dict() for m in extract_reliability(g)]
    b = [m.to_dict() for m in extract_reliability(g)]
    assert a == b
    f = rel_findings(extract_reliability(g))
    assert [x.to_dict() for x in f] == [x.to_dict() for x in f]
