"""Snowflake adapter: model, checks, CLI, runtime adapter (spec 213)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor.analyzers.snowflake_model import snowflake_model
from forge_doctor.analyzers.sql_ast import SQLGLOT_AVAILABLE
from forge_doctor.checks.snowflake import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext

runner = CliRunner()
needs_sqlglot = pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


_TF_WH = """
resource "snowflake_warehouse" "analytics" {
  name           = "ANALYTICS_WH"
  warehouse_size = "LARGE"
}
"""


def test_no_evidence_empty(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    assert not snowflake_model(_ctx(tmp_path)).has_evidence


def test_terraform_warehouse_attrs(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_WH, encoding="utf-8")
    model = snowflake_model(_ctx(tmp_path))
    assert model.has_evidence
    assert [w.name for w in model.warehouses] == ["ANALYTICS_WH"]
    w = model.warehouses[0]
    assert w.attr("warehouse_size") == "LARGE"
    assert w.attr("auto_suspend") == ""  # absent


@needs_sqlglot
def test_snowflake_ddl_objects(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "CREATE WAREHOUSE wh WAREHOUSE_SIZE='LARGE' AUTO_SUSPEND=300 AUTO_RESUME=TRUE;\n"
        "CREATE DATABASE db;\n"
        "CREATE STAGE st URL='s3://b/p' STORAGE_INTEGRATION=si;\n"
        "CREATE PIPE p AS COPY INTO t FROM @st;\n"
        "CREATE STREAM s ON TABLE t;\n"
        "CREATE TASK tk WAREHOUSE=wh AS SELECT 1;\n",
        encoding="utf-8",
    )
    model = snowflake_model(_ctx(tmp_path))
    assert [w.name for w in model.warehouses] == ["wh"]
    assert model.warehouses[0].attr("auto_suspend") == "300"
    kinds = {o.kind for o in model.objects}
    assert {"stage", "pipe", "stream", "task"} <= kinds
    assert ("db", "database") in {(n.name, n.kind) for n in model.namespaces}
    assert model.copies and model.copies[0].stage_ref.startswith("@")


@needs_sqlglot
def test_generic_sql_not_attributed(tmp_path: Path) -> None:
    """Adversarial: ANSI/spark SQL without vendor markers → no model rows."""
    (tmp_path / "q.sql").write_text(
        "CREATE VIEW v AS SELECT * FROM orders;\nSELECT * FROM staging;\n",
        encoding="utf-8",
    )
    model = snowflake_model(_ctx(tmp_path))
    assert not model.has_evidence


@needs_sqlglot
def test_snow001_missing_auto_suspend(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_WH, encoding="utf-8")
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "SNOW001" in found
    assert "SNOW002" not in found  # no auto_suspend -> asymmetry doesn't apply


@needs_sqlglot
def test_snow002_asymmetric_resume(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text("CREATE WAREHOUSE wh AUTO_SUSPEND=60;\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "SNOW002" in found
    assert "SNOW001" not in found


@needs_sqlglot
def test_snow004_insecure_stage(tmp_path: Path) -> None:
    (tmp_path / "load.sql").write_text(
        "CREATE STAGE public_stage URL='s3://open';\nCOPY INTO orders FROM @public_stage;\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "SNOW004" in found


@needs_sqlglot
def test_snow004_secure_stage_quiet(tmp_path: Path) -> None:
    (tmp_path / "load.sql").write_text(
        "CREATE STAGE st URL='s3://b' STORAGE_INTEGRATION=si;\nCOPY INTO orders FROM @st;\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "SNOW004" not in found


@needs_sqlglot
def test_snow005_wildcard_in_view(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "CREATE WAREHOUSE wh AUTO_SUSPEND=60;\nCREATE VIEW v AS SELECT * FROM orders;\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "SNOW005" in found


@needs_sqlglot
def test_snow005_clean_without_marker(tmp_path: Path) -> None:
    """Same DDL without snowflake markers -> SNOW005 stays silent."""
    (tmp_path / "ddl.sql").write_text("CREATE VIEW v AS SELECT * FROM orders;\n", encoding="utf-8")
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "SNOW005" not in found


def test_observed_exports_and_unparsed(tmp_path: Path) -> None:
    export = tmp_path / "snowflake" / "tables.json"
    export.parent.mkdir(parents=True)
    export.write_text(
        json.dumps(
            [
                {
                    "TABLE_NAME": "FACT",
                    "TABLE_SCHEMA": "DB",
                    "ROW_COUNT": "5000000000",
                    "BYTES": "2000000000",
                    "CLUSTERING_KEY": "",
                }
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "snowflake" / "odd.json").write_text('{"weird": {"nested": true}}')
    model = snowflake_model(_ctx(tmp_path))
    assert any(r.shape == "tables" for r in model.observed)
    assert "snowflake/odd.json" in model.unparsed


def test_snow003_large_unclustered(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "snowflake_table" "fact" { name = "FACT" }\n', encoding="utf-8"
    )
    e = tmp_path / "information_schema_tables.json"
    e.write_text(json.dumps([{"table_name": "FACT", "bytes": "5000000000", "clustering_key": ""}]))
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "SNOW003" in found


def test_query_history_runtime_adapter(tmp_path: Path) -> None:
    from forge_doctor.analyzers.runtime_evidence import ingest_artifact

    f = tmp_path / "query_history.json"
    f.write_text(
        json.dumps(
            [
                {
                    "QUERY_ID": "q1",
                    "QUERY_TEXT": "select 1",
                    "WAREHOUSE_NAME": "WH",
                    "EXECUTION_TIME": "1200",
                    "BYTES_SCANNED": "4096",
                }
            ]
        )
    )
    model = ingest_artifact(f)
    assert model.source == "snowflake_query_history"
    assert model.executions and model.executions[0].id == "q1"
    assert any(m.name == "execution_time" for m in model.metrics)


def test_capability_pack_registers() -> None:
    from forge_doctor.core.capabilities import CapabilityRegistry

    reg = CapabilityRegistry()
    assert "snowflake" in reg.platforms()
    assert "TIME_TRAVEL" in reg.capabilities_for("snowflake")


def test_cli_inspect(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_TF_WH, encoding="utf-8")
    result = runner.invoke(app, ["snowflake", "inspect", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Snowflake environment" in result.output
    assert "ANALYTICS_WH" in result.output


def test_warehouse_model_merges_snowflake(tmp_path: Path) -> None:
    from forge_doctor.analyzers.warehouse_model import warehouse_model

    (tmp_path / "main.tf").write_text(_TF_WH, encoding="utf-8")
    model = warehouse_model(_ctx(tmp_path))
    assert "snowflake" in model.platforms
    assert any(c.name == "ANALYTICS_WH" for c in model.compute)


def test_warehouse_graph_entities(tmp_path: Path) -> None:
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    (tmp_path / "main.tf").write_text(_TF_WH, encoding="utf-8")
    g = build_platform_graph(_ctx(tmp_path))
    assert g.entity("warehouse:warehouse:snowflake") is not None
    assert g.entity("warehouse_compute:warehouse:snowflake/ANALYTICS_WH") is not None
