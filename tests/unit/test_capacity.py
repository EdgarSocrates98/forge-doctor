"""Spec 246 — capacity/saturation signals, trends, CAP001-007."""

from __future__ import annotations

from forge_doctor.core.capacity import (
    CAP006,
    CAP007,
    CapacityDimension,
    SaturationClass,
    ThresholdProvenance,
    capacity_findings,
    capacity_signals,
    capacity_trends,
    threshold_for,
)
from forge_doctor.core.execution_history import SubjectKind, build_series
from forge_doctor.core.execution_model import (
    ExecutionStatus,
    QueryExecution,
)

HOUR = 3_600_000.0
BASE = 1_700_000_000_000.0


def _ex(
    fp: str, ts: float, queue: float | None = None, mem: float | None = None, engine: str = "spark"
) -> QueryExecution:
    return QueryExecution(
        execution_id=f"e-{ts}",
        engine=engine,
        query_fingerprint=fp,
        start_time=ts,
        queue_time_ms=queue,
        memory_peak=mem,
        status=ExecutionStatus.COMPLETED,
    )


def _series(fp: str, queues: list[float], mems: list[float] | None = None, engine: str = "spark"):
    mems = mems or [None] * len(queues)
    exs = [
        _ex(fp, BASE + i * HOUR, q, m, engine)
        for i, (q, m) in enumerate(zip(queues, mems, strict=True))
    ]
    return build_series(exs, SubjectKind.FINGERPRINT)


class TestThresholdProvenance:
    def test_config_beats_pack_and_baseline(self) -> None:
        th = threshold_for(
            CapacityDimension.QUEUE,
            {"saturation_elevated": "0.5", "saturation_saturated": "0.7"},
            "snowflake",
            [1.0] * 10,
        )
        assert th is not None
        assert th.provenance is ThresholdProvenance.CONFIG
        assert th.elevated == 0.5

    def test_pack_threshold(self) -> None:
        th = threshold_for(CapacityDimension.QUEUE, {}, "snowflake", [1.0])
        assert th is not None
        assert th.provenance is ThresholdProvenance.PACK
        assert "capacity/snowflake" in th.source

    def test_baseline_threshold(self) -> None:
        vals = [80.0, 90.0, 100.0, 100.0, 110.0, 120.0, 130.0, 200.0, 300.0, 400.0]
        th = threshold_for(CapacityDimension.QUEUE, {}, "spark", vals)
        assert th is not None
        assert th.provenance is ThresholdProvenance.BASELINE

    def test_no_source_unknown(self) -> None:
        th = threshold_for(CapacityDimension.QUEUE, {}, "spark", [1.0, 2.0])
        assert th is None


class TestSignals:
    def test_configured_capacity_saturation(self) -> None:
        series = _series("fp", [100.0] * 5 + [95.0], engine="snowflake")
        attrs = {"fp": {"max_queue_ms": "100"}}
        sigs = capacity_signals(series, attrs)
        q = next(s for s in sigs if s.dimension is CapacityDimension.QUEUE)
        assert q.observed_usage == 95.0
        assert q.configured_capacity == 100.0
        assert q.headroom == 5.0
        assert q.saturation is SaturationClass.SATURATED  # 0.95 >= pack/baseline sat

    def test_unknown_without_threshold(self) -> None:
        # spark has no pack; <8 samples -> no baseline either
        series = _series("fp", [50.0] * 4)
        sigs = capacity_signals(series)
        q = next(s for s in sigs if s.dimension is CapacityDimension.QUEUE)
        assert q.saturation is SaturationClass.UNKNOWN
        assert q.configured_capacity is None

    def test_healthy_within_baseline(self) -> None:
        series = _series("fp", [100.0] * 12)
        sigs = capacity_signals(series, {"fp": {}})
        q = next(s for s in sigs if s.dimension is CapacityDimension.QUEUE)
        # flat series: p95 == median -> no baseline threshold -> UNKNOWN (honest)
        assert q.saturation is SaturationClass.UNKNOWN

    def test_saturated_vs_configured(self) -> None:
        series = _series("fp", [10.0] * 4 + [9.6])
        attrs = {
            "fp": {
                "max_queue_ms": "10",
                "saturation_elevated": "0.8",
                "saturation_saturated": "0.95",
            }
        }
        sigs = capacity_signals(series, attrs)
        q = next(s for s in sigs if s.dimension is CapacityDimension.QUEUE)
        assert q.saturation is SaturationClass.SATURATED

    def test_memory_dimension(self) -> None:
        series = _series("fp", [None] * 6, [200.0] * 6)
        sigs = capacity_signals(series, {"fp": {"memory_limit_mb": "1000"}})
        m = next(s for s in sigs if s.dimension is CapacityDimension.MEMORY)
        assert m.observed_usage == 200.0
        assert m.headroom == 800.0


class TestTrends:
    def test_rising_with_projection(self) -> None:
        series = _series("fp", [30.0] * 4 + [60.0] * 4 + [90.0] * 4)
        sigs = capacity_signals(series, {"fp": {"max_queue_ms": "200"}})
        trends = capacity_trends(sigs, series)
        q = next(t for t in trends if t.dimension is CapacityDimension.QUEUE)
        assert q.direction == "rising"
        assert q.headroom == 110.0
        assert q.projection_label == "simple_projection"
        assert q.projected_saturation_at is not None

    def test_flat_no_projection(self) -> None:
        series = _series("fp", [50.0] * 9)
        sigs = capacity_signals(series, {"fp": {"max_queue_ms": "200"}})
        trends = capacity_trends(sigs, series)
        q = next(t for t in trends if t.dimension is CapacityDimension.QUEUE)
        assert q.direction == "flat"
        assert q.projected_saturation_at is None

    def test_insufficient_data(self) -> None:
        series = _series("fp", [50.0, 60.0])
        sigs = capacity_signals(series)
        trends = capacity_trends(sigs, series)
        assert all(t.direction == "insufficient_data" for t in trends)


class TestFindings:
    def test_cap001_queue_saturation(self) -> None:
        series = _series("fp", [10.0] * 4 + [9.7])
        attrs = {
            "fp": {
                "max_queue_ms": "10",
                "saturation_elevated": "0.8",
                "saturation_saturated": "0.95",
            }
        }
        sigs = capacity_signals(series, attrs)
        findings = capacity_findings(sigs, [])
        assert any(f.check_id == "CAP001" and f.severity == "warning" for f in findings)

    def test_cap006_rising_trend(self) -> None:
        series = _series("fp", [30.0] * 4 + [60.0] * 4 + [90.0] * 4)
        sigs = capacity_signals(series, {"fp": {"max_queue_ms": "500"}})
        trends = capacity_trends(sigs, series)
        findings = capacity_findings(sigs, trends)
        cap6 = [f for f in findings if f.check_id == CAP006]
        assert cap6
        assert "rising" in cap6[0].message
        assert "simple projection" in cap6[0].message

    def test_cap007_low_headroom_critical(self) -> None:
        series = _series("fp", [10.0] * 4 + [9.9])
        attrs = {"fp": {"max_queue_ms": "10"}}
        sigs = capacity_signals(series, attrs)
        res = "fingerprint:fp"
        findings = capacity_findings(sigs, [], critical_resources={res})
        assert any(f.check_id == CAP007 and f.severity == "warning" for f in findings)

    def test_unknown_dimension_info(self) -> None:
        series = _series("fp", [50.0] * 4)
        findings = capacity_findings(capacity_signals(series), [])
        assert any(f.check_id == "CAP001" and "unverifiable" in f.message for f in findings)

    def test_no_findings_when_healthy(self) -> None:
        series = _series("fp", [10.0] * 12)
        attrs = {
            "fp": {
                "max_queue_ms": "100",
                "saturation_elevated": "0.8",
                "saturation_saturated": "0.95",
            }
        }
        sigs = capacity_signals(series, attrs)
        findings = capacity_findings(sigs, [])
        assert not any(f.check_id == "CAP001" for f in findings)
