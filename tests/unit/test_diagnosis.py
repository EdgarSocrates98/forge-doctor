"""Finding promotion + root-cause clustering (phase 3)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.core.diagnosis import (
    PromotionLevel,
    cluster_findings,
    promote_findings,
)
from forge_doctor_data.core.models import CheckResult, Confidence, Severity
from forge_doctor_data.core.runtime_evidence import (
    ExecutionMetric,
    ExecutionThroughput,
    ExecutionTiming,
    RuntimeEvidenceModel,
    RuntimeExecution,
)


def _finding(
    check_id: str,
    message: str = "msg",
    file: str = "jobs/etl.py",
    severity: Severity = Severity.WARNING,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=f"{check_id} title",
        severity=severity,
        category="test",
        message=message,
        file=Path(file),
    )


def _spark_log(
    *, single_task: bool = False, skew: bool = False, spill: bool = False
) -> RuntimeEvidenceModel:
    m = RuntimeEvidenceModel(source="spark_eventlog")
    m.identifiers["app_name"] = "etl"
    m.executions.append(
        RuntimeExecution(id="stage-1", kind="stage", state="completed", duration_ms=8000)
    )
    m.executions.append(
        RuntimeExecution(id="stage-2", kind="stage", state="completed", duration_ms=61000)
    )
    n = 1 if single_task else 8
    m.metrics.append(ExecutionMetric("task_count", n, "tasks", scope="stage-2"))
    if skew:
        m.metrics.append(ExecutionMetric("task_max_ms", 55000, "ms", scope="stage-2"))
        m.metrics.append(ExecutionMetric("task_median_ms", 4000, "ms", scope="stage-2"))
        m.events.append("stage-2 skew: max task 55000ms vs median 4000ms")
    if spill:
        m.metrics.append(ExecutionMetric("spill_memory_bytes", 9e8, "bytes", scope="stage-2"))
        m.metrics.append(ExecutionMetric("spill_disk_bytes", 4e8, "bytes", scope="stage-2"))
    return m


def _progress(*, backlog: bool = True, batches: int = 5, rows: float = 400) -> RuntimeEvidenceModel:
    m = RuntimeEvidenceModel(source="spark_ss_progress")
    m.identifiers["stream"] = "orders-stream"
    for i in range(batches):
        m.executions.append(
            RuntimeExecution(id=f"batch-{i}", kind="batch", state="completed", duration_ms=1500)
        )
    m.throughput.append(
        ExecutionThroughput(
            name="orders-stream",
            input_rps=120.0,
            output_rps=80.0 if backlog else 200.0,
            input_rows=rows,
            duration_ms=1500,
        )
    )
    return m


def _athena(
    queue_ms: float = 900, planning_ms: float = 600, exec_ms: float = 800
) -> RuntimeEvidenceModel:
    m = RuntimeEvidenceModel(source="athena_stats")
    m.identifiers["query_id"] = "q-123"
    m.timings.extend(
        [
            ExecutionTiming(phase="queue", duration_ms=queue_ms),
            ExecutionTiming(phase="planning", duration_ms=planning_ms),
            ExecutionTiming(phase="execution", duration_ms=exec_ms),
        ]
    )
    return m


# --- promotion -----------------------------------------------------------------


def test_promotion_single_task_confirmed_by_identity() -> None:
    f = _finding("SPARK003", "repartition(1) serializes write in stage-2")
    promos = promote_findings([f], [_spark_log(single_task=True)])
    target = [p for p in promos if p.check_id == "SPARK003"]
    assert len(target) == 1
    p = target[0]
    assert p.level is PromotionLevel.CONFIRMED
    assert p.resulting_confidence is Confidence.HIGH
    assert p.resulting_severity is Severity.ERROR  # WARNING escalated
    assert p.base_fingerprint == f.fingerprint
    assert any("single task" in e for e in p.confirming_evidence)


def test_promotion_without_identity_is_strongly_supported() -> None:
    f = _finding("SPARK003", "repartition(1) serializes write")
    promos = promote_findings([f], [_spark_log(single_task=True)])
    p = next(p for p in promos if p.check_id == "SPARK003")
    assert p.level is PromotionLevel.STRONGLY_SUPPORTED
    assert p.resulting_confidence is Confidence.MEDIUM


def test_promotion_skew_and_spill() -> None:
    f = _finding("SPARK009", "cartesian join risks skew")
    promos = promote_findings([f], [_spark_log(skew=True, spill=True)])
    p = next(p for p in promos if p.check_id == "SPARK009")
    assert p.level is PromotionLevel.STRONGLY_SUPPORTED
    assert any("skew" in e or "spilled" in e for e in p.confirming_evidence)


def test_promotion_streaming_backlog() -> None:
    f = _finding("STREAM070", "foreachBatch sink to orders-stream")
    promos = promote_findings([f], [_progress(backlog=True)])
    p = next(p for p in promos if p.check_id == "STREAM070")
    assert p.level is PromotionLevel.CONFIRMED  # stream name in message = identity
    assert any("backlog" in e for e in p.confirming_evidence)


def test_no_backlog_no_promotion() -> None:
    f = _finding("STREAM070", "foreachBatch sink")
    promos = promote_findings([f], [_progress(backlog=False)])
    assert not [p for p in promos if p.check_id == "STREAM070"]


def test_domain_errors_possible_only() -> None:
    f = _finding("SPARK001", "collect() to driver")
    m = _spark_log()
    from forge_doctor_data.core.runtime_evidence import ExecutionError

    m.errors.append(ExecutionError(code="JobFailed", message="job 0 failed"))
    promos = promote_findings([f], [m])
    assert promos[0].level is PromotionLevel.POSSIBLE
    assert promos[0].resulting_confidence is Confidence.LOW


def test_no_runtime_no_promotions() -> None:
    promos = promote_findings([_finding("SPARK003"), _finding("STREAM070")], [])
    assert promos == []


def test_promotion_id_deterministic() -> None:
    f = _finding("SPARK003")
    a = promote_findings([f], [_spark_log(single_task=True)])[0]
    b = promote_findings([f], [_spark_log(single_task=True)])[0]
    assert a.promotion_id == b.promotion_id
    assert a.base_fingerprint == f.fingerprint


def test_unrelated_domain_not_promoted() -> None:
    f = _finding("NEP020", "unbounded traversal")
    m = _spark_log(single_task=True)
    promos = promote_findings([f], [m])
    assert not [p for p in promos if p.level is not PromotionLevel.POSSIBLE]


# --- causal clusters ------------------------------------------------------------


def test_streaming_commit_chain_confirmed() -> None:
    results = [
        _finding("STREAM070", "foreachBatch sink", file="streams/orders.py"),
        _finding("PARQ040", "small-file proliferation in s3://bucket/orders"),
    ]
    clusters = cluster_findings(results, [_progress(batches=6), _athena()])
    chain = [c for c in clusters if c.id.startswith("RC_STREAM_COMMITS")]
    assert len(chain) == 1
    c = chain[0]
    assert c.confidence is PromotionLevel.CONFIRMED
    labels = [e.label for e in c.causal_edges]
    assert labels == ["commits per batch", "write amplification", "scan overhead"]
    assert any("commit" in e for e in c.evidence)
    assert any("athena" in e.lower() for e in c.evidence)
    assert "orders-stream" in c.affected_entities


def test_spark_skew_chain_confirmed() -> None:
    results = [_finding("SPARK009", "cartesian join risks skew")]
    model = _spark_log(skew=True, spill=True)
    clusters = cluster_findings(results, [model])
    chain = [c for c in clusters if c.id.startswith("RC_SPARK_SKEW")]
    assert len(chain) == 1
    c = chain[0]
    assert c.confidence is PromotionLevel.CONFIRMED
    ids = [e.source for e in c.causal_edges]
    assert ids == ["join_key", "skew", "spill"]
    assert any("SPARK009" in f for f in c.related_findings)


def test_chain_possible_with_partial_evidence() -> None:
    # Only microbatch + commit amplification: no small-file or consumer evidence.
    clusters = cluster_findings([], [_progress(batches=4, rows=5000)])
    chain = [c for c in clusters if c.id.startswith("RC_STREAM_COMMITS")]
    assert len(chain) == 1
    assert chain[0].confidence in {
        PromotionLevel.POSSIBLE,
        PromotionLevel.STRONGLY_SUPPORTED,
    }


def test_no_evidence_no_cluster() -> None:
    assert cluster_findings([], []) == []
    assert cluster_findings([_finding("SPARK001")], [_spark_log()]) == []


def test_clusters_deterministic_across_orderings() -> None:
    results_a = [
        _finding("STREAM070", "foreachBatch sink"),
        _finding("PARQ040", "small files"),
        _finding("SPARK009", "cartesian join"),
    ]
    results_b = list(reversed(results_a))
    models_a = [_progress(), _athena(), _spark_log(skew=True, spill=True)]
    models_b = list(reversed(models_a))
    a = cluster_findings(results_a, models_a)
    b = cluster_findings(results_b, models_b)
    assert [(c.id, c.confidence, c.evidence) for c in a] == [
        (c.id, c.confidence, c.evidence) for c in b
    ]
    pa = promote_findings(results_a, models_a)
    pb = promote_findings(results_b, models_b)
    assert [(p.promotion_id, p.level) for p in pa] == [(p.promotion_id, p.level) for p in pb]
