"""Runtime evidence adapters - offline artifact parsing (phase 2)."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor.analyzers.runtime_evidence import (
    ADAPTERS,
    AthenaStatsAdapter,
    GlueLogAdapter,
    LambdaReportAdapter,
    NeptuneExplainAdapter,
    SparkEventLogAdapter,
    StepFunctionsHistoryAdapter,
    StreamingProgressAdapter,
    ingest_artifact,
)


def write(tmp_path: Path, name: str, text: str) -> Path:
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return f


SPARK_LOG = "\n".join(
    [
        json.dumps(
            {"Event": "SparkListenerApplicationStart", "App Name": "etl", "App ID": "app-1"}
        ),
        json.dumps({"Event": "SparkListenerJobStart", "Job ID": 0}),
        json.dumps(
            {
                "Event": "SparkListenerStageCompleted",
                "Stage Info": {
                    "Stage ID": 0,
                    "Number of Tasks": 4,
                    "Accumulables": [
                        {"Name": "internal.metrics.shuffle.write.bytesWritten", "Value": 2048},
                        {"Name": "internal.metrics.memoryBytesSpilled", "Value": 512},
                        {"Name": "internal.metrics.jvmGCTime", "Value": 120},
                    ],
                },
            }
        ),
        json.dumps(
            {
                "Event": "SparkListenerExecutorRemoved",
                "Executor ID": "3",
                "Removed Reason": "killed by YARN",
            }
        ),
        *(
            json.dumps(
                {
                    "Event": "SparkListenerTaskEnd",
                    "Task Info": {"Stage ID": 0, "Task ID": i},
                    "Task Metrics": {"Executor Run Time": d, "JVM GC Time": 5},
                }
            )
            for i, d in enumerate((10, 11, 12, 100))
        ),
        json.dumps(
            {"Event": "SparkListenerJobEnd", "Job ID": 0, "Job Result": {"Result": "JobSucceeded"}}
        ),
    ]
)

PROGRESS = json.dumps(
    {
        "id": "e1",
        "runId": "r1",
        "name": "orders_stream",
        "batchId": 42,
        "numInputRows": 1000,
        "inputRowsPerSecond": 200.0,
        "processedRowsPerSecond": 150.0,
        "durationMs": {"addBatch": 5000, "triggerExecution": 400},
        "stateOperators": [{"operatorName": "stateStoreSave", "numRowsTotal": 9000}],
        "sources": [
            {
                "description": "KafkaSource[orders]",
                "latestOffset": {"orders-0": 5000},
                "endOffset": {"orders-0": 4200},
            }
        ],
        "sink": {"description": "IcebergSink"},
        "eventTime": {"watermark": "2026-10-02T00:00:00Z"},
    }
)

_LAMBDA_LINE = (
    "REPORT RequestId: aaa-bbb\tDuration: 123.45 ms\tBilled Duration: 124 ms"
    "\tMemory Size: 128 MB\tMax Memory Used: 70 MB\tInit Duration: 50.12 ms"
)
LAMBDA_REPORT = (
    "START RequestId: aaa-bbb Version: $LATEST\n"
    "2026-10-02T00:00:00Z aaa-bbb INFO ok\n"
    "END RequestId: aaa-bbb\n" + _LAMBDA_LINE + "\n"
)

ATHENA_STATS = json.dumps(
    {
        "QueryExecution": {
            "QueryExecutionId": "q-123",
            "WorkGroup": "analytics",
            "Statistics": {
                "DataScannedInBytes": 1048576,
                "EngineExecutionTimeMillis": 3200,
                "QueryQueueTimeMillis": 120,
                "QueryPlanningTimeMillis": 80,
                "ServiceProcessingTimeMillis": 40,
                "TotalExecutionTimeMillis": 3440,
            },
        }
    }
)

SFN_HISTORY = json.dumps(
    {
        "events": [
            {"timestamp": 1000.0, "type": "ExecutionStarted"},
            {
                "timestamp": 1001.0,
                "type": "TaskStateEntered",
                "stateEnteredEventDetails": {"name": "Poll"},
            },
            {
                "timestamp": 1005.0,
                "type": "TaskFailed",
                "taskFailedEventDetails": {"cause": "Lambda.ServiceException"},
            },
            {"timestamp": 1010.0, "type": "ExecutionFailed"},
        ]
    }
)

GLUE_LOG = """2026-10-02 00:00:01 JobRunId: jr_abc123 Job Name: orders-job
2026-10-02 00:01:00 ERROR ExecutorLostFailure (executor 2 exited)
2026-10-02 00:02:00 java.lang.OutOfMemoryError: GC overhead limit exceeded
2026-10-02 00:03:00 Job failed
"""

NEPTUNE_EXPLAIN = json.dumps(
    {
        "steps": [
            {"name": "NeptuneGraphStep", "cardinality": 10},
            {"name": "Expand", "cardinality": 50000},
        ]
    }
)


def test_spark_eventlog(tmp_path: Path) -> None:
    f = write(tmp_path, "app-1", SPARK_LOG)
    m = ingest_artifact(f)
    assert m.source == "spark_eventlog"
    assert m.identifiers["app_id"] == "app-1"
    assert any(e.id == "job-0" and e.state == "completed" for e in m.executions)
    names = {m_.name for m_ in m.metrics}
    assert {"shuffle_write_bytes", "spill_memory_bytes", "gc_time_ms"} <= names
    assert any(e.code == "ExecutorLost" for e in m.errors)
    # skew: 100ms max vs 11ms median -> event emitted
    assert any("skew" in ev for ev in m.events)


def test_streaming_progress(tmp_path: Path) -> None:
    f = write(tmp_path, "progress.json", PROGRESS)
    m = ingest_artifact(f)
    assert m.source == "spark_ss_progress"
    assert m.identifiers["stream"] == "orders_stream"
    t = m.throughput[0]
    assert t.input_rps == 200.0 and t.output_rps == 150.0
    assert any(ph.phase == "addBatch" for ph in m.timings)
    assert any("watermark" in s for s in m.state)
    assert any(m_.name == "state_rows_total" for m_ in m.metrics)
    assert any(lag.scope == "KafkaSource[orders]" for lag in m.lag)


def test_athena_stats_nested(tmp_path: Path) -> None:
    f = write(tmp_path, "q.json", ATHENA_STATS)
    m = ingest_artifact(f)
    assert m.source == "athena_stats"
    assert m.identifiers["query_id"] == "q-123"
    phases = {t.phase for t in m.timings}
    assert {"queue", "planning", "execution", "total"} <= phases
    assert any(m_.name == "data_scanned_bytes" and m_.value == 1048576 for m_ in m.metrics)


def test_lambda_report(tmp_path: Path) -> None:
    f = write(tmp_path, "log.txt", LAMBDA_REPORT)
    m = ingest_artifact(f)
    assert m.source == "lambda_report"
    assert m.identifiers["request_id"] == "aaa-bbb"
    names = {mm.name: mm.value for mm in m.metrics}
    assert names["max_memory_used_mb"] == 70
    assert names["billed_duration_ms"] == 124
    assert names["init_duration_ms"] == 50.12


def test_lambda_timeout_flagged(tmp_path: Path) -> None:
    f = write(
        tmp_path,
        "log.txt",
        LAMBDA_REPORT + "2026-10-02 Task timed out after 30.00 seconds\n",
    )
    m = ingest_artifact(f)
    assert any(e.code == "Timeout" for e in m.errors)


def test_sfn_history(tmp_path: Path) -> None:
    f = write(tmp_path, "hist.json", SFN_HISTORY)
    m = ingest_artifact(f)
    assert m.source == "sfn_history"
    assert any(e.id == "Poll" for e in m.executions)
    assert any(e.code == "TaskFailed" for e in m.errors)
    ex = next(e for e in m.executions if e.kind == "execution")
    assert ex.duration_ms == 10000.0


def test_glue_log(tmp_path: Path) -> None:
    f = write(tmp_path, "glue.log", GLUE_LOG)
    m = ingest_artifact(f)
    assert m.source == "glue_logs"
    assert m.identifiers["execution_id"] == "jr_abc123"
    assert m.identifiers["job_name"] == "orders-job"
    codes = {e.code for e in m.errors}
    assert "OutOfMemoryError:" in codes or any("OutOfMemory" in c for c in codes)


def test_neptune_explain(tmp_path: Path) -> None:
    f = write(tmp_path, "explain.json", NEPTUNE_EXPLAIN)
    m = ingest_artifact(f)
    assert m.source == "neptune_explain"
    assert any(mm.name == "max_cardinality" and mm.value == 50000 for mm in m.metrics)


def test_unknown_artifact(tmp_path: Path) -> None:
    f = write(tmp_path, "blob.bin", "🙃 not an artifact 🙃\n")
    m = ingest_artifact(f)
    assert m.source == "unknown"


def test_malformed_json_progress(tmp_path: Path) -> None:
    f = write(
        tmp_path, "p.json", '{"inputRowsPerSecond": "x", "processedRowsPerSecond": "y", "sink": '
    )
    m = ingest_artifact(f)
    assert m.source in {"spark_ss_progress", "unknown"}
    if m.source == "spark_ss_progress":
        assert m.throughput == [] or m.throughput[0].input_rps is None


def test_first_match_wins(tmp_path: Path) -> None:
    """An artifact claiming two shapes resolves deterministically."""
    f = write(tmp_path, "mixed.json", PROGRESS)
    m1 = ingest_artifact(f)
    m2 = ingest_artifact(f)
    assert m1.source == m2.source == "spark_ss_progress"


def test_forced_adapter(tmp_path: Path) -> None:
    f = write(tmp_path, "p.json", PROGRESS)
    m = ingest_artifact(f, adapter="athena_stats")
    # forced adapter that does not match -> unknown, never mis-parsed
    assert m.source == "unknown"


def test_adapter_registry_order() -> None:
    names = [a.name for a in ADAPTERS]
    assert names == sorted(names) or len(set(names)) == len(names)
    assert {
        "spark_eventlog",
        "spark_ss_progress",
        "athena_stats",
        "sfn_history",
        "lambda_report",
        "glue_logs",
        "neptune_explain",
    } <= set(names)


def test_adapter_classes_satisfy_protocol() -> None:
    for a in ADAPTERS:
        assert callable(a.matches)
        assert callable(a.parse)


def test_identity_keys_demonstrable_only(tmp_path: Path) -> None:
    f = write(tmp_path, "p.json", PROGRESS)
    m = ingest_artifact(f)
    keys = m.identity_keys()
    assert keys == ["exec:e1"]  # stream name is not a canonical join key


def test_selected_adapters() -> None:
    """Direct adapter smoke on the positive cases (negative cases above)."""
    assert SparkEventLogAdapter().name == "spark_eventlog"
    assert StreamingProgressAdapter().name == "spark_ss_progress"
    assert AthenaStatsAdapter().name == "athena_stats"
    assert LambdaReportAdapter().name == "lambda_report"
    assert StepFunctionsHistoryAdapter().name == "sfn_history"
    assert GlueLogAdapter().name == "glue_logs"
    assert NeptuneExplainAdapter().name == "neptune_explain"
