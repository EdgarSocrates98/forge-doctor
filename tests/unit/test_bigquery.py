"""BigQuery adapter: model, checks, CLI, runtime adapter (spec 214)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor_data.analyzers.bigquery_model import bigquery_model
from forge_doctor_data.analyzers.sql_ast import SQLGLOT_AVAILABLE
from forge_doctor_data.checks.bigquery import CHECKS
from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity

runner = CliRunner()
needs_sqlglot = pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


_TF_DS = """
resource "google_bigquery_dataset" "analytics" {
  dataset_id = "analytics"
  location   = "US"
}
"""

_TF_PARTITIONED = (
    _TF_DS
    + """
resource "google_bigquery_table" "events" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  table_id   = "events"

  time_partitioning {
    type  = "DAY"
    field = "event_date"
  }
  clustering = ["user_id"]
}
"""
)


def test_no_evidence_empty(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    assert not bigquery_model(_ctx(tmp_path)).has_evidence


def test_terraform_dataset_and_partitioned_table(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_PARTITIONED, encoding="utf-8")
    model = bigquery_model(_ctx(tmp_path))
    assert model.has_evidence
    assert [d.name for d in model.datasets] == ["analytics"]
    table = next(t for t in model.tables if t.name == "events")
    assert table.attr("partition_by") == "event_date"
    assert "events" in model.partitioned_tables()


@needs_sqlglot
def test_bigquery_ddl_objects(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "CREATE SCHEMA ds OPTIONS(location='US');\n"
        "CREATE TABLE ds.t PARTITION BY DATE(ts) CLUSTER BY a OPTIONS(description='x');\n"
        "CREATE MATERIALIZED VIEW ds.mv AS SELECT a FROM ds.t;\n"
        "CREATE RESERVATION proj.r OPTIONS(slot_capacity=100);\n",
        encoding="utf-8",
    )
    model = bigquery_model(_ctx(tmp_path))
    assert [d.name for d in model.datasets] == ["ds"]
    table = next(t for t in model.tables if t.name == "ds.t")
    assert table.attr("partition_by")
    assert table.attr("cluster_by")
    assert any(v.kind == "materialized_view" and v.name == "ds.mv" for v in model.views)
    assert any(o.kind == "reservation" for o in model.objects)


@needs_sqlglot
def test_generic_sql_not_attributed(tmp_path: Path) -> None:
    """Adversarial: ANSI/spark SQL without vendor markers → no model rows."""
    (tmp_path / "q.sql").write_text(
        "CREATE VIEW v AS SELECT * FROM orders;\nSELECT * FROM staging;\n",
        encoding="utf-8",
    )
    assert not bigquery_model(_ctx(tmp_path)).has_evidence


def test_bq001_large_unpartitioned(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "google_bigquery_table" "fact" { table_id = "fact" }\n', encoding="utf-8"
    )
    e = tmp_path / "bigquery"
    e.mkdir()
    (e / "information_schema_tables.json").write_text(
        json.dumps(
            [
                {
                    "table_name": "fact",
                    "table_schema": "ds",
                    "size_bytes": "5000000000",
                }
            ]
        ),
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "BQ001" in found


@needs_sqlglot
def test_bq002_partitioned_no_filter(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_PARTITIONED, encoding="utf-8")
    (tmp_path / "q.sql").write_text(
        "SELECT user_id FROM `p.analytics.events` ORDER BY 1;\n", encoding="utf-8"
    )
    ctx = _ctx(tmp_path)
    found = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "BQ002"]
    assert found and found[0].severity == Severity.WARNING


@needs_sqlglot
def test_bq002_with_partition_filter_quiet(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_PARTITIONED, encoding="utf-8")
    (tmp_path / "q.sql").write_text(
        "SELECT user_id FROM `p.analytics.events` WHERE event_date > '2026-01-01';\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "BQ002" not in found


def test_bq002_observed_job_is_error(tmp_path: Path) -> None:
    """Spec: authored SQL = warning, observed jobs = error."""
    (tmp_path / "main.tf").write_text(_TF_PARTITIONED, encoding="utf-8")
    e = tmp_path / "bigquery"
    e.mkdir()
    (e / "jobs_by_project.json").write_text(
        json.dumps(
            [
                {
                    "job_id": "j1",
                    "statement_type": "SELECT",
                    "total_slot_ms": "500",
                    "query": "SELECT user_id FROM `p.analytics.events`",
                }
            ]
        ),
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "BQ002"]
    assert found and all(f.severity == Severity.ERROR for f in found)


@needs_sqlglot
def test_bq003_select_star(tmp_path: Path) -> None:
    (tmp_path / "q.sql").write_text("SELECT * FROM `p.ds.t`;\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "BQ003" in found


def test_bq004_public_access(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        _TF_DS + 'resource "google_bigquery_dataset_access" "pub" {\n'
        '  dataset_id   = "analytics"\n'
        '  special_group = "allAuthorizedUsers"\n'
        "}\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "BQ004"]
    assert found and any(f.severity == Severity.ERROR for f in found)


def test_bq004_authorized_view_undocumented(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "google_bigquery_dataset_access" "av" {\n'
        '  dataset_id = "analytics"\n'
        "  view {\n"
        '    project_id = "other"\n'
        '    dataset_id = "shared"\n'
        '    table_id   = "v"\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "BQ004"]
    assert found and any(f.severity == Severity.WARNING for f in found)


@needs_sqlglot
def test_bq005_mv_over_mutable_base(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "CREATE MATERIALIZED VIEW ds.mv AS SELECT a FROM ds.t;\nINSERT INTO `p.ds.t` VALUES (1);\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "BQ005" in found


@needs_sqlglot
def test_bq005_mv_with_staleness_quiet(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "CREATE MATERIALIZED VIEW ds.mv OPTIONS(max_staleness=INTERVAL 4 HOUR) "
        "AS SELECT a FROM ds.t;\n"
        "INSERT INTO `p.ds.t` VALUES (1);\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "BQ005" not in found


def test_shared_evidence_dir_claim_discipline(tmp_path: Path) -> None:
    """Generic rows in .forge-doctor-data/evidence/ without BQ fields stay unclaimed."""
    e = tmp_path / ".forge-doctor-data" / "evidence"
    e.mkdir(parents=True)
    (e / "rows.json").write_text(
        json.dumps([{"name": "x", "kind": "TABLE", "notes": "generic"}]), encoding="utf-8"
    )
    model = bigquery_model(_ctx(tmp_path))
    assert not model.observed


def test_observed_exports_and_unparsed(tmp_path: Path) -> None:
    e = tmp_path / "bigquery"
    e.mkdir()
    (e / "odd.json").write_text('{"weird": {"nested": true}}', encoding="utf-8")
    model = bigquery_model(_ctx(tmp_path))
    assert "bigquery/odd.json" in model.unparsed


def test_jobs_runtime_adapter(tmp_path: Path) -> None:
    from forge_doctor_data.analyzers.runtime_evidence import ingest_artifact

    f = tmp_path / "jobs_by_project.json"
    f.write_text(
        json.dumps(
            [
                {
                    "job_id": "j1",
                    "state": "DONE",
                    "statement_type": "SELECT",
                    "total_bytes_processed": "1024",
                    "total_slot_ms": "500",
                }
            ]
        ),
        encoding="utf-8",
    )
    model = ingest_artifact(f)
    assert model.source == "bigquery_jobs"
    assert model.executions and model.executions[0].id == "j1"
    assert any(m.name == "slot_ms" for m in model.metrics)


def test_capability_pack_registers() -> None:
    from forge_doctor_data.core.capabilities import CapabilityRegistry

    reg = CapabilityRegistry()
    assert "bigquery" in reg.platforms()
    assert "PARTITIONING" in reg.capabilities_for("bigquery")


def test_cli_inspect(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_PARTITIONED, encoding="utf-8")
    result = runner.invoke(app, ["bigquery", "inspect", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "BigQuery environment" in result.output
    assert "analytics" in result.output
    assert "events" in result.output


def test_warehouse_model_merges_bigquery(tmp_path: Path) -> None:
    from forge_doctor_data.analyzers.warehouse_model import warehouse_model

    (tmp_path / "main.tf").write_text(_TF_PARTITIONED, encoding="utf-8")
    model = warehouse_model(_ctx(tmp_path))
    assert "bigquery" in model.platforms
    assert any(n.name == "analytics" for n in model.schemas)


def test_warehouse_graph_entities(tmp_path: Path) -> None:
    from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph

    (tmp_path / "main.tf").write_text(_TF_PARTITIONED, encoding="utf-8")
    g = build_platform_graph(_ctx(tmp_path))
    assert g.entity("warehouse:warehouse:bigquery") is not None
    assert g.entity("table:warehouse:bigquery/events") is not None
