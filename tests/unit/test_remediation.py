"""Deterministic remediation planning (phase 4)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.core.diagnosis import cluster_findings
from forge_doctor_data.core.models import CheckResult, Severity
from forge_doctor_data.core.remediation import plan_remediation, remediation_entries
from forge_doctor_data.core.runtime_evidence import (
    ExecutionMetric,
    ExecutionThroughput,
    RuntimeEvidenceModel,
    RuntimeExecution,
)


def _finding(check_id: str, file: str = "jobs/etl.py") -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=f"{check_id} title",
        severity=Severity.WARNING,
        category="test",
        message="m",
        file=Path(file),
    )


def test_packs_load_and_have_provenance() -> None:
    entries = remediation_entries()
    assert len(entries) >= 15
    for e in entries:
        assert e.get("match"), "every entry needs a match"
        assert e.get("actions"), "every entry needs actions"


def test_plan_for_finding() -> None:
    plans = plan_remediation([_finding("SPARK003")])
    plan = next(p for p in plans if p.id == "PLAN-SPARK003")
    assert "single-partition" in plan.problem
    assert plan.targets == ("jobs/etl.py",)
    assert len(plan.actions) >= 2
    assert plan.actions[1].depends_on == ("A1",)


def test_plan_targets_aggregated_sorted() -> None:
    plans = plan_remediation(
        [_finding("ICE008", "b.py"), _finding("ICE008", "a.py"), _finding("ICE008", "b.py")]
    )
    plan = next(p for p in plans if p.id == "PLAN-ICE008")
    assert plan.targets == ("a.py", "b.py")


def test_unmatched_finding_no_plan() -> None:
    plans = plan_remediation([_finding("REP001")])
    assert plans == []


def test_chain_plan_via_root_cause() -> None:
    m = RuntimeEvidenceModel(source="spark_ss_progress")
    m.identifiers["stream"] = "orders-stream"
    for i in range(5):
        m.executions.append(RuntimeExecution(id=f"batch-{i}", kind="batch", duration_ms=1500))
    m.throughput.append(
        ExecutionThroughput(
            name="orders-stream",
            input_rps=120.0,
            output_rps=80.0,
            input_rows=400,
            duration_ms=1500,
        )
    )
    results = [_finding("STREAM070"), _finding("PARQ040")]
    clusters = cluster_findings(results, [m])
    cluster = next(c for c in clusters if c.id.startswith("RC_STREAM_COMMITS"))
    plans = plan_remediation(results, clusters, root_cause=cluster.id)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.id == "PLAN-RC_STREAM_COMMITS"
    # required example: trigger -> partitioning -> compact -> expire -> re-measure
    assert len(plan.actions) == 5
    assert plan.actions[0].depends_on == ()
    assert "A1" in plan.actions[1].depends_on
    assert "orders-stream" in plan.targets


def test_root_cause_prefix_match() -> None:
    results = [_finding("SPARK009")]
    m = RuntimeEvidenceModel(source="spark_eventlog")
    m.executions.extend(
        [
            RuntimeExecution(id="stage-1", kind="stage", duration_ms=5000),
            RuntimeExecution(id="stage-2", kind="stage", duration_ms=60000),
        ]
    )
    m.metrics.extend(
        [
            ExecutionMetric("spill_memory_bytes", 1e9, "bytes", scope="stage-2"),
            ExecutionMetric("task_count", 8, "tasks", scope="stage-2"),
            ExecutionMetric("task_max_ms", 55000, "ms", scope="stage-2"),
        ]
    )
    m.events.append("stage-2 skew: max task 55000ms vs median 4000ms")
    clusters = cluster_findings(results, [m])
    plans = plan_remediation(results, clusters, root_cause="RC_SPARK_SKEW")
    assert [p.id for p in plans] == ["PLAN-RC_SPARK_SKEW"]
    assert len(plans[0].actions) == 4


def test_root_cause_no_match_empty() -> None:
    assert plan_remediation([_finding("SPARK003")], [], root_cause="RC_NOPE") == []


def test_plans_deterministic() -> None:
    results = [
        _finding("ICE008", "b.py"),
        _finding("SPARK003", "a.py"),
        _finding("PARQ040", "c.py"),
    ]
    a = plan_remediation(results)
    b = plan_remediation(list(reversed(results)))
    assert [p.id for p in a] == [p.id for p in b]
    assert a == b


def test_actions_never_autoapply() -> None:
    """No action field may contain an executable/patch directive."""
    for e in remediation_entries():
        for act in e.get("actions", []):
            blob = json_dump(act).lower()
            for banned in ("auto-commit", "terraform apply", "git push", "deploy"):
                assert banned not in blob


def json_dump(x: object) -> str:
    import json

    return json.dumps(x)
