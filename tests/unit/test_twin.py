"""Formal digital twin: invariant validation, snapshot, CLI (spec 227)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor_data.cli.app import app
from forge_doctor_data.core.models import CheckResult, ScanReport, Severity
from forge_doctor_data.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)
from forge_doctor_data.core.twin import build_twin, twin_snapshot, validate_twin

runner = CliRunner()


def _graph() -> DataPlatformGraph:
    g = DataPlatformGraph()
    job = Entity(
        kind=EntityKind.COMPUTE_JOB,
        domain="glue",
        identifier="etl",
        file=Path("jobs.py"),
        line=3,
    )
    table = Entity(kind=EntityKind.TABLE, domain="dynamodb", identifier="orders")
    g.add_entity(job)
    g.add_entity(table)
    g.add_relationship(Relationship(src=job.id, dst=table.id, kind=RelKind.WRITES))
    return g


# --- invariants ----------------------------------------------------------------


def test_clean_graph_passes_all_invariants() -> None:
    rep = validate_twin(_graph())
    assert rep.ok
    assert rep.entity_count == 2 and rep.relationship_count == 1
    assert dict(rep.entities_by_kind) == {"compute_job": 1, "table": 1}


def test_i1_detects_dangling_endpoint() -> None:
    g = _graph()
    # Simulate a corrupted/deserialized graph: drop the dst entity.
    del g._entities["table:dynamodb:orders"]
    rep = validate_twin(g)
    assert not rep.ok
    assert any(v.invariant == "I1" and "table:dynamodb:orders" in v.detail for v in rep.violations)


def test_i2_detects_malformed_id() -> None:
    g = _graph()

    class _FakeEntity:
        id = "not-a-canonical-id"
        kind = EntityKind.TABLE
        domain = "dynamodb"
        identifier = "x"
        name = ""
        file = None
        line = None
        attrs: tuple = ()

    g._entities["not-a-canonical-id"] = _FakeEntity()
    rep = validate_twin(g)
    assert any(v.invariant == "I2" for v in rep.violations)


def test_i3_detects_unknown_evidence_plane() -> None:
    class _BadPlane:
        value = "bogus_plane"

    g = _graph()
    rel = next(iter(g.relationships()))
    g._edges.discard(rel)
    broken = Relationship(
        src=rel.src,
        dst=rel.dst,
        kind=rel.kind,
        evidence_kind=_BadPlane(),  # type: ignore[arg-type]
    )
    g._edges.add(broken)
    rep = validate_twin(g)
    assert any(v.invariant == "I3" and "bogus_plane" in v.detail for v in rep.violations)


def test_i4_detects_finding_file_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()
    report = ScanReport(
        version="3.0",
        project=root,
        results=[
            CheckResult(
                check_id="X001",
                title="t",
                severity=Severity.WARNING,
                category="cat",
                message="m",
                file=outside,
            )
        ],
    )
    rep = validate_twin(_graph(), report, root)
    assert any(v.invariant == "I4" for v in rep.violations)


def test_i4_passes_for_files_inside_root(tmp_path: Path) -> None:
    (tmp_path / "jobs.py").write_text("x = 1\n", encoding="utf-8")
    report = ScanReport(
        version="3.0",
        project=tmp_path,
        results=[
            CheckResult(
                check_id="X001",
                title="t",
                severity=Severity.WARNING,
                category="cat",
                message="m",
                file=tmp_path / "jobs.py",
            )
        ],
    )
    rep = validate_twin(_graph(), report, tmp_path)
    assert not any(v.invariant == "I4" for v in rep.violations)


def test_i5_reports_attr_gaps() -> None:
    rep = validate_twin(_graph())
    gaps = {g.domain: g for g in rep.attr_gaps}
    assert gaps["dynamodb"].missing_file == 1
    assert gaps["glue"].missing_file == 0
    assert gaps["glue"].missing_line == 0


def test_ontology_violations_fold_in() -> None:
    g = DataPlatformGraph()
    g.add_entity(Entity(kind=EntityKind.TABLE, domain="bogus-domain", identifier="x"))
    rep = validate_twin(g)
    assert any(v.invariant == "ontology" and "bogus-domain" in v.detail for v in rep.violations)


# --- snapshot ------------------------------------------------------------------


def test_snapshot_deterministic_and_shape() -> None:
    twin = build_twin(_graph())
    a = json.dumps(twin_snapshot(twin), sort_keys=True)
    b = json.dumps(twin_snapshot(build_twin(_graph())), sort_keys=True)
    assert a == b
    snap = json.loads(a)
    assert snap["invariants_ok"] is True
    assert {"entities", "relationships"} <= set(snap)


# --- CLI -----------------------------------------------------------------------


def test_cli_twin_inspect(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["twin", "inspect", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "invariants hold" in result.output


def test_cli_twin_inspect_json(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["twin", "inspect", str(tmp_path), "-f", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["entities"] >= 1


def test_cli_twin_export(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    out = tmp_path / "twin.json"
    result = runner.invoke(app, ["twin", "export", str(tmp_path), "-o", str(out)])
    assert result.exit_code == 0, result.output
    snap = json.loads(out.read_text())
    assert "entities" in snap and "invariants_ok" in snap
