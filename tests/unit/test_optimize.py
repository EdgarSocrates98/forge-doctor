"""Optimization intelligence: candidate enumeration + CLI (spec 229)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor.cli.app import app
from forge_doctor.core.models import (
    CheckResult,
    Confidence,
    EvidenceKind,
    ScanReport,
    Severity,
)
from forge_doctor.core.optimize import optimize
from forge_doctor.core.platform_graph import DataPlatformGraph, Entity, EntityKind

runner = CliRunner()


def _report(*results: CheckResult, root: Path = Path("/tmp/proj")) -> ScanReport:
    return ScanReport(version="1", project=root, results=list(results))


def _finding(
    check_id: str,
    file: str | None = None,
    evidence_kind: EvidenceKind | None = EvidenceKind.STATIC,
    severity: Severity = Severity.WARNING,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title=f"{check_id} title",
        severity=severity,
        category="cat",
        message="m",
        file=Path(file) if file else None,
        confidence=Confidence.HIGH,
        evidence="e",
        evidence_kind=evidence_kind,
    )


def _graph(*entities: Entity) -> DataPlatformGraph:
    g = DataPlatformGraph()
    for e in entities:
        g.add_entity(e)
    return g


def test_no_evidence_no_candidates() -> None:
    assert optimize(_report(), _graph(), Path("/tmp/proj")) == []


def test_clean_finding_yields_no_candidate() -> None:
    # a finding with no optimization mapping produces no row
    report = _report(_finding("X999"))
    assert optimize(report, _graph(), Path("/tmp/proj")) == []


def test_spark003_maps_to_partition_data() -> None:
    report = _report(_finding("SPARK003", file="job.py"))
    rows = optimize(report, _graph(), Path("/tmp/proj"))
    assert [r.optimization for r in rows] == ["partition-data"]
    row = rows[0]
    assert row.files == ("job.py",)
    assert "--hypothesis partition-data" in row.validate_command
    assert any(e.startswith("SPARK003:") for e in row.evidence)


def test_stream002_maps_to_add_checkpoint() -> None:
    report = _report(_finding("STREAM002", file="stream.py"))
    rows = optimize(report, _graph(), Path("/tmp/proj"))
    assert [r.optimization for r in rows] == ["add-checkpoint"]
    assert "--hypothesis add-checkpoint" in rows[0].validate_command


def test_dedupe_per_optimization_and_file() -> None:
    report = _report(
        _finding("SPARK003", file="a.py"),
        _finding("SPARK003", file="a.py"),
        _finding("SPARK003", file="b.py"),
    )
    rows = optimize(report, _graph(), Path("/tmp/proj"))
    assert [tuple(r.files) for r in rows] == [("a.py",), ("b.py",)]


def test_entity_attribution_and_cost_proxy() -> None:
    ent = Entity(
        kind=EntityKind.COMPUTE_JOB,
        domain="spark",
        identifier="job",
        file=Path("job.py"),
    )
    report = _report(_finding("SPARK003", file="job.py"))
    rows = optimize(report, _graph(ent), Path("/tmp/proj"))
    row = rows[0]
    assert row.entities == (ent.id,)
    assert row.cost_proxy == 1
    assert f"entity:{ent.id}" in row.evidence


def test_confidence_from_evidence_plane() -> None:
    static = optimize(_report(_finding("SPARK003", file="a.py")), _graph(), Path("/tmp/p"))
    derived = optimize(
        _report(_finding("SPARK003", file="a.py", evidence_kind=EvidenceKind.DERIVED)),
        _graph(),
        Path("/tmp/p"),
    )
    assert static[0].confidence == "low"
    assert derived[0].confidence == "medium"


def test_glue_version_upgrade_candidate() -> None:
    ent = Entity(
        kind=EntityKind.COMPUTE_JOB,
        domain="glue",
        identifier="etl",
        attrs=(("glue_version", "4.0"),),
    )
    rows = optimize(_report(), _graph(ent), Path("/tmp/proj"))
    up = [r for r in rows if r.optimization == "glue-version-upgrade"]
    assert len(up) == 1
    assert "glue-version=6.0" in up[0].validate_command
    assert "what-if" in up[0].validate_command
    assert up[0].entities == (ent.id,)


def test_no_upgrade_when_at_latest() -> None:
    ent = Entity(
        kind=EntityKind.COMPUTE_JOB,
        domain="glue",
        identifier="etl",
        attrs=(("glue_version", "6.0"),),
    )
    rows = optimize(_report(), _graph(ent), Path("/tmp/proj"))
    assert all(r.optimization != "glue-version-upgrade" for r in rows)


def test_deterministic_ordering() -> None:
    results = [
        _finding("STREAM002", file="b.py"),
        _finding("SPARK003", file="a.py"),
        _finding("PLAT003", file="c.py", evidence_kind=EvidenceKind.DERIVED),
    ]
    a = optimize(_report(*results), _graph(), Path("/tmp/p"))
    b = optimize(_report(*reversed(results)), _graph(), Path("/tmp/p"))
    assert [r.to_dict() for r in a] == [r.to_dict() for r in b]


def test_to_dict_stable_shape() -> None:
    report = _report(_finding("SPARK003", file="job.py"))
    row = optimize(report, _graph(), Path("/tmp/proj"))[0].to_dict()
    assert set(row) == {
        "optimization",
        "reason",
        "files",
        "entities",
        "evidence",
        "cost_proxy",
        "confidence",
        "validate_command",
    }


# --- CLI ---------------------------------------------------------------------


def test_cli_optimize_clean(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["optimize", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Optimization candidates" in result.output


def test_cli_optimize_json(tmp_path: Path) -> None:
    (tmp_path / "job.py").write_text(
        "from pyspark.sql import SparkSession\n"
        "df = SparkSession.builder.getOrCreate().read.parquet('s3://in')\n"
        "df.repartition(1).write.save('/x')\n"
    )
    result = runner.invoke(app, ["optimize", str(tmp_path), "-f", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert any(r["optimization"] == "partition-data" for r in rows)
