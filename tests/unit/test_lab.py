"""Forge Lab scenario runner (roadmap-2 phase 1)."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor.core.lab import (
    discover_scenarios,
    load_ground_truth,
    parse_finding_expectation,
    run_lab,
    run_scenario,
)

_SPARK = (
    "from pyspark.sql import SparkSession\n"
    "spark = SparkSession.builder.getOrCreate()\n"
    'df = spark.read.parquet("s3://b/in")\n'
    'df.repartition(1).write.mode("overwrite").parquet("s3://b/out")\n'
)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _scenario(root: Path, name: str, truth: dict, files: dict[str, str]) -> Path:
    d = root / name
    for rel, text in files.items():
        _write(d, rel, text)
    _write(d, "expected.json", json.dumps(truth))
    return d


def test_discover_scenarios_sorted(tmp_path: Path) -> None:
    _scenario(tmp_path, "b-scen", {}, {})
    _scenario(tmp_path, "a-scen", {}, {})
    (tmp_path / "not-a-scen").mkdir()
    found = discover_scenarios(tmp_path)
    assert [p.name for p in found] == ["a-scen", "b-scen"]


def test_load_ground_truth_missing(tmp_path: Path) -> None:
    truth = load_ground_truth(tmp_path / "nope" / "expected.json")
    assert truth.expected_findings == ()


def test_parse_finding_expectation() -> None:
    e = parse_finding_expectation("SPARK003@jobs/etl.py")
    assert e.check_id == "SPARK003" and e.file_fragment == "jobs/etl.py"
    e2 = parse_finding_expectation("SPARK003")
    assert e2.file_fragment == ""


def test_scenario_expected_finding_passes(tmp_path: Path) -> None:
    d = _scenario(
        tmp_path,
        "spark-x",
        {"expected_findings": ["SPARK003"], "forbidden_findings": ["DELTA001"]},
        {"jobs.py": _SPARK},
    )
    report = run_scenario(d)
    assert report.passed
    assert "SPARK003" in report.findings.actual
    assert report.findings.missed == []


def test_scenario_missed_finding_fails(tmp_path: Path) -> None:
    d = _scenario(
        tmp_path,
        "spark-miss",
        {"expected_findings": ["SPARK099"]},
        {"jobs.py": _SPARK},
    )
    report = run_scenario(d)
    assert not report.passed
    assert report.findings.missed == ["SPARK099"]


def test_scenario_forbidden_hit_fails(tmp_path: Path) -> None:
    d = _scenario(
        tmp_path,
        "spark-forbidden",
        {"forbidden_findings": ["SPARK003"]},
        {"jobs.py": _SPARK},
    )
    report = run_scenario(d)
    assert not report.passed
    assert report.findings.forbidden_hit == ["SPARK003"]


def test_finding_file_fragment_scopes_match(tmp_path: Path) -> None:
    d = _scenario(
        tmp_path,
        "spark-file",
        {"expected_findings": ["SPARK003@jobs.py", "SPARK003@other.py"]},
        {"jobs.py": _SPARK},
    )
    report = run_scenario(d)
    assert not report.passed
    assert report.findings.missed == ["SPARK003@other.py"]


def test_graph_edges_expected(tmp_path: Path) -> None:
    tf = 'resource "aws_dynamodb_table" "t" {\n  name = "orders"\n}\n'
    d = _scenario(
        tmp_path,
        "ddb-edge",
        {
            "expected_graph_edges": [
                "defines|infrastructure_resource:aws:aws_dynamodb_table.t->table:dynamodb:orders"
            ]
        },
        {"main.tf": tf},
    )
    report = run_scenario(d)
    assert report.passed, report.graph_edges.missed


def test_capability_expectation(tmp_path: Path) -> None:
    d = _scenario(
        tmp_path,
        "cap-check",
        {
            "expected_capabilities": [
                "iceberg:ICEBERG_MERGE_WRITE;format_version=2=supported",
                "iceberg:ICEBERG_MERGE_WRITE;format_version=1=unsupported",
            ]
        },
        {"a.py": "x = 1\n"},
    )
    report = run_scenario(d)
    assert report.passed, report.capabilities.missed


def test_root_cause_chain_via_runtime(tmp_path: Path) -> None:
    progress = json.dumps(
        {
            "batchId": 0,
            "inputRowsPerSecond": 100.0,
            "processedRowsPerSecond": 50.0,
            "numInputRows": 100,
            "sink": {},
            "sources": [{"description": "KafkaV2[Subscribe[t]]"}],
        }
    )
    stream = (
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        'ss = spark.readStream.format("kafka").option("subscribe", "t").load()\n'
        'q = ss.writeStream.format("delta").option("checkpointLocation", "s3://c").start()\n'
    )
    d = _scenario(
        tmp_path,
        "stream-chain",
        {"expected_root_causes": ["RC_STREAM_COMMITS"]},
        {"s.py": stream, "runtime/b0.json": progress},
    )
    report = run_scenario(d)
    assert report.passed, (report.root_causes.missed, report.root_causes.actual)


def test_run_lab_aggregates(tmp_path: Path) -> None:
    _scenario(tmp_path, "ok", {"expected_findings": ["SPARK003"]}, {"a.py": _SPARK})
    _scenario(tmp_path, "bad", {"expected_findings": ["NOPE"]}, {"a.py": _SPARK})
    lab = run_lab(tmp_path)
    assert lab.passed == 1 and lab.failed == 1
    assert not lab.ok


def test_lab_run_named_filter(tmp_path: Path) -> None:
    _scenario(tmp_path, "one", {"expected_findings": ["SPARK003"]}, {"a.py": _SPARK})
    _scenario(tmp_path, "two", {"expected_findings": ["NOPE"]}, {"a.py": _SPARK})
    lab = run_lab(tmp_path, scenario="one")
    assert len(lab.reports) == 1 and lab.reports[0].passed
