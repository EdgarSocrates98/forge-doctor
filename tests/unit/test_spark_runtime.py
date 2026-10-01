import json
from pathlib import Path

from forge_doctor.core.models import Severity
from forge_doctor.core.spark_runtime import (
    analyze_eventlog_text,
    analyze_log,
    analyze_plan_text,
)


def _event(event: str, **fields) -> str:
    return json.dumps({"Event": event, **fields})


def test_eventlog_empty():
    findings = analyze_eventlog_text("not json\n")
    assert findings[0].check_id == "RT000"
    assert findings[0].severity is Severity.INFO


def test_eventlog_executor_lost():
    text = _event("SparkListenerExecutorRemoved", **{"Removed Reason": "OOM"})
    findings = analyze_eventlog_text(text)
    assert any(f.check_id == "RT001" and f.severity is Severity.ERROR for f in findings)


def test_eventlog_single_task_stage_and_retries():
    lines = [
        _event("SparkListenerStageCompleted", **{"Stage Info": {"Number of Tasks": 1}}),
        _event(
            "SparkListenerTaskEnd",
            **{
                "Task Info": {"Stage ID": 1, "Attempt": 1},
                "Task Metrics": {"Scheduler Delay": 0},
            },
        ),
    ]
    findings = analyze_eventlog_text("\n".join(lines))
    ids = {f.check_id for f in findings}
    assert "RT005" in ids  # single-task stage
    assert "RT006" in ids  # retry


def test_eventlog_spill_and_gc():
    task = _event(
        "SparkListenerTaskEnd",
        **{
            "Task Info": {"Stage ID": 0},
            "Task Metrics": {
                "Memory Bytes Spilled": 4096,
                "Executor Run Time": 1000,
                "JVM GC Time": 400,
            },
        },
    )
    findings = analyze_eventlog_text(task)
    ids = {f.check_id for f in findings}
    assert "RT003" in ids  # spill
    assert "RT004" in ids  # GC pressure


def test_eventlog_clean():
    text = _event("SparkListenerApplicationStart", AppName="ok")
    findings = analyze_eventlog_text(text)
    assert findings[0].severity is Severity.PASS


def test_plan_cartesian_and_join_mix():
    plan = "*(2) CartesianProduct\n*(1) BroadcastNestedLoopJoin BuildRight\n"
    findings = analyze_plan_text(plan)
    ids = {f.check_id for f in findings}
    assert "RT010" in ids
    assert "RT011" in ids
    assert "RT014" in ids  # strategy mix info


def test_plan_clean():
    findings = analyze_plan_text("*(1) FileScan parquet\n*(1) HashAggregate")
    assert findings[0].severity is Severity.PASS


def test_log_fingerprints(tmp_path: Path):
    log = tmp_path / "driver.log"
    log.write_text("Lost executor 3: OOM\nOutOfMemoryError in task\n", encoding="utf-8")
    findings = analyze_log(log)
    assert findings and all(
        f.check_id.startswith(("RT", "SPARK", "GLUE", "PY", "DB", "ICE", "LF")) for f in findings
    )
    assert not all(f.severity is Severity.PASS for f in findings)
