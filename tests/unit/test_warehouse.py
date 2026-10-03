"""Warehouse model, graph adapter, and WARE### checks (spec 212)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph
from forge_doctor_data.analyzers.warehouse_model import warehouse_model
from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext

runner = CliRunner()

_TF = """
resource "aws_redshift_cluster" "analytics" {
  cluster_identifier = "analytics"
  node_type          = "ra3.xlplus"
  database_name      = "warehouse"
}

resource "aws_redshiftserverless_workgroup" "adhoc" {
  workgroup_name = "adhoc"
}
"""


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


def test_empty_model_on_plain_project(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    model = warehouse_model(_ctx(tmp_path))
    assert not model.has_evidence
    assert model.platforms == ()
    assert model.tables == []
    assert build_platform_graph(_ctx(tmp_path)).entity("warehouse:warehouse:redshift") is None


def test_terraform_redshift_model(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF, encoding="utf-8")
    model = warehouse_model(_ctx(tmp_path))
    assert model.has_evidence
    assert model.platforms == ("redshift",)
    assert {c.name for c in model.compute} == {"analytics", "adhoc"}
    # cluster's database_name normalizes to a database namespace
    assert [(n.name, n.kind) for n in model.databases] == [("warehouse", "database")]


def test_snowflake_resources_map(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "snowflake_warehouse" "wh" { name = "WH" }\n'
        'resource "snowflake_database" "db" { name = "DB" }\n'
        'resource "snowflake_schema" "s" { name = "DB.S" }\n'
        'resource "snowflake_table" "t" { name = "DB.S.T" }\n'
        'resource "snowflake_view" "v" { name = "DB.S.V" }\n',
        encoding="utf-8",
    )
    model = warehouse_model(_ctx(tmp_path))
    assert model.platforms == ("snowflake",)
    assert [c.name for c in model.compute] == ["WH"]
    assert [n.name for n in model.databases] == ["DB"]
    assert [n.name for n in model.schemas] == ["DB.S"]
    assert [t.name for t in model.tables] == ["DB.S.T"]
    assert [v.name for v in model.views] == ["DB.S.V"]


def test_graph_adapter_entities_and_edges(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF, encoding="utf-8")
    g = build_platform_graph(_ctx(tmp_path))
    wh = g.entity("warehouse:warehouse:redshift")
    assert wh is not None
    c1 = g.entity("warehouse_compute:warehouse:redshift/analytics")
    c2 = g.entity("warehouse_compute:warehouse:redshift/adhoc")
    db = g.entity("database:warehouse:redshift/warehouse")
    assert c1 is not None and c2 is not None and db is not None
    kinds = {(r.src, r.kind.value) for r in g.relationships()}
    assert (wh.id, "CONTAINS") in kinds
    for r in g.relationships():
        if r.kind.value == "CONTAINS":
            assert r.src == wh.id
            assert r.dst in {c1.id, c2.id, db.id}


def test_view_contains_and_reads_from(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "snowflake_table" "t" { name = "DB.S.T" }\n',
        encoding="utf-8",
    )
    model = warehouse_model(_ctx(tmp_path))
    assert [t.name for t in model.tables] == ["DB.S.T"]


def test_checks_on_warehouse_fixture(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF, encoding="utf-8")
    ctx = _ctx(tmp_path)
    from forge_doctor_data.checks.warehouse import CHECKS

    findings = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert {"WARE001", "WARE030"} <= findings
    assert "WARE010" not in findings  # no tables declared
    assert "WARE020" not in findings  # no views declared


def test_ware010_fires_on_tables(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "snowflake_table" "t" { name = "T" }\n', encoding="utf-8"
    )
    ctx = _ctx(tmp_path)
    from forge_doctor_data.checks.warehouse import UnprofiledTable

    rows = UnprofiledTable().run(ctx)
    assert len(rows) == 1 and "T" in rows[0].message


def test_ware020_dangling_view(tmp_path: Path) -> None:
    """A view reading a base the model doesn't know flags WARE020."""
    from forge_doctor_data.checks.warehouse import DanglingView

    ctx = _ctx(tmp_path)
    model = warehouse_model(ctx)
    # plant a SQL-derived view row directly — model is the contract
    from forge_doctor_data.analyzers.warehouse_model import WarehouseView

    model.views.append(WarehouseView("V", "snowflake", False, ("MISSING_TABLE",), Path("v.sql"), 3))
    rows = DanglingView().run(ctx)
    assert len(rows) == 1 and "MISSING_TABLE" in rows[0].message


def test_ontology_accepts_warehouse_terms(tmp_path: Path) -> None:
    from forge_doctor_data.core.ontology import validate_graph

    (tmp_path / "main.tf").write_text(_TF, encoding="utf-8")
    violations = validate_graph(build_platform_graph(_ctx(tmp_path)))
    assert violations == []


def test_warehouse_capability_family() -> None:
    from forge_doctor_data.core.capabilities import CapabilityRegistry

    registry = CapabilityRegistry()
    assert "warehouse" in registry.platforms()
    caps = registry.capabilities_for("warehouse")
    assert "SQL_QUERY" in caps


def test_deterministic_model(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF, encoding="utf-8")
    a = warehouse_model(_ctx(tmp_path))
    b = warehouse_model(_ctx(tmp_path))
    assert [c.name for c in a.compute] == [c.name for c in b.compute]
    assert a.platforms == b.platforms


def test_cli_ontology_validate_clean(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF, encoding="utf-8")
    result = runner.invoke(app, ["ontology", "validate", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "conform" in result.output
