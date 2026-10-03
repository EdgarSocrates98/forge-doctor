"""Cross-engine performance intelligence (spec 236)."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor_data.analyzers.execution_adapters import ingest_executions
from forge_doctor_data.core.execution_model import (
    ExecutionScan,
    ExecutionStage,
    QueryExecution,
    StageKind,
)
from forge_doctor_data.core.models import Severity
from forge_doctor_data.core.performance import (
    PerformanceSignal,
    PerfPolicy,
    RegressionClass,
    SignalFamily,
    baseline,
    build_trends,
    classify_regression,
    extract_signals,
    perf_findings,
    queue_pressure,
    skew_signals,
)


def _exec(**kw) -> QueryExecution:
    base = {"execution_id": "e1", "engine": "snowflake"}
    base.update(kw)
    ex = QueryExecution(
        **{k: v for k, v in base.items() if k != "metrics"},
    )
    # derive metrics like adapters do
    from forge_doctor_data.core.execution_model import derive_metrics

    return QueryExecution(
        **{
            f: getattr(ex, f)
            for f in (
                "execution_id",
                "engine",
                "query_id",
                "query_fingerprint",
                "start_time",
                "end_time",
                "duration_ms",
                "queue_time_ms",
                "status",
                "stages",
                "inputs",
                "outputs",
                "bytes_read",
                "bytes_written",
                "rows_read",
                "rows_written",
                "cpu_time_ms",
                "memory_peak",
                "spill_bytes",
                "network_bytes",
                "evidence",
            )
        },
        metrics=derive_metrics(ex),
    )


def test_scan_amplification_signal() -> None:
    ex = _exec(bytes_read=1000.0, bytes_written=100.0)
    sigs = extract_signals([ex])
    fams = {s.family for s in sigs}
    assert SignalFamily.SCAN_AMPLIFICATION in fams
    sig = next(s for s in sigs if s.family is SignalFamily.SCAN_AMPLIFICATION)
    assert sig.value == 10.0
    assert "derived" in sig.derived


def test_no_signal_without_evidence() -> None:
    ex = _exec()  # no bytes, no stages
    sigs = extract_signals([ex])
    assert sigs == []


def test_spill_and_queue_signals() -> None:
    ex = _exec(
        duration_ms=1000.0,
        queue_time_ms=400.0,
        spill_bytes=100.0,
        memory_peak=200.0,
    )
    sigs = extract_signals([ex])
    fams = {s.family for s in sigs}
    assert SignalFamily.SPILL_PRESSURE in fams
    assert SignalFamily.QUEUE_PRESSURE in fams
    qp = queue_pressure([ex])
    assert qp[0].share == 0.4
    assert qp[0].mechanism == "warehouse_queue"


def test_poor_pruning_signal() -> None:
    stage = ExecutionStage(
        id="s0",
        kind=StageKind.SCAN,
        scans=(ExecutionScan(partitions_scanned=95.0, partitions_total=100.0, pruning=0.95),),
    )
    ex = _exec(stages=(stage,))
    sigs = extract_signals([ex])
    prune = [s for s in sigs if s.family is SignalFamily.POOR_PRUNING]
    assert prune and prune[0].value == 0.95


def test_skew_requires_distribution() -> None:
    ex = _exec()
    assert skew_signals([ex], {}) == []
    skew = skew_signals([ex], {"e1:stage-1": [10, 12, 11, 10, 200, 13]})
    assert len(skew) == 1
    assert skew[0].ratio > 4
    assert skew[0].p95_ms >= skew[0].median_ms


def test_skew_foreign_execution_ignored() -> None:
    ex = _exec()
    skew = skew_signals([ex], {"other:stage-1": [1, 2, 3, 4, 50]})
    assert skew == []


def test_perf_findings_policy_gated() -> None:
    ex = _exec(
        bytes_read=1000.0,
        bytes_written=100.0,
        duration_ms=1000.0,
        queue_time_ms=500.0,
    )
    sigs = extract_signals([ex])
    # no policy -> informational only, threshold says why
    findings = perf_findings([ex], sigs, None)
    assert findings
    assert all(f.severity is Severity.INFO for f in findings)
    assert all(f.threshold == "no configured bound" for f in findings)
    # policy -> fired warnings
    policy = PerfPolicy.defaults()
    fired = perf_findings([ex], sigs, policy)
    warns = {f.check_id for f in fired if f.severity is Severity.WARNING}
    assert "PERF001" in warns  # scan amp 10 > 5
    assert "PERF006" in warns  # queue share .5 > .3


def test_perf004_skew_finding() -> None:
    ex = _exec()
    skew = skew_signals([ex], {"e1:s0": [10, 10, 11, 12, 400]})
    findings = perf_findings([ex], [], PerfPolicy.defaults(), skew=skew)
    p4 = [f for f in findings if f.check_id == "PERF004"]
    assert p4 and p4[0].severity is Severity.WARNING
    assert "ratio" in p4[0].derived


def test_finding_serialization() -> None:
    ex = _exec(bytes_read=10.0, bytes_written=1.0)
    f = perf_findings([ex], extract_signals([ex]), PerfPolicy.defaults())[0]
    payload = f.to_dict()
    assert json.dumps(payload)
    assert payload["check_id"].startswith("PERF")


def test_trends_and_baseline() -> None:
    exs = [
        _exec(
            execution_id=f"e{i}",
            query_fingerprint="fp1",
            start_time=float(i),
            duration_ms=d,
            bytes_read=100.0,
        )
        for i, d in enumerate([100, 110, 105, 108, 112])
    ]
    trends = build_trends(exs)
    assert "fp1" in trends and trends["fp1"].runs == 5
    base = baseline(trends["fp1"])
    assert 100 <= base.median_duration <= 112
    slow = _exec(execution_id="new", query_fingerprint="fp1", duration_ms=base.p95_duration * 2)
    assert classify_regression(slow, base) is RegressionClass.REGRESSED
    fast = _exec(execution_id="n2", query_fingerprint="fp1", duration_ms=10.0)
    assert classify_regression(fast, base) is RegressionClass.IMPROVED
    assert classify_regression(fast, None) is RegressionClass.NEW
    sparse = _exec(execution_id="n3", query_fingerprint="fp1", duration_ms=None)
    assert classify_regression(sparse, base) is RegressionClass.INSUFFICIENT_DATA


def test_no_fingerprint_no_trend() -> None:
    ex = _exec(query_fingerprint="")
    assert build_trends([ex]) == {}


def test_signal_provenance() -> None:
    ex = _exec(
        bytes_read=100.0,
        bytes_written=10.0,
        evidence=("adapter:test", "artifact:x.json"),
    )
    sig = extract_signals([ex])[0]
    assert any(e.startswith("execution:") for e in sig.evidence)
    assert "engine:" in "|".join(sig.evidence)


def test_end_to_end_from_artifact(tmp_path: Path) -> None:
    rows = json.dumps(
        [
            {
                "QUERY_ID": "q1",
                "QUERY_TEXT": "select * from t",
                "EXECUTION_TIME": 1000,
                "BYTES_SCANNED": 5000,
                "QUEUED_OVERLOAD_TIME": 400,
            }
        ]
    )
    p = tmp_path / "qh.json"
    p.write_text(rows)
    name, exs = ingest_executions(p)
    assert name == "snowflake_execution"
    sigs = extract_signals(exs)
    fams = {s.family for s in sigs}
    assert SignalFamily.QUEUE_PRESSURE in fams


# --- physical design ---------------------------------------------------------


def test_extract_designs_from_graph() -> None:
    from forge_doctor_data.core.physical_design import extract_designs, physical_findings
    from forge_doctor_data.core.platform_graph import (
        DataPlatformGraph,
        Entity,
        EntityKind,
    )

    g = DataPlatformGraph()
    g.add_entity(
        Entity(
            kind=EntityKind.TABLE,
            domain="redshift",
            identifier="orders",
            attrs=(("diststyle", "key"), ("distkey", "customer_id"), ("sortkey", "ts")),
        )
    )
    g.add_entity(
        Entity(
            kind=EntityKind.TABLE,
            domain="bigquery",
            identifier="events",
            attrs=(("partition_by", "ts"), ("cluster_by", "user")),
        )
    )
    designs = extract_designs(g)
    engines = {d.engine for d in designs}
    assert "redshift" in engines and "bigquery" in engines
    rs = next(d for d in designs if d.engine == "redshift")
    assert "distkey=customer_id" in rs.distribution
    assert rs.ordering == ("ts",)
    assert rs.evidence and rs.evidence[0].startswith("entity:")
    findings = physical_findings(designs)
    assert isinstance(findings, list)


def test_phy005_fanout() -> None:
    from forge_doctor_data.core.physical_design import PhysicalDesign, physical_findings

    designs = [PhysicalDesign(engine=e, subject="orders") for e in ("a", "b", "c", "d")]
    out = physical_findings(designs)
    assert any(f.check_id == "PHY005" for f in out)


def test_phy002_needs_signals() -> None:

    sig = PerformanceSignal(
        SignalFamily.POOR_PRUNING,
        "orders",
        "snowflake",
        observed="95/100 partitions scanned",
        derived="pruning_share=0.95",
    )
    from forge_doctor_data.core.physical_design import PhysicalDesign, physical_findings

    out = physical_findings([PhysicalDesign(engine="snowflake", subject="orders")], [sig])
    assert any(f.check_id == "PHY002" for f in out)


def test_phy001_no_org_keys() -> None:
    from forge_doctor_data.core.physical_design import PhysicalDesign, physical_findings

    bare = PhysicalDesign(engine="opensearch", subject="logs", replication="replicas=1")
    organized = PhysicalDesign(engine="bigquery", subject="events", partitioning=("ts",))
    out = physical_findings([bare, organized])
    ids = {f.check_id for f in out}
    assert "PHY001" in ids
    phy1 = [f for f in out if f.check_id == "PHY001"]
    assert len(phy1) == 1 and "logs" in phy1[0].message


def test_phy004_storage_pressure() -> None:
    from forge_doctor_data.core.physical_design import PhysicalDesign, physical_findings

    sig = PerformanceSignal(
        SignalFamily.SMALL_FILE_AMPLIFICATION,
        "events",
        "spark",
        observed="avg 128KB/file",
        derived="small_file penalty",
    )
    designs = [PhysicalDesign(engine="spark", subject="events", partitioning=("dt",))]
    out = physical_findings(designs, [sig])
    assert any(f.check_id == "PHY004" for f in out)
    # without signals it stays quiet
    assert not any(f.check_id == "PHY004" for f in physical_findings(designs))
