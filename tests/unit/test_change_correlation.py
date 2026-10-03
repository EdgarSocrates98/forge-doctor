"""Spec 243 — change → runtime correlation."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor_data.core.change_correlation import (
    ChangeClass,
    ChangeEvent,
    CorrelationConfidence,
    DataShapeSnapshot,
    PlanChangeKind,
    change_events_from_diff,
    change_events_from_json,
    classify_change,
    correlate,
    plan_changes,
    plan_fingerprint,
    shape_delta,
)
from forge_doctor_data.core.execution_history import SubjectKind, build_series
from forge_doctor_data.core.execution_model import (
    ExecutionStage,
    ExecutionStatus,
    QueryExecution,
    StageKind,
)
from forge_doctor_data.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
)
from forge_doctor_data.core.semantic_diff import EntityChange, SemanticDiff

HOUR = 3_600_000.0
BASE = 1_700_000_000_000.0  # fixed epoch ms


def _ex(fp: str, dur: float, ts: float, eid: str = "") -> QueryExecution:
    return QueryExecution(
        execution_id=eid or f"e-{ts}",
        engine="spark",
        query_fingerprint=fp,
        start_time=ts,
        duration_ms=dur,
        status=ExecutionStatus.COMPLETED,
    )


def _regressed_series(fp: str = "abc"):
    """8 baseline runs at ~100ms then a persistent breach at ~400ms."""
    durs = [100.0] * 8 + [400.0] * 4
    ts = [BASE + i * HOUR for i in range(len(durs))]
    exs = [_ex(fp, d, t) for d, t in zip(durs, ts, strict=True)]
    return build_series(exs, SubjectKind.FINGERPRINT)


def _change(
    entities=("abc",),
    props=("shuffle_partitions",),
    klass=ChangeClass.DISTRIBUTION_CHANGE,
    ts=BASE + 7 * HOUR,
    cid="c1",
) -> ChangeEvent:
    return ChangeEvent(
        id=cid,
        timestamp=ts,
        source="deployment_export",
        changed_entities=entities,
        changed_properties=props,
        change_class=klass,
    )


class TestClassify:
    def test_each_class(self) -> None:
        assert classify_change(["glue_version"]) is ChangeClass.RUNTIME_UPGRADE
        assert classify_change(["field.user_id"]) is ChangeClass.SCHEMA_CHANGE
        assert classify_change(["partition_by"]) is ChangeClass.PARTITION_CHANGE
        assert classify_change(["distkey"]) is ChangeClass.DISTRIBUTION_CHANGE
        assert classify_change(["instances"]) is ChangeClass.CAPACITY_CHANGE
        assert classify_change(["schedule_interval"]) is ChangeClass.ORCHESTRATION_CHANGE
        assert classify_change(["materialized"]) is ChangeClass.MATERIALIZATION_CHANGE
        assert classify_change(["kms_key"]) is ChangeClass.SECURITY_CHANGE
        assert classify_change(["query_text"]) is ChangeClass.QUERY_CHANGE
        assert classify_change(["depends_on"]) is ChangeClass.DEPENDENCY_CHANGE

    def test_fallback_config(self) -> None:
        assert classify_change(["owner", "tags"]) is ChangeClass.CONFIG_CHANGE


class TestEventsFromDiff:
    def test_modified_entity_emits_event(self) -> None:
        diff = SemanticDiff(
            changed_files=("jobs/etl.py",),
            changes=(
                EntityChange(
                    entity_id="job:aws:etl",
                    change="modified",
                    via_files=("jobs/etl.py",),
                    attr_diffs=("glue_version",),
                ),
            ),
        )
        events = change_events_from_diff(diff, timestamp=BASE, commit="deadbeef99")
        assert len(events) == 1
        assert events[0].change_class is ChangeClass.RUNTIME_UPGRADE
        assert events[0].evidence == ("file:jobs/etl.py",)

    def test_touched_emits_nothing(self) -> None:
        diff = SemanticDiff(
            changed_files=("jobs/etl.py",),
            changes=(
                EntityChange(
                    entity_id="job:aws:etl",
                    change="touched",
                    via_files=("jobs/etl.py",),
                ),
            ),
        )
        assert change_events_from_diff(diff) == ()

    def test_docs_only_change_no_events(self) -> None:
        """Docs-only commits map to no entity — zero correlation surface."""
        diff = SemanticDiff(
            changed_files=("README.md", "docs/guide.md"),
            changes=(),
            unmapped_files=("README.md", "docs/guide.md"),
        )
        series = _regressed_series()
        assert correlate(change_events_from_diff(diff), series) == []


class TestEventsFromJson:
    def test_valid_rows(self, tmp_path: Path) -> None:
        p = tmp_path / "events.json"
        p.write_text(
            json.dumps(
                [
                    {
                        "id": "d1",
                        "timestamp": BASE,
                        "entities": ["job:aws:etl"],
                        "properties": ["instances"],
                        "class": "capacity_change",
                        "commit": "abc123",
                    },
                    {"id": "d2", "entities": ["job:aws:x"]},
                    {"id": "d3"},  # no entities -> skipped
                ]
            )
        )
        events = change_events_from_json(p)
        assert len(events) == 2
        assert events[0].change_class is ChangeClass.CAPACITY_CHANGE
        assert events[1].change_class is ChangeClass.CONFIG_CHANGE

    def test_malformed_file_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        assert change_events_from_json(p) == ()


class TestCorrelate:
    def test_all_four_legs_high_confidence(self) -> None:
        series = _regressed_series()
        graph = DataPlatformGraph()
        graph.add_entity(Entity(kind=EntityKind.COMPUTE_JOB, domain="aws", identifier="abc"))
        change = _change(ts=BASE + 7 * HOUR)  # breach starts at BASE+8h
        out = correlate([change], series, graph)
        assert len(out) == 1
        c = out[0]
        assert c.confidence is CorrelationConfidence.HIGH
        assert c.temporal_match and c.entity_overlap
        assert c.graph_match and c.metric_relevant
        assert "correlated with" in c.explanation

    def test_no_locality_not_emitted(self) -> None:
        """Temporal + metric relevance alone is not enough — the change
        must reach the subject (entity overlap or graph path)."""
        series = _regressed_series()
        change = _change(entities=("unrelated",), ts=BASE + 7 * HOUR)
        assert correlate([change], series) == []

    def test_locality_plus_metric_is_low(self) -> None:
        series = _regressed_series()
        change = _change(ts=None)  # entity overlap + metric, no temporal
        out = correlate([change], series)
        assert len(out) == 1
        assert out[0].confidence is CorrelationConfidence.LOW
        assert out[0].entity_overlap and out[0].metric_relevant
        assert not out[0].temporal_match

    def test_single_leg_not_emitted(self) -> None:
        series = _regressed_series()
        # entity overlap only — no temporal, no metric relevance, no graph
        change = _change(klass=ChangeClass.SECURITY_CHANGE, ts=None)
        out = correlate([change], series)
        assert out == []

    def test_missing_timestamp_drops_temporal_leg(self) -> None:
        series = _regressed_series()
        change = _change(ts=None)
        out = correlate([change], series)
        # entity overlap + metric relevance = 2 legs -> LOW, no temporal claim
        assert len(out) == 1
        assert out[0].temporal_match is False
        assert out[0].temporal_distance_ms is None

    def test_change_after_regression_not_temporal(self) -> None:
        series = _regressed_series()
        change = _change(ts=BASE + 20 * HOUR)  # after the breach window
        out = correlate([change], series)
        assert all(not c.temporal_match for c in out)

    def test_deterministic_order(self) -> None:
        series = _regressed_series()
        changes = [
            _change(cid="c2"),
            _change(cid="c1"),
        ]
        out = correlate(changes, series)
        assert [c.change.id for c in out] == sorted(c.change.id for c in out)


class TestPlanFingerprint:
    def _ex_with_stages(self, kinds: list[StageKind]) -> QueryExecution:
        return QueryExecution(
            execution_id="x",
            engine="spark",
            stages=tuple(ExecutionStage(id=str(i), kind=k) for i, k in enumerate(kinds)),
        )

    def test_deterministic(self) -> None:
        ex = self._ex_with_stages([StageKind.SCAN, StageKind.EXCHANGE])
        assert plan_fingerprint(ex) == plan_fingerprint(ex)

    def test_exchange_added_detected(self) -> None:
        before = plan_fingerprint(self._ex_with_stages([StageKind.SCAN]))
        after = plan_fingerprint(self._ex_with_stages([StageKind.SCAN, StageKind.EXCHANGE]))
        assert PlanChangeKind.EXCHANGE_ADDED in plan_changes(before, after)

    def test_no_change(self) -> None:
        p = plan_fingerprint(self._ex_with_stages([StageKind.SCAN]))
        assert plan_changes(p, p) == ()


class TestShapeDelta:
    def test_ratios(self) -> None:
        before = DataShapeSnapshot(row_count=100.0, byte_size=1000.0)
        after = DataShapeSnapshot(row_count=250.0, byte_size=500.0)
        delta = shape_delta(before, after)
        assert delta["row_count"] == 2.5
        assert delta["byte_size"] == 0.5

    def test_unmeasured_fields_excluded(self) -> None:
        before = DataShapeSnapshot(row_count=100.0)
        after = DataShapeSnapshot(row_count=200.0, file_count=9.0)
        assert shape_delta(before, after) == {"row_count": 2.0}
