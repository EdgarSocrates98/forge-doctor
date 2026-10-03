"""Adversarial tests for finding promotion and root-cause clustering.

The promotion layer must never let runtime artifacts manufacture certainty:
absence of proof is not negative proof, correlation is not confirmation, and
promotion must not rewrite the original finding's identity.
"""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.core.diagnosis import (
    PromotionLevel,
    cluster_findings,
    promote_findings,
)
from forge_doctor_data.core.models import CheckResult, Severity
from forge_doctor_data.core.runtime_evidence import (
    ExecutionError,
    ExecutionMetric,
    RuntimeEvidenceModel,
    RuntimeExecution,
)


def _finding(check_id: str, message: str = "m") -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=f"{check_id} title",
        severity=Severity.WARNING,
        category="test",
        message=message,
        file=Path("x.py"),
    )


def test_runtime_cannot_confirm_without_identity_overlap() -> None:
    """A spark event log full of single-task stages must not CONFIRM an
    unrelated repartition finding - shared domain caps at STRONGLY_SUPPORTED."""
    f = _finding("SPARK003", "repartition(1) in analytics.py - no overlap")
    m = RuntimeEvidenceModel(source="spark_eventlog")
    for i in range(5):
        m.executions.append(RuntimeExecution(id=f"stage-{i}", kind="stage", duration_ms=99_000))
        m.metrics.append(ExecutionMetric("task_count", 1, "tasks", scope=f"stage-{i}"))
    promos = promote_findings([f], [m])
    assert promos[0].level is PromotionLevel.STRONGLY_SUPPORTED
    assert promos[0].resulting_severity is Severity.WARNING  # not escalated


def test_promotion_never_mutates_original() -> None:
    f = _finding("SPARK003")
    fp_before = f.fingerprint
    sev_before = f.severity
    m = RuntimeEvidenceModel(source="spark_eventlog")
    m.metrics.append(ExecutionMetric("task_count", 1, "tasks", scope="stage-0"))
    promote_findings([f], [m])
    assert f.fingerprint == fp_before
    assert f.severity is sev_before


def test_empty_and_unreadable_models_are_safe() -> None:
    m = RuntimeEvidenceModel(source="unreadable")
    m2 = RuntimeEvidenceModel(source="unknown")
    promos = promote_findings([_finding("SPARK003"), _finding("PLAT001")], [m, m2])
    assert promos == []
    assert cluster_findings([_finding("SPARK003")], [m]) == []


def test_fabricated_error_flood_only_yields_possible() -> None:
    """A model stuffed with domain-adjacent errors must cap at POSSIBLE for
    unrelated checks - error noise is corroboration, never confirmation."""
    f = _finding("DDB020", "hot partition suspected")
    m = RuntimeEvidenceModel(source="glue_logs")
    for i in range(50):
        m.errors.append(ExecutionError(code="OOM", message=f"boom {i}"))
    promos = promote_findings([f], [m])
    # DDB is not in the glue_logs domain map - nothing at all.
    assert promos == []


def test_cluster_rejects_single_node_evidence() -> None:
    """One runtime fact alone must not manufacture a causal chain."""
    m = RuntimeEvidenceModel(source="spark_eventlog")
    m.metrics.append(ExecutionMetric("spill_disk_bytes", 1e9, "bytes", scope="s1"))
    clusters = cluster_findings([], [m])
    assert [c for c in clusters if c.id.startswith("RC_SPARK_SKEW")] == []


def test_cluster_confidence_never_exceeds_evidence() -> None:
    """All-static evidence with no runtime facts must not reach CONFIRMED."""
    results = [
        CheckResult(
            check_id="STREAM070",
            title="foreachBatch sink",
            severity=Severity.WARNING,
            category="streaming",
            message="foreachBatch",
            file=Path("s.py"),
        ),
        CheckResult(
            check_id="PARQ040",
            title="small-file proliferation",
            severity=Severity.WARNING,
            category="parquet",
            message="small files",
            file=Path("p.py"),
        ),
    ]
    clusters = cluster_findings(results, [])
    for c in clusters:
        assert c.confidence is not PromotionLevel.CONFIRMED
