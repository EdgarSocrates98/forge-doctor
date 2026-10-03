"""Spec 244 — incident episodes, candidate causes, propagation."""

from __future__ import annotations

from forge_doctor.core.change_correlation import (
    ChangeClass,
    ChangeEvent,
)
from forge_doctor.core.diagnosis import PromotionLevel
from forge_doctor.core.execution_history import SubjectKind, build_series
from forge_doctor.core.execution_model import (
    ExecutionStatus,
    QueryExecution,
)
from forge_doctor.core.incident import (
    CauseCategory,
    build_incidents,
    structural_causes,
)
from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)

HOUR = 3_600_000.0
BASE = 1_700_000_000_000.0


def _ex(fp: str, dur: float, ts: float, eid: str = "") -> QueryExecution:
    return QueryExecution(
        execution_id=eid or f"e-{ts}",
        engine="spark",
        query_fingerprint=fp,
        start_time=ts,
        duration_ms=dur,
        status=ExecutionStatus.COMPLETED,
    )


def _regressed(fp: str, base: float = BASE):
    durs = [100.0] * 8 + [400.0] * 4
    exs = [_ex(fp, d, base + i * HOUR) for i, d in enumerate(durs)]
    return build_series(exs, SubjectKind.FINGERPRINT)


def _change(entities=("abc",), ts=BASE + 7 * HOUR, cid="c1"):
    return ChangeEvent(
        id=cid,
        timestamp=ts,
        source="deployment_export",
        changed_entities=entities,
        changed_properties=("num_partitions",),
        change_class=ChangeClass.PARTITION_CHANGE,
    )


def _graph_with_link() -> DataPlatformGraph:
    g = DataPlatformGraph()
    g.add_entity(Entity(kind=EntityKind.COMPUTE_JOB, domain="aws", identifier="abc"))
    g.add_entity(Entity(kind=EntityKind.DATASET, domain="aws", identifier="out"))
    g.add_relationship(
        Relationship(src="compute_job:aws:abc", dst="dataset:aws:out", kind=RelKind.WRITES)
    )
    return g


class TestIncidentGrouping:
    def test_single_episode_window(self) -> None:
        incidents = build_incidents(_regressed("abc"))
        assert len(incidents) == 1
        assert incidents[0].symptoms == ("abc.duration",)
        assert incidents[0].affected_entities == ("abc",)

    def test_co_occurring_regressions_merge(self) -> None:
        series = _regressed("a") | _regressed("b")
        incidents = build_incidents(series)
        assert len(incidents) == 1
        assert set(incidents[0].symptoms) == {"a.duration", "b.duration"}

    def test_distant_regressions_split(self) -> None:
        series = _regressed("a") | _regressed("b", base=BASE + 200 * HOUR)
        incidents = build_incidents(series)
        assert len(incidents) == 2

    def test_no_timestamps_standalone(self) -> None:
        exs = [
            _ex("abc", d, ts, eid=f"e{i}")
            for i, (d, ts) in enumerate([(100.0, 0.0)] * 8 + [(400.0, 0.0)] * 4)
        ]
        # timestamps all identical → no temporal ordering evidence
        for e in exs:
            object.__setattr__(e, "start_time", None)
        series = build_series(exs, SubjectKind.FINGERPRINT)
        incidents = build_incidents(series)
        assert len(incidents) == 1
        assert any("unverifiable" in u for u in incidents[0].unknowns)

    def test_no_regressions_no_incidents(self) -> None:
        durs = [100.0] * 12
        exs = [_ex("x", d, BASE + i * HOUR) for i, d in enumerate(durs)]
        assert build_incidents(build_series(exs, SubjectKind.FINGERPRINT)) == []


class TestCandidateCauses:
    def test_full_chain_confirmed(self) -> None:
        series = _regressed("abc")
        g = _graph_with_link()
        incidents = build_incidents(series, [_change()], g)
        causes = incidents[0].candidate_causes
        assert causes
        top = causes[0]
        assert top.category is CauseCategory.STORAGE_LAYOUT
        assert top.confidence is PromotionLevel.CONFIRMED
        assert top.confirmed_links == 5
        assert "persistent regression breach" in top.evidence_path

    def test_no_change_no_candidate(self) -> None:
        incidents = build_incidents(_regressed("abc"))
        inc = incidents[0]
        assert inc.candidate_causes == ()
        assert "cause space open" in inc.unknowns[-1]

    def test_weak_chain_supported(self) -> None:
        """Entity overlap + metric only -> possible, with limitations."""
        series = _regressed("abc")
        ch = _change(ts=None)  # no temporal leg
        incidents = build_incidents(series, [ch])
        top = incidents[0].candidate_causes[0]
        assert top.confidence in (
            PromotionLevel.POSSIBLE,
            PromotionLevel.STRONGLY_SUPPORTED,
        )
        assert top.temporal_match is False
        assert top.limitations  # always states what's missing

    def test_evidence_path_text(self) -> None:
        incidents = build_incidents(_regressed("abc"), [_change()], _graph_with_link())
        c = incidents[0].candidate_causes[0]
        assert f"{c.confirmed_links}/{c.expected_links}" in c.to_dict()["evidence_path"]


class TestPropagation:
    def test_directed_path_reported(self) -> None:
        series = _regressed("out")
        g = DataPlatformGraph()
        g.add_entity(Entity(kind=EntityKind.COMPUTE_JOB, domain="aws", identifier="src"))
        g.add_entity(Entity(kind=EntityKind.DATASET, domain="aws", identifier="out"))
        g.add_relationship(
            Relationship(src="compute_job:aws:src", dst="dataset:aws:out", kind=RelKind.WRITES)
        )
        ch = _change(entities=("src",), ts=BASE + 7 * HOUR)
        incidents = build_incidents(series, [ch], g)
        prop = incidents[0].propagations[0]
        assert prop.path_found
        assert prop.intermediate == ("compute_job:aws:src --WRITES--> dataset:aws:out",)

    def test_no_path_honest(self) -> None:
        series = _regressed("abc")
        g = DataPlatformGraph()  # empty graph, no edges
        ch = _change()
        incidents = build_incidents(series, [ch], g)
        prop = incidents[0].propagations[0]
        assert prop.path_found is False
        assert prop.intermediate == ()


class TestStructuralCauses:
    def test_capability_gap_is_possible(self) -> None:
        causes = structural_causes({}, {"spark/iceberg_write": "unsupported"})
        assert len(causes) == 1
        assert causes[0].confidence is PromotionLevel.POSSIBLE
        assert causes[0].category is CauseCategory.UNKNOWN
        assert causes[0].limitations

    def test_drift_maps_to_category(self) -> None:
        from forge_doctor.core.twin_states import (
            DriftType,
            TwinReconciliation,
            TwinState,
        )

        rec = TwinReconciliation(
            entity="t1",
            property="schema",
            expected="int",
            actual="string",
            states_compared=(TwinState.DECLARED, TwinState.OBSERVED),
            difference="int != string",
            drift_type=DriftType.SCHEMA_DRIFT,
            confidence="observed",
            affected_entities=("t1",),
        )
        causes = structural_causes((rec,), {})
        assert causes[0].category is CauseCategory.SCHEMA


class TestDownstream:
    def test_downstream_effects_listed(self) -> None:
        incidents = build_incidents(_regressed("abc"), [_change()], _graph_with_link())
        assert "dataset:aws:out" in incidents[0].downstream_effects

    def test_owners_from_domain(self) -> None:
        incidents = build_incidents(_regressed("abc"), [_change()], _graph_with_link())
        assert "aws" in incidents[0].owners
