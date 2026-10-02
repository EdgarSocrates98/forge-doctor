"""Spec-234 tests: ontology-driven concept mapping, lossiness, readiness,
SQL portability findings (SQLPORT001-7), schema compatibility, and the
`migrate explain` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.crossmigration import plan_platform_migration
from forge_doctor.core.migration_v2 import (
    Lossiness,
    MappingKind,
    ReadinessStatus,
    assess_readiness,
    concept_for_abstraction,
    concept_implementations,
    map_service,
    platform_kind_for,
)
from forge_doctor.core.platform_ontology import PlatformKind
from forge_doctor.core.schema_compat import Compatibility, map_type
from forge_doctor.core.sql_portability import (
    analyze_sql_portability,
    dialect_capabilities,
    known_dialects,
)

runner = CliRunner()


def _ctx(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return ProjectContext(root=tmp_path)


# --- concept registry -----------------------------------------------------


def test_concept_registry_covers_abstractions() -> None:
    for ab in (
        "object_storage",
        "stream",
        "compute_engine",
        "catalog",
        "operational_store",
        "warehouse",
        "orchestrator",
    ):
        assert concept_for_abstraction(ab), ab
        assert concept_implementations(concept_for_abstraction(ab))


def test_platform_kind_resolution() -> None:
    assert platform_kind_for("kinesis") == PlatformKind.STREAM
    assert platform_kind_for("columnar_mpp_warehouse") == PlatformKind.WAREHOUSE
    assert platform_kind_for("not-a-service") is None


def test_map_direct_when_no_gaps(tmp_path: Path) -> None:
    c = map_service("databricks", "databricks")
    assert c.mapping == MappingKind.DIRECT
    assert c.lossiness == Lossiness.LOSSLESS


def test_map_approximate_semantic_gap() -> None:
    c = map_service("kinesis", "eventhubs", abstraction="stream")
    assert c.logical_concept == "managed_stream"
    assert c.mapping == MappingKind.APPROXIMATE
    assert c.lossiness in (Lossiness.SEMANTIC_CHANGE, Lossiness.UNKNOWN)


def test_map_no_equivalent_when_target_missing() -> None:
    c = map_service("kinesis", "not-a-real-service", abstraction="stream")
    assert c.mapping == MappingKind.NO_EQUIVALENT
    assert c.lossiness == Lossiness.MANUAL_REDESIGN


def test_map_unknown_service() -> None:
    c = map_service("mystery-svc", "pubsub", abstraction="")
    assert c.mapping == MappingKind.UNKNOWN
    assert c.lossiness == Lossiness.UNKNOWN
    assert c.missing_evidence


def test_map_deterministic() -> None:
    a = map_service("dynamodb", "bigtable", abstraction="operational_store")
    b = map_service("dynamodb", "bigtable", abstraction="operational_store")
    assert a == b


# --- readiness ------------------------------------------------------------


def test_readiness_blocked_on_no_equivalent() -> None:
    c = map_service("kinesis", "", abstraction="stream")
    r = assess_readiness([c], [])
    assert r.status == ReadinessStatus.BLOCKED


def test_readiness_insufficient_when_empty() -> None:
    r = assess_readiness([], [])
    assert r.status == ReadinessStatus.INSUFFICIENT_EVIDENCE
    assert r.required_evidence


def test_readiness_unknown_budget() -> None:
    c = map_service("mystery", "pubsub")
    r = assess_readiness([c], [])
    assert r.unknown_count >= 1
    assert "mystery" in r.unknowns[0]


# --- scenario-level plans --------------------------------------------------

_AWS_ESTATE = """
resource "aws_s3_bucket" "raw" { bucket = "raw" }
resource "aws_kinesis_stream" "events" { name = "events" }
resource "aws_dynamodb_table" "t" { name = "orders" }
resource "aws_glue_job" "etl" { name = "etl" }
"""


def test_plan_aws_to_gcp(tmp_path: Path) -> None:
    plan = plan_platform_migration(_ctx(tmp_path, {"main.tf": _AWS_ESTATE}), "aws", "gcp")
    by_src = {c.source_implementation: c for c in plan.concepts}
    assert by_src["kinesis"].target_implementation == "pubsub"
    assert by_src["s3"].target_implementation == "gcs"
    assert by_src["glue"].target_implementation == "dataproc"
    assert plan.readiness is not None
    assert plan.readiness.status != ReadinessStatus.INSUFFICIENT_EVIDENCE


def test_plan_aws_to_azure(tmp_path: Path) -> None:
    plan = plan_platform_migration(_ctx(tmp_path, {"main.tf": _AWS_ESTATE}), "aws", "azure")
    by_src = {c.source_implementation: c for c in plan.concepts}
    assert by_src["s3"].target_implementation == "adls_gen2"
    assert by_src["kinesis"].target_implementation == "eventhubs"
    assert by_src["dynamodb"].target_implementation == "cosmosdb"


def test_plan_azure_to_gcp(tmp_path: Path) -> None:
    tf = (
        'resource "azurerm_storage_account" "a" { name = "st" }\n'
        'resource "azurerm_eventhub" "h" { name = "h" }\n'
    )
    plan = plan_platform_migration(_ctx(tmp_path, {"main.tf": tf}), "azure", "gcp")
    by_src = {c.source_implementation: c for c in plan.concepts}
    assert by_src["storage_account"].target_implementation == "gcs"
    assert by_src["eventhubs"].target_implementation == "pubsub"


def test_plan_warehouse_scenarios(tmp_path: Path) -> None:
    tf = 'resource "aws_redshift_cluster" "dw" { cluster_identifier = "dw" }\n'
    for target in ("snowflake", "bigquery"):
        plan = plan_platform_migration(_ctx(tmp_path / target, {"main.tf": tf}), "aws", target)
        wh = [c for c in plan.concepts if c.logical_concept == "columnar_mpp_warehouse"]
        assert wh and wh[0].target_implementation == target


# --- migrate explain CLI ---------------------------------------------------


def test_migrate_explain_cli(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_AWS_ESTATE)
    result = runner.invoke(
        app,
        ["migrate", "explain", "--from", "aws", "--to", "gcp", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "kinesis -> pubsub" in result.output
    assert "concept=managed_stream" in result.output
    assert "readiness:" in result.output


def test_migrate_explain_json(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_AWS_ESTATE)
    result = runner.invoke(
        app,
        ["migrate", "explain", "--from", "aws", "--to", "gcp", "--format", "json", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["readiness"]["status"] in {
        "ready",
        "partial",
        "blocked",
        "insufficient_evidence",
    }
    assert payload["concepts"][0]["mapping"] in {
        "direct",
        "approximate",
        "redesign_required",
        "no_equivalent",
        "unknown",
    }


def test_migrate_plan_json_still_compatible(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_AWS_ESTATE)
    result = runner.invoke(
        app,
        ["migrate", "plan", "--from", "aws", "--to", "gcp", "--format", "json", str(tmp_path)],
    )
    assert result.exit_code in (0, 1)
    payload = json.loads(result.output)
    assert "entity_map" in payload and "capability_deltas" in payload
    assert "concepts" in payload and "readiness" in payload


# --- SQL portability -------------------------------------------------------


def test_dialect_registry() -> None:
    for d in ("snowflake", "bigquery", "redshift", "trino", "spark", "databricks", "clickhouse"):
        assert dialect_capabilities(d), d
    assert len(known_dialects()) == 7


def test_sqlport_qualify_and_functions() -> None:
    sql = (
        "select *, row_number() over (partition by k order by ts desc) rn\n"
        "from t\nqualify rn = 1;\n"
        "select iff(a > 0, 'p', 'n') from t;\n"
    )
    out = analyze_sql_portability(sql, "snowflake", "redshift")
    ids = {f.check_id for f in out}
    assert "SQLPORT003" in ids  # redshift has no QUALIFY
    assert "SQLPORT001" in ids  # IFF is snowflake-only here


def test_sqlport_merge_missing() -> None:
    out = analyze_sql_portability("MERGE INTO t USING s ON t.id = s.id", "snowflake", "clickhouse")
    assert any(f.check_id == "SQLPORT002" for f in out)


def test_sqlport_timestamp_tz() -> None:
    out = analyze_sql_portability(
        "select convert_timezone('UTC', ts) from t", "snowflake", "bigquery"
    )
    assert any(f.check_id == "SQLPORT004" for f in out)


def test_sqlport_identifier_quoting() -> None:
    out = analyze_sql_portability('select "order", "user" from t', "snowflake", "bigquery")
    assert any(f.check_id == "SQLPORT005" for f in out)


def test_sqlport_nested_and_nulls() -> None:
    out = analyze_sql_portability("select * from t order by a", "snowflake", "bigquery")
    assert any(f.check_id == "SQLPORT007" for f in out)
    out2 = analyze_sql_portability("create table x (v variant)", "snowflake", "redshift")
    assert any(f.check_id == "SQLPORT006" for f in out2)


def test_sqlport_no_findings_same_dialect_or_empty() -> None:
    assert analyze_sql_portability("select 1", "snowflake", "snowflake") == []
    assert analyze_sql_portability("", "snowflake", "bigquery") == []
    assert analyze_sql_portability("select 1", "unknown", "bigquery") == []


def test_sqlport_in_plan(tmp_path: Path) -> None:
    files = {
        "q.sql": "select *, qualify row_number() over (order by x) = 1 from t",
    }
    plan = plan_platform_migration(_ctx(tmp_path, files), "snowflake", "redshift")
    assert any(f.check_id == "SQLPORT003" for f in plan.sql_findings)


# --- schema compatibility --------------------------------------------------


def test_schema_struct_to_variant_lossy() -> None:
    c = map_type("bigquery", "STRUCT<a INT64>", "snowflake")
    assert c.target_type == "OBJECT"
    assert c.compatibility == Compatibility.LOSSY
    assert c.nested_shape


def test_schema_variant_to_json_coerced() -> None:
    c = map_type("snowflake", "VARIANT", "bigquery")
    assert c.target_type == "JSON"
    assert c.compatibility == Compatibility.COERCED


def test_schema_struct_to_tuple_lossy() -> None:
    c = map_type("bigquery", "STRUCT<a INT64>", "clickhouse")
    assert c.target_type == "Tuple"
    assert c.compatibility == Compatibility.LOSSY


def test_schema_object_to_json_coerced() -> None:
    c = map_type("snowflake", "OBJECT", "bigquery")
    assert c.target_type == "JSON"
    assert c.compatibility == Compatibility.COERCED


def test_schema_row_to_struct_direct() -> None:
    c = map_type("trino", "ROW", "bigquery")
    assert c.target_type == "STRUCT"
    assert c.compatibility == Compatibility.DIRECT


def test_schema_tuple_to_object() -> None:
    c = map_type("clickhouse", "Tuple", "opensearch")
    assert c.target_type == "object"
    assert c.compatibility in (Compatibility.LOSSY, Compatibility.COERCED)


def test_schema_scalars() -> None:
    assert map_type("snowflake", "NUMBER(38,0)", "bigquery").target_type == "NUMERIC"
    assert map_type("bigquery", "TIMESTAMP", "trino").target_type == ("TIMESTAMP WITH TIME ZONE")
    assert map_type("redshift", "VARCHAR(100)", "bigquery").target_type == "STRING"


def test_schema_unknown_and_incompatible() -> None:
    assert map_type("snowflake", "NOSUCHTYPE", "bigquery").compatibility == Compatibility.UNKNOWN
    assert map_type("trino", "GEOGRAPHY", "clickhouse").compatibility in (
        Compatibility.INCOMPATIBLE,
        Compatibility.UNKNOWN,
    )


def test_schema_deterministic() -> None:
    assert map_type("bigquery", "STRUCT<a INT64>", "snowflake") == map_type(
        "bigquery", "STRUCT<a INT64>", "snowflake"
    )
