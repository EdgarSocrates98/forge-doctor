"""Query execution model + engine adapters (spec 235)."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor.analyzers.execution_adapters import (
    EXECUTION_ADAPTERS,
    ingest_executions,
)
from forge_doctor.core.execution_model import (
    ExecutionStage,
    ExecutionStatus,
    QueryExecution,
    StageKind,
    derive_metrics,
    fingerprint_sql,
    redact_sql,
    sanitize_text,
)

# --- fingerprinting / redaction --------------------------------------------------


def test_fingerprint_stable_across_literals() -> None:
    a = fingerprint_sql("SELECT * FROM orders WHERE id = 10")
    b = fingerprint_sql("select  *  from  orders  where  id = 20")
    c = fingerprint_sql("SELECT * FROM orders WHERE id = 'abc'")
    d = fingerprint_sql("SELECT * FROM customers WHERE id = 10")
    assert a == b == c
    assert a != d


def test_fingerprint_keeps_identifiers() -> None:
    a = fingerprint_sql("SELECT a FROM t1")
    b = fingerprint_sql("SELECT a FROM t2")
    assert a != b


def test_redacted_sql_strips_literals() -> None:
    red = redact_sql("SELECT * FROM users WHERE email = 'ed@x.com' AND ssn = '123'")
    assert "ed@x.com" not in red and "123" not in red
    assert "users" in red and "where" in red


def test_sanitize_strips_credentials() -> None:
    out = sanitize_text("connect password=hunter2 host=x")
    assert "hunter2" not in out
    assert "password=<redacted>" in out


# --- metrics: honest UNKNOWN ------------------------------------------------------


def test_metrics_unknown_without_denominators() -> None:
    ex = QueryExecution(execution_id="e1", engine="snowflake", query_id="q1")
    m = derive_metrics(ex)
    assert m.scan_amplification is None
    assert m.queue_ratio is None
    assert "queue_ratio" in m.unknown()


def test_metrics_derived_with_evidence() -> None:
    ex = QueryExecution(
        execution_id="e1",
        engine="snowflake",
        queue_time_ms=100.0,
        duration_ms=1000.0,
        bytes_read=200.0,
        bytes_written=50.0,
    )
    m = derive_metrics(ex)
    assert m.queue_ratio is not None and m.queue_ratio.value == 0.1
    assert m.queue_ratio.basis.startswith("derived")
    assert m.scan_amplification is not None and m.scan_amplification.value == 4.0


def test_stage_rollup_metrics() -> None:
    stage = ExecutionStage(id="s1", kind=StageKind.SHUFFLE, shuffle_bytes=100.0)
    ex = QueryExecution(execution_id="e", engine="spark", stages=(stage,), bytes_read=200.0)
    m = derive_metrics(ex)
    assert m.shuffle_amplification is not None
    assert m.shuffle_amplification.value == 0.5


# --- adapters ---------------------------------------------------------------------

SPARK_LOG = "\n".join(
    [
        json.dumps(
            {
                "Event": "SparkListenerApplicationStart",
                "App ID": "app-1",
                "App Name": "job",
            }
        ),
        json.dumps(
            {
                "Event": "SparkListenerSQLExecutionStart",
                "Execution ID": "7",
                "Description": "select * from orders",
            }
        ),
        json.dumps(
            {
                "Event": "SparkListenerJobStart",
                "Job ID": "0",
                "Stage IDs": [0],
                "Properties": {"spark.sql.execution.id": "7"},
            }
        ),
        json.dumps(
            {
                "Event": "SparkListenerStageCompleted",
                "Stage Info": {
                    "Stage ID": 0,
                    "Number of Tasks": 4,
                    "Submission Time": 1000,
                    "Completion Time": 5000,
                },
            }
        ),
        json.dumps(
            {
                "Event": "SparkListenerTaskEnd",
                "Task Info": {"Stage ID": 0, "Task ID": 1},
                "Task Metrics": {
                    "Executor Run Time": 900,
                    "Input Metrics": {"Bytes Read": 1000},
                    "Shuffle Write Metrics": {"Bytes Written": 500},
                    "Memory Bytes Spilled": 64,
                },
            }
        ),
    ]
)

SNOWFLAKE_ROWS = json.dumps(
    [
        {
            "QUERY_ID": "01ab",
            "QUERY_TEXT": "select * from orders where id = 7",
            "EXECUTION_TIME": 1200,
            "BYTES_SCANNED": 4096,
            "PARTITIONS_SCANNED": 4,
            "PARTITIONS_TOTAL": 100,
            "QUEUED_OVERLOAD_TIME": 300,
            "BYTES_SPILLED_TO_LOCAL_STORAGE": 1000,
        }
    ]
)

BQ_ROWS = json.dumps(
    [
        {
            "job_id": "job-9",
            "query": "select id from t",
            "total_bytes_processed": 100000,
            "total_bytes_billed": 100000,
            "total_slot_ms": 5000,
            "state": "DONE",
        }
    ]
)

REDSHIFT_ROWS = json.dumps(
    [
        {
            "query": "555",
            "querytxt": "select 1 from t",
            "total_exec_time": 4200000,
            "wlm_queue_time": 50000,
            "service_class": "6",
            "aborted": "0",
        }
    ]
)

TRINO_QUERY = json.dumps(
    {
        "queryId": "20261003_1",
        "query": "select * from hive.t",
        "state": "FINISHED",
        "queryStats": {
            "elapsedTime": "2.5s",
            "queuedTime": "0.3s",
            "processedInputDataSize": "10MB",
            "processedInputRows": 5000,
        },
        "outputStageInfos": {
            "stages": [
                {
                    "stageId": "0",
                    "operatorTypes": ["TableScanOperator"],
                    "stageStats": {
                        "rawInputDataSize": "10MB",
                        "rawInputRows": 5000,
                    },
                }
            ]
        },
    }
)

CLICKHOUSE_ROWS = json.dumps(
    [
        {
            "query_id": "ch-1",
            "query": "select * from hits",
            "type": "QueryFinish",
            "query_duration_ms": 850,
            "read_rows": 40000,
            "read_bytes": 8000000,
            "result_rows": 12,
            "memory_usage": 16000000,
        }
    ]
)


def _ingest(tmp_path: Path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return ingest_executions(p)


def test_spark_adapter(tmp_path: Path) -> None:
    name, exs = _ingest(tmp_path, "eventlog", SPARK_LOG)
    assert name == "spark_execution"
    assert len(exs) == 1
    ex = exs[0]
    assert ex.engine == "spark"
    assert ex.stages and ex.stages[0].shuffle_bytes == 500.0
    assert ex.stages[0].spill_bytes == 64.0
    assert ex.duration_ms == 4000.0
    assert ex.query_fingerprint  # SQL execution start description
    assert ex.evidence and "artifact:" in ex.evidence[1]


def test_snowflake_adapter(tmp_path: Path) -> None:
    name, exs = _ingest(tmp_path, "qh.json", SNOWFLAKE_ROWS)
    assert name == "snowflake_execution"
    ex = exs[0]
    assert ex.query_id == "01ab"
    assert ex.bytes_read == 4096.0
    assert ex.queue_time_ms == 300.0
    assert ex.metrics.queue_ratio is not None
    assert ex.spill_bytes == 1000.0
    scan = ex.stages[0].scans[0]
    assert scan.pruning == 0.04  # 4/100 partitions


def test_bigquery_adapter(tmp_path: Path) -> None:
    name, exs = _ingest(tmp_path, "jobs.json", BQ_ROWS)
    assert name == "bigquery_execution"
    ex = exs[0]
    assert ex.bytes_read == 100000.0
    assert ex.cpu_time_ms == 5000.0
    assert ex.status is ExecutionStatus.COMPLETED


def test_redshift_adapter(tmp_path: Path) -> None:
    name, exs = _ingest(tmp_path, "stl.json", REDSHIFT_ROWS)
    assert name == "redshift_execution"
    ex = exs[0]
    assert ex.query_id == "555"
    assert ex.queue_time_ms == 50000.0
    assert ex.status is ExecutionStatus.COMPLETED


def test_trino_adapter(tmp_path: Path) -> None:
    name, exs = _ingest(tmp_path, "query.json", TRINO_QUERY)
    assert name == "trino_execution"
    ex = exs[0]
    assert ex.engine == "trino"
    assert ex.stages[0].kind is StageKind.SCAN
    assert ex.rows_read == 5000.0
    assert ex.queue_time_ms == 300.0


def test_clickhouse_adapter(tmp_path: Path) -> None:
    name, exs = _ingest(tmp_path, "qlog.json", CLICKHOUSE_ROWS)
    assert name == "clickhouse_execution"
    ex = exs[0]
    assert ex.rows_read == 40000.0
    assert ex.memory_peak == 16000000.0


def test_malformed_artifact_no_claim(tmp_path: Path) -> None:
    name, exs = _ingest(tmp_path, "junk.txt", "definitely not an export {{{")
    assert name == "unknown" and exs == []


def test_unmatched_file(tmp_path: Path) -> None:
    name, exs = _ingest(tmp_path, "empty.json", "{}")
    assert name == "unknown" and exs == []


def test_adapter_registry_order_and_names() -> None:
    names = [a.name for a in EXECUTION_ADAPTERS]
    assert len(names) == len(set(names))
    assert names[0] == "spark_execution"


def test_forced_adapter(tmp_path: Path) -> None:
    p = tmp_path / "qh.json"
    p.write_text(SNOWFLAKE_ROWS)
    name, exs = ingest_executions(p, adapter="snowflake_execution")
    assert name == "snowflake_execution" and len(exs) == 1
    name2, exs2 = ingest_executions(p, adapter="bigquery_execution")
    assert name2 == "unknown" and exs2 == []


def test_determinism(tmp_path: Path) -> None:
    n1, e1 = _ingest(tmp_path, "a.json", SNOWFLAKE_ROWS)
    n2, e2 = _ingest(tmp_path, "a.json", SNOWFLAKE_ROWS)
    assert (n1, [e.to_dict() for e in e1]) == (n2, [e.to_dict() for e in e2])


def test_to_dict_serializable(tmp_path: Path) -> None:
    _, exs = _ingest(tmp_path, "qh.json", SNOWFLAKE_ROWS)
    payload = json.dumps(exs[0].to_dict())
    assert "01ab" in payload
    assert "unknown_metrics" in payload
