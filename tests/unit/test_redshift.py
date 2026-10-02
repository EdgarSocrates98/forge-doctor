"""Redshift adapter: model, checks, CLI, runtime adapter (spec 215)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor.analyzers.redshift_model import redshift_model
from forge_doctor.analyzers.sql_ast import SQLGLOT_AVAILABLE
from forge_doctor.checks.redshift import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity

runner = CliRunner()
needs_sqlglot = pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


_TF_CLUSTER = """
resource "aws_redshift_cluster" "analytics" {
  cluster_identifier  = "analytics"
  node_type           = "ra3.xlplus"
  database_name       = "analytics_db"
  publicly_accessible = true
  encrypted           = false
}
"""

_TF_PRIVATE = """
resource "aws_redshift_cluster" "analytics" {
  cluster_identifier  = "analytics"
  node_type           = "ra3.xlplus"
  database_name       = "analytics_db"
  publicly_accessible = false
  encrypted           = true
}
"""


def test_no_evidence_empty(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    assert not redshift_model(_ctx(tmp_path)).has_evidence


def test_terraform_cluster_attrs(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_CLUSTER, encoding="utf-8")
    model = redshift_model(_ctx(tmp_path))
    assert model.has_evidence
    cluster = next(c for c in model.compute if c.kind == "cluster")
    assert cluster.name == "analytics"
    assert cluster.attr("node_type") == "ra3.xlplus"
    assert cluster.attr("publicly_accessible").lower() == "true"
    assert any(n.name == "analytics_db" and n.kind == "database" for n in model.namespaces)
    assert model.ato_available()


@needs_sqlglot
def test_redshift_ddl_objects(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "CREATE TABLE fact (id INT, ts DATE) DISTKEY(id) SORTKEY(ts);\n"
        "CREATE TABLE dim (id INT) DISTSTYLE ALL;\n"
        "CREATE EXTERNAL SCHEMA spectrum_schema FROM DATA CATALOG DATABASE 'db';\n"
        "CREATE MATERIALIZED VIEW mv AS SELECT id FROM fact;\n",
        encoding="utf-8",
    )
    model = redshift_model(_ctx(tmp_path))
    fact = next(t for t in model.tables if t.name == "fact")
    assert fact.attr("distkey") == "id"
    assert fact.attr("sortkey") == "ts"
    dim = next(t for t in model.tables if t.name == "dim")
    assert dim.attr("diststyle") == "all"
    assert any(n.kind == "external_schema" for n in model.namespaces)
    assert any(v.kind == "materialized_view" for v in model.views)


@needs_sqlglot
def test_postgres_sql_not_attributed(tmp_path: Path) -> None:
    """Adversarial: Postgres DDL/maintenance must not fire RS checks."""
    (tmp_path / "q.sql").write_text(
        "CREATE TABLE users (id SERIAL PRIMARY KEY);\nVACUUM users;\nANALYZE;\n",
        encoding="utf-8",
    )
    model = redshift_model(_ctx(tmp_path))
    assert not model.has_evidence
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx) if f.severity != Severity.PASS}
    assert not found


def test_rs004_public_unencrypted(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_CLUSTER, encoding="utf-8")
    found = [f for c in CHECKS for f in c.run(_ctx(tmp_path)) if f.check_id == "RS004"]
    assert found and found[0].severity == Severity.ERROR


def test_rs004_private_quiet(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_PRIVATE, encoding="utf-8")
    found = {f.check_id for c in CHECKS for f in c.run(_ctx(tmp_path))}
    assert "RS004" not in found


@needs_sqlglot
def test_rs001_even_joined(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "CREATE TABLE fact (id INT) DISTSTYLE EVEN;\n"
        "SELECT f.id FROM fact f JOIN dim d ON f.id = d.id;\n",
        encoding="utf-8",
    )
    e = tmp_path / "redshift"
    e.mkdir()
    (e / "svv_table_info.json").write_text(
        json.dumps(
            [{"table": "fact", "size": "5000", "diststyle": "EVEN", "tbl_rows": "50000000"}]
        ),
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "RS001"]
    assert found
    assert found[0].evidence_kind.value == "observed_metadata"


@needs_sqlglot
def test_rs001_small_table_quiet(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "CREATE TABLE fact (id INT) DISTSTYLE EVEN;\n"
        "SELECT f.id FROM fact f JOIN dim d ON f.id = d.id;\n",
        encoding="utf-8",
    )
    e = tmp_path / "redshift"
    e.mkdir()
    (e / "svv_table_info.json").write_text(
        json.dumps([{"table": "fact", "size": "1", "diststyle": "EVEN", "tbl_rows": "10"}]),
        encoding="utf-8",
    )
    found = {f.check_id for c in CHECKS for f in c.run(_ctx(tmp_path))}
    assert "RS001" not in found


@needs_sqlglot
def test_rs002_unsorted_range_filter(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "CREATE TABLE events (id INT, ts DATE) DISTSTYLE EVEN;\n"
        "SELECT id FROM events WHERE ts >= '2026-01-01';\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "RS002" in found


def test_rs003_ato_off_with_skew(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        _TF_PRIVATE + 'resource "aws_redshift_parameter_group" "pg" {\n'
        '  name = "pg"\n'
        "  parameter {\n"
        '    name  = "auto_analyze"\n'
        '    value = "false"\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    e = tmp_path / "redshift"
    e.mkdir()
    (e / "svv_table_info.json").write_text(
        json.dumps([{"table": "t", "skew_rows": "0.7", "size": "5"}]), encoding="utf-8"
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "RS003" in found


@needs_sqlglot
def test_rs005_manual_vacuum_with_ato(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_PRIVATE, encoding="utf-8")
    (tmp_path / "maint.sql").write_text(
        "CREATE TABLE t (id INT) DISTSTYLE EVEN;\nVACUUM t;\n", encoding="utf-8"
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "RS005" in found


@needs_sqlglot
def test_rs005_no_compute_quiet(tmp_path: Path) -> None:
    """No compute evidence -> ATO availability unknown -> no finding."""
    (tmp_path / "maint.sql").write_text(
        "CREATE TABLE t (id INT) DISTSTYLE EVEN;\nVACUUM t;\n", encoding="utf-8"
    )
    found = {f.check_id for c in CHECKS for f in c.run(_ctx(tmp_path))}
    assert "RS005" not in found


def test_shared_evidence_dir_claim_discipline(tmp_path: Path) -> None:
    e = tmp_path / ".forge-doctor" / "evidence"
    e.mkdir(parents=True)
    (e / "rows.json").write_text(
        json.dumps([{"name": "x", "kind": "TABLE", "notes": "generic"}]), encoding="utf-8"
    )
    model = redshift_model(_ctx(tmp_path))
    assert not model.observed


def test_observed_exports_and_unparsed(tmp_path: Path) -> None:
    e = tmp_path / "redshift"
    e.mkdir()
    (e / "odd.json").write_text('{"weird": {"nested": true}}', encoding="utf-8")
    model = redshift_model(_ctx(tmp_path))
    assert "redshift/odd.json" in model.unparsed


def test_stl_query_runtime_adapter(tmp_path: Path) -> None:
    from forge_doctor.analyzers.runtime_evidence import ingest_artifact

    f = tmp_path / "stl_query.json"
    f.write_text(
        json.dumps(
            [
                {
                    "query": "1001",
                    "querytxt": "select 1",
                    "total_exec_time": "1200",
                    "service_class": "6",
                    "aborted": "0",
                }
            ]
        ),
        encoding="utf-8",
    )
    model = ingest_artifact(f)
    assert model.source == "redshift_query_log"
    assert model.executions and model.executions[0].id == "1001"
    assert any(m.name == "execution_time" for m in model.metrics)


def test_capability_pack_registers() -> None:
    from forge_doctor.core.capabilities import CapabilityRegistry

    reg = CapabilityRegistry()
    assert "redshift" in reg.platforms()
    assert "SPECTRUM_EXTERNAL" in reg.capabilities_for("redshift")


def test_cli_inspect(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_CLUSTER, encoding="utf-8")
    result = runner.invoke(app, ["redshift", "inspect", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Redshift environment" in result.output
    assert "analytics" in result.output


def test_warehouse_model_merges_redshift(tmp_path: Path) -> None:
    from forge_doctor.analyzers.warehouse_model import warehouse_model

    (tmp_path / "main.tf").write_text(_TF_PRIVATE, encoding="utf-8")
    model = warehouse_model(_ctx(tmp_path))
    assert "redshift" in model.platforms
    assert any(c.name == "analytics" for c in model.compute)


def test_warehouse_graph_entities(tmp_path: Path) -> None:
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    (tmp_path / "main.tf").write_text(_TF_PRIVATE, encoding="utf-8")
    g = build_platform_graph(_ctx(tmp_path))
    assert g.entity("warehouse:warehouse:redshift") is not None
    assert g.entity("warehouse_compute:warehouse:redshift/analytics") is not None
