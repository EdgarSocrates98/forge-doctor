"""Spec 245 — critical paths, SLO budgets, SLO001-006."""

from __future__ import annotations

from forge_doctor_data.core.critical_path import (
    SLOCheckId,
    critical_paths,
    slo_budgets,
    slo_findings,
)
from forge_doctor_data.core.execution_model import (
    ExecutionStatus,
    QueryExecution,
)
from forge_doctor_data.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)
from forge_doctor_data.core.reliability import ObjectiveMetric, ServiceObjective


def _ent(
    identifier: str,
    kind=EntityKind.COMPUTE_JOB,
    domain: str = "aws",
    **attrs: str,
) -> Entity:
    return Entity(kind=kind, domain=domain, identifier=identifier, attrs=tuple(attrs.items()))


def _edge(src: str, dst: str, kind=RelKind.WRITES) -> Relationship:
    return Relationship(src=src, dst=dst, kind=kind)


def _linear_graph() -> DataPlatformGraph:
    """src -> etl -> out data flow."""
    g = DataPlatformGraph()
    g.add_entity(_ent("src", kind=EntityKind.DATASET))
    g.add_entity(_ent("etl"))
    g.add_entity(_ent("out", kind=EntityKind.DATASET))
    g.add_relationship(_edge("dataset:aws:src", "compute_job:aws:etl", RelKind.TRIGGERS))
    g.add_relationship(_edge("compute_job:aws:etl", "dataset:aws:out", RelKind.WRITES))
    return g


def _ex(qid: str, dur: float) -> QueryExecution:
    return QueryExecution(
        execution_id=f"e-{qid}-{dur}",
        engine="spark",
        query_id=qid,
        duration_ms=dur,
        status=ExecutionStatus.COMPLETED,
    )


def _latency_obj(target: float, scope: str = "out") -> ServiceObjective:
    return ServiceObjective(metric=ObjectiveMetric.LATENCY, target=target, unit="ms", scope=scope)


def _freshness_obj(target: float, scope: str = "out") -> ServiceObjective:
    return ServiceObjective(metric=ObjectiveMetric.FRESHNESS, target=target, unit="s", scope=scope)


class TestPathDiscovery:
    def test_linear_path(self) -> None:
        paths = critical_paths(_linear_graph())
        assert len(paths) == 1
        p = paths[0]
        assert p.entities == (
            "dataset:aws:src",
            "compute_job:aws:etl",
            "dataset:aws:out",
        )
        assert p.coverage() == "0 known / 3 unknown"

    def test_segment_latency_from_executions(self) -> None:
        exs = [_ex("etl", 100.0), _ex("etl", 120.0), _ex("etl", 140.0)]
        paths = critical_paths(_linear_graph(), exs)
        p = paths[0]
        seg = next(s for s in p.latency_segments if s.entity.endswith("etl"))
        assert seg.latency_ms == 120.0  # median
        assert p.total_latency == 120.0
        assert p.bottleneck == "compute_job:aws:etl"
        assert p.coverage() == "1 known / 2 unknown"
        assert set(p.unknown_segments) == {"dataset:aws:src", "dataset:aws:out"}

    def test_no_graph_no_paths(self) -> None:
        assert critical_paths(None) == []

    def test_no_arbitrary_paths(self) -> None:
        """DEFINES is not a data-flow edge — it must not form paths."""
        g = DataPlatformGraph()
        g.add_entity(_ent("tf", kind=EntityKind.INFRASTRUCTURE_RESOURCE))
        g.add_entity(_ent("etl"))
        g.add_relationship(
            _edge(
                "infrastructure_resource:aws:tf",
                "compute_job:aws:etl",
                RelKind.DEFINES,
            )
        )
        assert critical_paths(g) == []

    def test_consumes_edge_recovers_flow_direction(self) -> None:
        """consumer --CONSUMES--> stream is stored consumer-first; the
        data-flow path must read kafka -> job -> table."""
        g = DataPlatformGraph()
        g.add_entity(_ent("orders", kind=EntityKind.STREAM, domain="kafka"))
        g.add_entity(_ent("ss", kind=EntityKind.STREAM, domain="spark_ss"))
        g.add_entity(_ent("t", kind=EntityKind.TABLE))
        g.add_relationship(_edge("stream:spark_ss:ss", "stream:kafka:orders", RelKind.CONSUMES))
        g.add_relationship(_edge("stream:spark_ss:ss", "table:aws:t", RelKind.PRODUCES))
        paths = critical_paths(g)
        assert len(paths) == 1
        assert paths[0].entities == (
            "stream:kafka:orders",
            "stream:spark_ss:ss",
            "table:aws:t",
        )

    def test_cycles_terminated(self) -> None:
        g = DataPlatformGraph()
        g.add_entity(_ent("a"))
        g.add_entity(_ent("b"))
        g.add_relationship(_edge("compute_job:aws:a", "compute_job:aws:b"))
        g.add_relationship(_edge("compute_job:aws:b", "compute_job:aws:a"))
        paths = critical_paths(g)
        assert all(len(p.entities) <= 2 for p in paths)


class TestBudgets:
    def test_budget_within(self) -> None:
        exs = [_ex("etl", 100.0)]
        paths = critical_paths(_linear_graph(), exs)
        budgets = slo_budgets([_latency_obj(1000.0)], paths)
        assert len(budgets) == 1
        b = budgets[0]
        assert b.consumed == 100.0 and b.remaining == 900.0
        assert not b.exhausted

    def test_budget_exhausted_lists_consumers(self) -> None:
        exs = [_ex("etl", 900.0)]
        paths = critical_paths(_linear_graph(), exs)
        budgets = slo_budgets([_latency_obj(500.0)], paths)
        b = budgets[0]
        assert b.exhausted
        assert "compute_job:aws:etl" in b.violating_segments
        assert b.to_dict()["coverage"] == "1 known / 2 unknown"

    def test_freshness_uses_freshness_segments(self) -> None:
        g = _linear_graph()
        g.add_entity(_ent("served", kind=EntityKind.DATASET, freshness_s="60"))
        g.add_relationship(_edge("dataset:aws:out", "dataset:aws:served", RelKind.READS_FROM))
        paths = critical_paths(g)
        budgets = slo_budgets([_freshness_obj(30.0)], paths)
        assert budgets and budgets[0].consumed is not None
        assert budgets[0].consumed >= 60.0

    def test_scope_filters_paths(self) -> None:
        paths = critical_paths(_linear_graph())
        budgets = slo_budgets([_latency_obj(100.0, scope="nomatch")], paths)
        assert budgets == []


class TestFindings:
    def test_slo002_on_exhaustion(self) -> None:
        exs = [_ex("etl", 900.0)]
        paths = critical_paths(_linear_graph(), exs)
        budgets = slo_budgets([_latency_obj(50.0)], paths)
        findings = slo_findings(paths, budgets, _linear_graph())
        ids = {f.check_id for f in findings}
        assert "SLO002" in ids

    def test_slo003_unknown_segment(self) -> None:
        paths = critical_paths(_linear_graph())
        findings = slo_findings(paths, [], _linear_graph())
        assert any(f.check_id == "SLO003" for f in findings)

    def test_slo004_rpo_without_failover(self) -> None:
        g = DataPlatformGraph()
        g.add_entity(_ent("src", kind=EntityKind.DATASET))
        g.add_entity(_ent("etl", rpo="300"))
        g.add_entity(_ent("out", kind=EntityKind.DATASET))
        g.add_relationship(_edge("dataset:aws:src", "compute_job:aws:etl", RelKind.TRIGGERS))
        g.add_relationship(_edge("compute_job:aws:etl", "dataset:aws:out", RelKind.WRITES))
        paths = critical_paths(g)
        findings = slo_findings(paths, [], g)
        assert any(f.check_id == SLOCheckId.SLO004.value for f in findings)

    def test_slo006_scoped_no_failover(self) -> None:
        paths = critical_paths(_linear_graph())
        budgets = slo_budgets([_latency_obj(9999.0, scope="etl")], paths)
        findings = slo_findings(paths, budgets, _linear_graph())
        assert any(f.check_id == "SLO006" for f in findings)

    def test_failover_suppresses_004(self) -> None:
        g = DataPlatformGraph()
        g.add_entity(_ent("src", kind=EntityKind.DATASET))
        g.add_entity(_ent("etl", rpo="300", replicas="2"))
        g.add_entity(_ent("out", kind=EntityKind.DATASET))
        g.add_relationship(_edge("dataset:aws:src", "compute_job:aws:etl", RelKind.TRIGGERS))
        g.add_relationship(_edge("compute_job:aws:etl", "dataset:aws:out", RelKind.WRITES))
        findings = slo_findings(critical_paths(g), [], g)
        assert not any(f.check_id == "SLO004" for f in findings)

    def test_no_objectives_no_budget_findings(self) -> None:
        findings = slo_findings(critical_paths(_linear_graph()), [], _linear_graph())
        assert all(f.check_id == "SLO003" for f in findings)
