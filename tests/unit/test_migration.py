"""Cross-platform migration: plan builder, MIGR findings, CLI (spec 224)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.crossmigration import (
    PlatformMigrationPlan,
    _migr_findings,
    plan_platform_migration,
)
from forge_doctor_data.core.whatif import evaluate_change, parse_change

runner = CliRunner()

_LAB = Path("labs/migration/snowflake-to-bigquery")


def _ctx(path: Path) -> ProjectContext:
    return ProjectContext(root=path.resolve())


def _snowflake_dir(tmp_path: Path) -> Path:
    (tmp_path / "main.tf").write_text(
        'resource "snowflake_warehouse" "wh" {\n  name = "WH1"\n}\n',
        encoding="utf-8",
    )
    (tmp_path / "ddl.sql").write_text(
        "CREATE STAGE stg URL='s3://b/x';\n"
        "CREATE STREAM st ON TABLE t1;\n"
        "CREATE TASK tk WAREHOUSE=WH1 SCHEDULE='60 MINUTE' AS SELECT 1;\n",
        encoding="utf-8",
    )
    return tmp_path


def test_plan_maps_snowflake_entities() -> None:
    plan = plan_platform_migration(_ctx(_LAB), "snowflake", "bigquery")
    assert plan.entity_map
    by_src = {m.source: m for m in plan.entity_map}
    assert by_src["snowflake:ANALYTICS_WH"].target_service == "bigquery"
    stage = [m for m in plan.entity_map if m.abstraction == "object_storage"]
    assert stage and stage[0].target_service == "gcs"
    stream = [m for m in plan.entity_map if m.abstraction == "stream"]
    assert stream and stream[0].target_service == "pubsub"


def test_capability_deltas() -> None:
    plan = plan_platform_migration(_ctx(_LAB), "snowflake", "bigquery")
    deltas = {d.capability: d for d in plan.capability_deltas}
    assert deltas["TIME_TRAVEL"].delta == "equivalent"
    assert deltas["ZERO_COPY_CLONE"].delta == "review"  # bq has no clone entry
    assert deltas["BIGLAKE_EXTERNAL"].delta == "gained"


def test_stages_ordered() -> None:
    plan = plan_platform_migration(_ctx(_LAB), "snowflake", "bigquery")
    stages = [s.stage for s in plan.stages]
    assert stages == sorted(stages, key=["catalog", "schema", "data", "compute", "consumers"].index)
    assert "schema" in stages and "compute" in stages


def test_migr002_reverse_direction(tmp_path: Path) -> None:
    """Reverse direction: bigquery-only facts surface as review, not lost
    (snowflake pack declares no explicit unsupported for bq-only caps)."""
    (tmp_path / "main.tf").write_text(
        'resource "google_bigquery_dataset" "dw" {\n  dataset_id = "dw"\n}\n',
        encoding="utf-8",
    )
    plan = plan_platform_migration(_ctx(tmp_path), "bigquery", "snowflake")
    deltas = {d.capability: d for d in plan.capability_deltas}
    assert deltas["BIGLAKE_EXTERNAL"].delta == "review"
    assert any(f.check_id == "MIGR002" for f in plan.findings)


def test_migr001_unmapped_service() -> None:
    plan = PlatformMigrationPlan(source="x", target="snowflake")
    from forge_doctor_data.core.crossmigration import EntityMapping

    plan.entity_map.append(
        EntityMapping(
            source="cosmosdb:db1",
            abstraction="operational_store",
            target_service="",
            note="no equivalent",
            confidence="low",
        )
    )
    findings = _migr_findings(plan)
    assert any(f.check_id == "MIGR001" and "operational_store" in f.message for f in findings)


def test_migr003_unmapped_consumers() -> None:
    plan = PlatformMigrationPlan(source="x", target="bigquery")
    plan.unmapped_consumers.append("dataset:bi:dashboard_d1")
    findings = _migr_findings(plan)
    assert any(f.check_id == "MIGR003" for f in findings)


def test_plan_empty_source(tmp_path: Path) -> None:
    plan = plan_platform_migration(_ctx(tmp_path), "snowflake", "bigquery")
    assert not plan.entity_map


def test_migrate_plan_cli() -> None:
    result = runner.invoke(
        app,
        ["migrate", "plan", "--from", "snowflake", "--to", "bigquery", str(_LAB)],
    )
    assert result.exit_code == 0
    assert "ANALYTICS_WH" in result.output
    assert "pubsub" in result.output
    assert "MIGR002" in result.output


def test_migrate_plan_json() -> None:
    result = runner.invoke(
        app,
        [
            "migrate",
            "plan",
            "--from",
            "snowflake",
            "--to",
            "bigquery",
            "-f",
            "json",
            str(_LAB),
        ],
    )
    assert result.exit_code == 0
    doc = json.loads(result.output)
    assert doc["source"] == "snowflake" and doc["target"] == "bigquery"
    assert doc["entity_map"] and doc["capability_deltas"]
    assert any(f["check_id"] == "MIGR002" for f in doc["findings"])


def test_whatif_platform_change() -> None:
    ch = parse_change("platform=bigquery")
    report = evaluate_change(_ctx(_LAB), ch)
    assert report.change.from_ == "snowflake"  # detected source
    assert report.affected_entities
    assert "TIME_TRAVEL" not in report.unsupported_now
    assert any("semantic review" in i.detail for i in report.impacts)


def test_whatif_platform_alias(tmp_path: Path) -> None:
    _snowflake_dir(tmp_path)
    ch = parse_change("warehouse=redshift")
    report = evaluate_change(_ctx(tmp_path), ch)
    assert report.change.to == "redshift"
    assert report.affected_entities


def test_migrate_plan_legacy_still_works(tmp_path: Path) -> None:
    result = runner.invoke(app, ["migrate", "plan", str(tmp_path)])
    assert result.exit_code == 0
    assert "no applicable migration" in result.output
