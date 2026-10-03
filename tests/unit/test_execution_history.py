"""Spec 241 — execution history, baselines, snapshot roundtrip."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_doctor_data.core.execution_history import (
    BaselineWindow,
    BaselineWindowKind,
    HistoryRetention,
    SubjectKind,
    TimestampQuality,
    baseline_for,
    build_series,
    iter_samples,
    iter_snapshots,
    observed_fact_series,
    record_executions,
    sample_from,
)
from forge_doctor_data.core.execution_model import (
    ExecutionStatus,
    QueryExecution,
)
from forge_doctor_data.core.trends import (
    MetricBaseline,
    TrendDirection,
    mad,
    median,
    metric_baseline,
    percentile,
    series_from,
    trend_direction,
)
from forge_doctor_data.core.twin_states import TwinFact, TwinState


def _ex(
    fp: str = "abc",
    dur: float | None = 100.0,
    ts: float | None = 0.0,
    **kw,
) -> QueryExecution:
    return QueryExecution(
        execution_id=kw.pop("eid", f"e-{fp}-{ts}"),
        engine=kw.pop("engine", "spark"),
        query_fingerprint=fp,
        start_time=kw.pop("start_time", ts),
        duration_ms=dur,
        status=kw.pop("status", ExecutionStatus.COMPLETED),
        **kw,
    )


# --- series construction ---------------------------------------------------


def test_empty_history() -> None:
    assert build_series([]) == {}
    assert list(iter_samples(Path("/nonexistent"))) == []


def test_single_sample() -> None:
    s = build_series([_ex(ts=1000)])
    series = s["fingerprint:abc"]
    assert series.sample_count == 1
    assert series.first_seen == series.last_seen == 1000


def test_multiple_samples_deterministic_order() -> None:
    exs = [_ex(ts=t, eid=f"e{t}") for t in (3000, 1000, 2000)]
    series = build_series(exs)["fingerprint:abc"]
    assert [s.timestamp for s in series.samples] == [1000.0, 2000.0, 3000.0]


def test_missing_timestamp_sorts_last() -> None:
    exs = [_ex(ts=0, eid="z"), _ex(eid="a", start_time=None)]
    series = build_series(exs)["fingerprint:abc"]
    assert series.samples[-1].execution_id == "a"
    assert series.first_seen == 0.0


def test_subjects_grouped_by_kind() -> None:
    exs = [_ex(fp="a", eid="1", engine="spark"), _ex(fp="a", eid="2", engine="trino")]
    by_engine = build_series(exs, SubjectKind.ENGINE)
    assert set(by_engine) == {"engine:spark", "engine:trino"}


# --- robust stats ----------------------------------------------------------


def test_deterministic_percentiles() -> None:
    xs = [float(x) for x in range(1, 101)]
    assert percentile(xs, 0.5) == 50.0
    assert percentile(xs, 0.95) == 95.0
    assert percentile(xs, 0.99) == 99.0
    assert percentile([], 0.5) is None


def test_median_and_mad() -> None:
    assert median([1, 2, 3, 4]) == 2.5
    assert median([5]) == 5
    assert mad([10, 10, 10, 100]) == 0.0  # median 10, deviations 0,0,0,90 -> 0
    assert mad([]) is None


def test_outlier_resistant_baseline() -> None:
    exs = [_ex(ts=t, dur=100.0, eid=f"e{t}") for t in range(1, 10)]
    exs.append(_ex(ts=10, dur=9000.0, eid="e10"))  # one outlier
    series = build_series(exs)["fingerprint:abc"]
    b = baseline_for(series)
    dur = b.metrics["duration_ms"]
    assert dur.median == 100.0
    assert dur.maximum == 9000.0
    # MAD-based spread stays honest: outlier does not dominate median
    assert dur.mad == 0.0


def test_stable_series_trend() -> None:
    series = series_from("s", "duration_ms", [(t, 100.0) for t in range(8)])
    assert trend_direction(series) is TrendDirection.STABLE


def test_slow_trend_detected() -> None:
    vals = [100.0] * 4 + [150.0] * 4
    series = series_from("s", "duration_ms", [(t, v) for t, v in enumerate(vals)])
    assert trend_direction(series) is TrendDirection.RISING


def test_fast_regression_trend() -> None:
    vals = [100.0] * 4 + [500.0] * 4
    series = series_from("s", "duration_ms", [(t, v) for t, v in enumerate(vals)])
    assert trend_direction(series) is TrendDirection.RISING


def test_insufficient_samples_no_trend() -> None:
    series = series_from("s", "duration_ms", [(0, 100.0), (1, 400.0)])
    assert trend_direction(series) is TrendDirection.INSUFFICIENT_DATA


def test_missing_metrics_stay_none() -> None:
    ex = QueryExecution(execution_id="e", engine="spark")
    s = sample_from(ex)
    assert s.duration_ms is None and s.bytes_read is None
    series = build_series([ex], SubjectKind.ENGINE)["engine:spark"]
    b = baseline_for(series)
    # no duration evidence -> metric absent from baseline entirely
    assert "duration_ms" not in b.metrics


def test_baseline_confidence_ladder() -> None:
    few = build_series([_ex(ts=t, eid=str(t)) for t in range(2)])
    many = build_series([_ex(ts=t, eid=str(t)) for t in range(12)])
    assert baseline_for(few["fingerprint:abc"]).confidence == "unknown"
    assert baseline_for(many["fingerprint:abc"]).confidence == "high"


def test_window_last_n() -> None:
    exs = [_ex(ts=t, dur=100.0 if t < 6 else 400.0, eid=str(t)) for t in range(10)]
    series = build_series(exs)["fingerprint:abc"]
    b = baseline_for(series, BaselineWindow(BaselineWindowKind.LAST_N, n=4))
    assert b.metrics["duration_ms"].median == 400.0
    assert b.sample_count == 4


def test_window_last_days() -> None:
    day_ms = 86_400_000
    exs = [_ex(ts=100 * day_ms + t, dur=50.0, eid=f"a{t}") for t in range(3)] + [
        _ex(ts=1 * day_ms + t, dur=900.0, eid=f"b{t}") for t in range(3)
    ]
    series = build_series(exs)["fingerprint:abc"]
    b = baseline_for(series, BaselineWindow(BaselineWindowKind.LAST_DAYS, n=10))
    assert b.metrics["duration_ms"].median == 50.0


# --- snapshot storage ------------------------------------------------------


def test_snapshot_roundtrip(tmp_path: Path) -> None:
    exs = [_ex(ts=t * 1000, dur=100 + t, eid=f"e{t}") for t in range(5)]
    path = record_executions(tmp_path, exs)
    assert path.suffix == ".jsonl"
    samples = list(iter_samples(tmp_path))
    assert len(samples) == 5
    assert samples[0].fingerprint == "abc"
    # no SQL text anywhere in the file
    assert "select" not in path.read_text().lower()


def test_experiment_history_isolated(tmp_path: Path) -> None:
    record_executions(tmp_path, [_ex(ts=1, eid="p1")], kind="production")
    record_executions(tmp_path, [_ex(ts=2, eid="x1")], kind="experiment")
    prod = list(iter_samples(tmp_path, kind="production"))
    exp = list(iter_samples(tmp_path, kind="experiment"))
    assert [s.execution_id for s in prod] == ["p1"]
    assert [s.execution_id for s in exp] == ["x1"]
    assert [p.name for p in iter_snapshots(tmp_path, "experiment")] == [
        p.name for p in iter_snapshots(tmp_path) if p.name.startswith("exp-")
    ]


def test_bad_snapshot_rejected(tmp_path: Path) -> None:
    bad = tmp_path / ".forge-doctor-data" / "execution-history"
    bad.mkdir(parents=True)
    (bad / "x.jsonl").write_text('{"header": {"format": "other"}}\n')
    from forge_doctor_data.core.execution_history import read_snapshot

    with pytest.raises(ValueError):
        list(read_snapshot(bad / "x.jsonl"))


def test_retention_config_parse() -> None:
    r = HistoryRetention.from_dict({"keep_days": 10, "keep_samples": 5})
    assert r.keep_days == 10 and r.keep_samples == 5
    assert HistoryRetention.from_dict(None).keep_days == 30


# --- twin integration ------------------------------------------------------


def test_observed_facts_get_series() -> None:
    facts = [
        TwinFact(entity="abc", property="p95", value="100", state=TwinState.OBSERVED),
        TwinFact(entity="abc", property="decl", value="x", state=TwinState.DECLARED),
    ]
    out = observed_fact_series(facts, [_ex(ts=1), _ex(ts=2)])
    assert "abc.p95" in out and "abc.decl" not in out


def test_timestamp_quality_propagates() -> None:
    s = sample_from(_ex(ts=1000))
    assert s.timestamp_quality is TimestampQuality.EPOCH_ASSUMED_UTC
    s2 = sample_from(_ex(start_time=None))
    assert s2.timestamp_quality is TimestampQuality.UNKNOWN


def test_metric_baseline_fields() -> None:
    mb = metric_baseline(series_from("s", "m", [(t, float(t)) for t in range(1, 21)]))
    assert isinstance(mb, MetricBaseline)
    assert mb.p50 == 10.0 and mb.maximum == 20.0 and mb.samples == 20
