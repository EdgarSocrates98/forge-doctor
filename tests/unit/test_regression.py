"""Spec 242 — regression detection, episodes, persistence, PERFREG."""

from __future__ import annotations

from forge_doctor.core.execution_history import (
    SubjectKind,
    build_series,
)
from forge_doctor.core.execution_model import (
    ExecutionStatus,
    QueryExecution,
)
from forge_doctor.core.performance import RegressionClass
from forge_doctor.core.regression import (
    PersistenceClass,
    RegressionConfidence,
    RegressionDimension,
    RegressionPolicy,
    classify_persistence,
    detect_dimension,
    detect_regressions,
    episodes,
    perfreg_findings,
)


def _ex(fp: str = "abc", dur: float = 100.0, ts: float = 0.0, **kw) -> QueryExecution:
    return QueryExecution(
        execution_id=kw.pop("eid", f"e-{fp}-{ts}"),
        engine=kw.pop("engine", "spark"),
        query_fingerprint=fp,
        start_time=ts,
        duration_ms=dur,
        status=kw.pop("status", ExecutionStatus.COMPLETED),
        **kw,
    )


def _series(durs: list[float], fp: str = "abc"):
    exs = [_ex(fp=fp, dur=d, ts=t, eid=f"e{t}") for t, d in enumerate(durs)]
    return build_series(exs, SubjectKind.FINGERPRINT)[f"fingerprint:{fp}"]


POL = RegressionPolicy()


def test_stable_series_no_regression() -> None:
    s = _series([100.0] * 8)
    sig = detect_dimension(s, RegressionDimension.DURATION, POL)
    assert sig.klass is RegressionClass.STABLE


def test_regression_p95_breach() -> None:
    s = _series([100.0] * 8 + [300.0] * 4)
    sig = detect_dimension(s, RegressionDimension.DURATION, POL)
    assert sig.klass is RegressionClass.REGRESSED
    assert sig.current and sig.baseline_value
    assert "baseline" in sig.basis


def test_improvement_detected() -> None:
    s = _series([100.0] * 8 + [40.0] * 4)
    sig = detect_dimension(s, RegressionDimension.DURATION, POL)
    assert sig.klass is RegressionClass.IMPROVED


def test_insufficient_baseline() -> None:
    s = _series([100.0, 500.0])
    sig = detect_dimension(s, RegressionDimension.DURATION, POL)
    assert sig.klass is RegressionClass.INSUFFICIENT_DATA


def test_volatile_series() -> None:
    # continuous spread: median=300, MAD=200 -> ratio 0.67 > 0.5
    s = _series([100.0, 200.0, 300.0, 600.0, 900.0, 150.0, 700.0, 400.0])
    sig = detect_dimension(s, RegressionDimension.DURATION, POL)
    assert sig.klass is RegressionClass.VOLATILE


def test_missing_dimension_skipped() -> None:
    # bytes_read never set -> scan regression can't be evaluated
    s = _series([100.0] * 8)
    sig = detect_dimension(s, RegressionDimension.SCAN, POL)
    assert sig.klass is RegressionClass.INSUFFICIENT_DATA


# --- persistence -----------------------------------------------------------


def test_one_off_is_candidate() -> None:
    flags = [False] * 7 + [True]
    assert classify_persistence(flags) is PersistenceClass.ONE_OFF


def test_persistent_tail() -> None:
    flags = [False] * 5 + [True, True, True]
    assert classify_persistence(flags) is PersistenceClass.PERSISTENT


def test_recovered() -> None:
    flags = [True, True] + [False] * 6
    assert classify_persistence(flags) is PersistenceClass.RECOVERED


def test_flapping() -> None:
    flags = [True, False, True, False, True, False]
    assert classify_persistence(flags) is PersistenceClass.FLAPPING


def test_burst_open() -> None:
    flags = [False] * 5 + [True, True]
    assert classify_persistence(flags) is PersistenceClass.BURST


def test_no_breach_no_episode() -> None:
    s = _series([100.0] * 8)
    assert episodes(s, RegressionDimension.DURATION, POL) == []


def test_episode_persistent_vs_oneoff() -> None:
    s = _series([100.0] * 5 + [400.0] * 4)
    eps = episodes(s, RegressionDimension.DURATION, POL)
    assert eps and eps[0].persistence is PersistenceClass.PERSISTENT
    s2 = _series([100.0] * 5 + [400.0])
    eps2 = episodes(s2, RegressionDimension.DURATION, POL)
    assert eps2 and eps2[0].persistence is PersistenceClass.ONE_OFF


# --- findings --------------------------------------------------------------


def test_perfreg_warning_only_persistent() -> None:
    s = _series([100.0] * 5 + [400.0] * 4)
    findings = perfreg_findings({"fingerprint:abc": s}, POL)
    perfreg = [f for f in findings if f.check_id == "PERFREG001"]
    assert perfreg and perfreg[0].severity.value == "warning"


def test_perfreg_info_for_oneoff() -> None:
    s = _series([100.0] * 5 + [400.0])
    findings = perfreg_findings({"fingerprint:abc": s}, POL)
    perfreg = [f for f in findings if f.check_id == "PERFREG001"]
    assert perfreg and perfreg[0].severity.value == "info"
    assert "one_off" in perfreg[0].message


def test_detect_regressions_covers_dimensions() -> None:
    s = _series([100.0] * 8)
    out = detect_regressions({"fingerprint:abc": s}, POL)
    # stable duration signal; other dims lack evidence -> filtered out
    dims = {s.dimension for s in out}
    assert RegressionDimension.DURATION in dims
    assert all(s.klass is not RegressionClass.INSUFFICIENT_DATA for s in out)


def test_confidence_follows_evidence() -> None:
    s = _series([100.0] * 12 + [400.0] * 3)
    sig = detect_dimension(s, RegressionDimension.DURATION, POL)
    assert sig.confidence in (RegressionConfidence.HIGH, RegressionConfidence.MEDIUM)


def test_throughput_lower_is_better() -> None:
    # rows/duration drops -> regression even though duration stable
    exs = []
    for t in range(8):
        exs.append(_ex(dur=100.0, ts=t, eid=f"e{t}", rows_written=1000.0))
    for t in range(8, 11):
        exs.append(_ex(dur=100.0, ts=t, eid=f"e{t}", rows_written=100.0))
    s = build_series(exs, SubjectKind.FINGERPRINT)["fingerprint:abc"]
    sig = detect_dimension(s, RegressionDimension.THROUGHPUT, POL)
    assert sig.klass is RegressionClass.REGRESSED
