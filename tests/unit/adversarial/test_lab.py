"""Forge Lab adversarial cases: malformed truth, edge matching, honesty."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.core.lab import (
    discover_scenarios,
    load_ground_truth,
    run_scenario,
)


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_missing_expected_json_means_empty_truth(tmp_path: Path) -> None:
    d = tmp_path / "no-truth"
    d.mkdir()
    _write(d, "a.py", "x = 1\n")
    report = run_scenario(d)
    assert report.passed  # nothing declared -> nothing missed
    assert report.findings.missed == []


def test_invalid_json_reported_not_raised(tmp_path: Path) -> None:
    d = tmp_path / "broken"
    d.mkdir()
    _write(d, "expected.json", "{not json")
    report = run_scenario(d)
    assert not report.passed
    assert any("invalid" in e for e in report.errors)


def test_non_object_truth_invalid(tmp_path: Path) -> None:
    d = tmp_path / "scalar"
    d.mkdir()
    _write(d, "expected.json", "[1, 2]")
    report = run_scenario(d)
    assert not report.passed and report.errors


def test_non_list_fields_ignored(tmp_path: Path) -> None:
    d = tmp_path / "weird"
    d.mkdir()
    _write(d, "expected.json", '{"expected_findings": "SPARK003"}')
    truth = load_ground_truth(d / "expected.json")
    assert truth.expected_findings == ()


def test_extra_findings_do_not_fail(tmp_path: Path) -> None:
    """Detected-but-unexpected findings are reported, not failed."""
    d = tmp_path / "extra"
    d.mkdir()
    _write(d, "a.py", "from pyspark.sql import SparkSession\n")
    _write(d, "expected.json", '{"expected_findings": []}')
    report = run_scenario(d)
    assert report.passed
    assert report.findings.extra  # anchors/anchors-ish results still listed


def test_forbidden_id_prefix_does_not_match(tmp_path: Path) -> None:
    """'SPARK003' must not match a real 'SPARK0030' id - exact check_id only."""
    d = tmp_path / "prefix"
    d.mkdir()
    _write(d, "expected.json", '{"forbidden_findings": ["SPARK003X"]}')
    _write(
        d,
        "j.py",
        "from pyspark.sql import SparkSession\n"
        "spark = SparkSession.builder.getOrCreate()\n"
        'df = spark.read.parquet("s3://b/in")\n'
        'df.repartition(1).write.parquet("s3://b/out")\n',
    )
    report = run_scenario(d)
    assert report.findings.forbidden_hit == []


def test_determinism_same_input_same_report(tmp_path: Path) -> None:
    d = tmp_path / "det"
    _write(
        d,
        "main.tf",
        'resource "aws_dynamodb_table" "t" {\n  name = "orders"\n'
        '  stream_enabled = true\n  stream_view_type = "NEW_IMAGE"\n}\n',
    )
    _write(d, "expected.json", '{"expected_findings": ["DDB001"]}')
    a = run_scenario(d)
    b = run_scenario(d)
    assert a.passed == b.passed
    assert a.findings.actual == b.findings.actual
    assert a.graph_edges.actual == b.graph_edges.actual


def test_runtime_dir_not_a_scenario(tmp_path: Path) -> None:
    """runtime/ subdirs hold artifacts, not scenarios."""
    d = tmp_path / "s"
    (d / "runtime").mkdir(parents=True)
    _write(d / "runtime", "b.json", "{}")
    _write(d, "expected.json", "{}")
    found = discover_scenarios(tmp_path)
    assert found == [d]


def test_capability_unknown_status_is_a_miss(tmp_path: Path) -> None:
    d = tmp_path / "cap-miss"
    d.mkdir()
    _write(d, "expected.json", '{"expected_capabilities": ["glue:NOPE@9.9=supported"]}')
    report = run_scenario(d)
    assert not report.passed
    assert report.capabilities.missed and "unknown" in report.capabilities.missed[0]
