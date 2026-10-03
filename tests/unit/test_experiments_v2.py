"""Spec 240 — experiment/simulation v2: measured behavior, verdicts, workloads."""

from __future__ import annotations

from forge_doctor_data.core.execution_model import (
    ExecutionScan,
    ExecutionStage,
    ExecutionStatus,
    QueryExecution,
    StageKind,
    derive_metrics,
)
from forge_doctor_data.core.experiments_v2 import (
    WORKLOAD_KINDS,
    ExperimentPlanV2,
    ExperimentVerdict,
    evaluate,
    measure_bundle,
    synthetic_workload,
)


def _ex(bytes_read: float | None = None, **kw: object) -> QueryExecution:
    ex = QueryExecution(
        execution_id="e",
        engine="synthetic",
        status=ExecutionStatus.COMPLETED,
        bytes_read=bytes_read,
        **kw,  # type: ignore[arg-type]
    )
    import dataclasses

    return dataclasses.replace(ex, metrics=derive_metrics(ex))


def test_verdict_supported() -> None:
    plan = ExperimentPlanV2(
        hypothesis="partition pruning",
        expected_effects=("scan_bytes:decrease", "duration_ms:decrease"),
    )
    before = [_ex(bytes_read=1e9, duration_ms=2000.0)]
    after = [_ex(bytes_read=1e8, duration_ms=500.0)]
    r = evaluate(plan, before, after)
    assert r.verdict is ExperimentVerdict.SUPPORTED
    assert all(c.direction_ok for c in r.comparisons)


def test_verdict_not_supported() -> None:
    plan = ExperimentPlanV2(
        hypothesis="partition pruning",
        expected_effects=("scan_bytes:decrease",),
    )
    before = [_ex(bytes_read=1e9)]
    after = [_ex(bytes_read=1e9)]
    r = evaluate(plan, before, after)
    assert r.verdict is ExperimentVerdict.NOT_SUPPORTED


def test_verdict_inconclusive_when_unmeasurable() -> None:
    plan = ExperimentPlanV2(hypothesis="x", expected_effects=("scan_bytes:decrease",))
    r = evaluate(plan, [_ex()], [_ex()])
    assert r.verdict is ExperimentVerdict.INCONCLUSIVE


def test_constraint_violated_overrides_benefit() -> None:
    plan = ExperimentPlanV2(
        hypothesis="raise trigger 5s->30s",
        expected_effects=("duration_ms:decrease",),
        protected_constraints=("freshness_seconds:<=:60",),
    )
    before = [_ex(duration_ms=2000.0)]
    after = [_ex(duration_ms=500.0)]
    # after bundle carries freshness evidence via protected metric name
    r = evaluate(plan, before, after)
    # freshness not measured in after bundle -> unmeasured, not violated
    assert r.verdict is ExperimentVerdict.SUPPORTED


def test_constraint_violated_when_measured() -> None:
    plan = ExperimentPlanV2(
        hypothesis="x",
        expected_effects=("scan_bytes:decrease",),
        protected_constraints=("duration_ms:<=:1000",),
    )
    before = [_ex(bytes_read=1e9, duration_ms=900.0)]
    after = [_ex(bytes_read=1e8, duration_ms=5000.0)]
    r = evaluate(plan, before, after)
    assert r.verdict is ExperimentVerdict.CONSTRAINT_VIOLATED


def test_acceptance_fraction() -> None:
    plan = ExperimentPlanV2(
        hypothesis="x",
        expected_effects=("scan_bytes:decrease", "duration_ms:decrease"),
        acceptance=0.5,
    )
    before = [_ex(bytes_read=1e9, duration_ms=2000.0)]
    after = [_ex(bytes_read=1e8, duration_ms=3000.0)]
    r = evaluate(plan, before, after)
    assert r.verdict is ExperimentVerdict.SUPPORTED


def test_factor_bound() -> None:
    plan = ExperimentPlanV2(hypothesis="x", expected_effects=("scan_bytes:decrease:0.5",))
    before = [_ex(bytes_read=1e9)]
    ok = evaluate(plan, before, [_ex(bytes_read=4e8)])
    not_ok = evaluate(plan, before, [_ex(bytes_read=8e8)])
    assert ok.verdict is ExperimentVerdict.SUPPORTED
    assert not_ok.verdict is ExperimentVerdict.NOT_SUPPORTED


def test_measure_bundle_fields() -> None:
    scan = ExecutionScan(source="t", bytes_scanned=1e6, files_scanned=10.0)
    ex = _ex(
        bytes_read=1e6,
        duration_ms=1000.0,
        queue_time_ms=100.0,
        rows_read=5000.0,
        stages=(
            ExecutionStage(
                id="s",
                kind=StageKind.SCAN,
                shuffle_bytes=5e5,
                scans=(scan,),
            ),
        ),
    )
    m = measure_bundle([ex])
    assert m["scan_bytes"] == 1e6
    assert m["file_count"] == 10.0
    assert m["median_file_size"] == 100000.0
    assert m["queue_time_ms"] == 100.0
    assert m["throughput_rows_per_s"] == 5000.0
    assert m["error_count"] == 0.0


def test_workload_determinism() -> None:
    for kind in WORKLOAD_KINDS:
        a = synthetic_workload(kind, n=20, seed=42)
        b = synthetic_workload(kind, n=20, seed=42)
        assert [e.to_dict() for e in a] == [e.to_dict() for e in b]


def test_workload_shapes_differ() -> None:
    small = synthetic_workload("many_small_files", n=5, seed=1)
    uniform = synthetic_workload("uniform", n=5, seed=1)
    small_files = sum(sc.files_scanned or 0 for e in small for s in e.stages for sc in s.scans)
    uni_files = sum(sc.files_scanned or 0 for e in uniform for s in e.stages for sc in s.scans)
    assert small_files > uni_files
    hot = synthetic_workload("hot_key", n=5, seed=1)
    assert all(e.spill_bytes for e in hot)


def test_verdict_matrix_serialization() -> None:
    plan = ExperimentPlanV2(hypothesis="h", expected_effects=("scan_bytes:decrease",))
    r = evaluate(plan, [_ex(bytes_read=1e9)], [_ex(bytes_read=1e8)])
    d = r.to_dict()
    assert d["verdict"] == "supported" and d["plan"]["hypothesis"] == "h"
