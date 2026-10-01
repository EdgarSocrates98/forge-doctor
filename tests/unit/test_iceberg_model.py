"""Unit tests for the IcebergProjectModel evidence fusion."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_doctor.analyzers.iceberg_model import iceberg_model
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


PYSPARK_JOB = """\
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
spark.conf.set("spark.sql.catalog.glue_catalog", "iceberg.SparkCatalog")
spark.conf.set("spark.sql.extensions", "iceberg.SparkSessionExtensions")

df = spark.read.parquet("s3://bucket/in")
df.writeTo("glue_catalog.db.orders").using("iceberg").createOrReplace()
"""


def test_python_catalog_and_format_evidence(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": PYSPARK_JOB})
    model = iceberg_model(ctx)
    assert model.has_iceberg
    assert "glue_catalog" in model.catalog_names
    assert "iceberg" in {e.name for e in model.by_kind("format")}
    assert "spark.sql.extensions" in model.configs
    assert "createorreplace" in model.operation_names


def test_table_ops_on_configured_catalog(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "conf.py": 'spark.conf.set("spark.sql.catalog.cat", "iceberg.SparkCatalog")\n',
            "job.py": 'spark.read.table("cat.db.t")\n'
            'df.writeTo("cat.db.t2").using("iceberg").append()\n',
        },
    )
    model = iceberg_model(ctx)
    assert "cat.db.t" in model.tables
    assert "read" in model.operation_names
    assert "append" in model.operation_names


def test_sql_create_using_iceberg(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot", reason="requires the [sql] extra")
    ctx = make_context(
        tmp_path,
        {
            "ddl.sql": "CREATE TABLE glue_catalog.db.t (id BIGINT) USING iceberg "
            "PARTITIONED BY (days(ts)) TBLPROPERTIES ('format-version'='2');\n",
        },
    )
    model = iceberg_model(ctx)
    assert model.has_iceberg
    assert "glue_catalog.db.t" in model.tables
    props = model.properties
    assert props.get("format-version", ("",))[0] == "2"
    assert "partitioned-by" in props


def test_sql_merge_and_call(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot", reason="requires the [sql] extra")
    ctx = make_context(
        tmp_path,
        {
            "q.sql": "MERGE INTO cat.db.t t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET *;\n"
            "CALL cat.system.expire_snapshots('db.t');\n",
            "conf.properties": "spark.sql.catalog.cat=org.apache.iceberg.spark.SparkCatalog\n",
        },
    )
    model = iceberg_model(ctx)
    assert "merge" in model.operation_names
    assert "cat.db.t" in model.tables
    assert "expire_snapshots" in model.maintenance_names


def test_config_file_catalog(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"spark-defaults.conf": "spark.sql.catalog.glue_catalog iceberg.SparkCatalog\n"},
    )
    model = iceberg_model(ctx)
    # whitespace-separated `key value` (spark-defaults.conf shape) is supported
    assert "glue_catalog" in model.catalog_names


def test_config_file_catalog_equals(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"spark-defaults.conf": "spark.sql.catalog.glue_catalog=iceberg.SparkCatalog\n"},
    )
    model = iceberg_model(ctx)
    assert "glue_catalog" in model.catalog_names


def test_iac_runtime_evidence(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"main.tf": 'resource "aws_glue_job" "etl" {\n  name = "etl"\n  glue_version = "4.0"\n}\n'},
    )
    model = iceberg_model(ctx)
    assert model.runtimes.get("glue") is not None
    assert model.runtimes["glue"].value == "4.0"


def test_no_iceberg(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": "x = 1\n"})
    model = iceberg_model(ctx)
    assert not model.has_iceberg
    assert model.evidence == []


def test_deterministic(tmp_path: Path) -> None:
    files = {
        "a.sql": "MERGE INTO c.t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET *;\n",
        "b.py": PYSPARK_JOB,
    }
    first = iceberg_model(make_context(tmp_path / "p1", files))
    second = iceberg_model(make_context(tmp_path / "p2", files))
    key = lambda e: (e.file.name, e.line, e.kind, e.name, e.value)  # noqa: E731
    assert sorted(map(key, first.evidence)) == sorted(map(key, second.evidence))


def test_merge_detail_evidence(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot", reason="requires the [sql] extra")
    ctx = make_context(
        tmp_path,
        {
            "conf.properties": "spark.sql.catalog.cat=iceberg.SparkCatalog\n",
            "q.sql": "MERGE INTO cat.db.t t USING staging s ON t.id=s.id AND t.dt=s.dt "
            "WHEN MATCHED THEN UPDATE SET *;\n",
        },
    )
    model = iceberg_model(ctx)
    detail = model.by_kind("merge_detail")
    assert len(detail) == 1
    assert detail[0].name == "cat.db.t"
    assert detail[0].value == "staging"
    on = {e.value for e in model.by_kind("merge_on")}
    assert {"id", "dt"} <= on


def test_partition_columns_evidence(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot", reason="requires the [sql] extra")
    ctx = make_context(
        tmp_path,
        {
            "conf.properties": "spark.sql.catalog.cat=iceberg.SparkCatalog\n",
            "ddl.sql": "CREATE TABLE cat.db.t (id BIGINT, ts TIMESTAMP) USING iceberg "
            "PARTITIONED BY (days(ts), bucket(16, id));\n",
        },
    )
    model = iceberg_model(ctx)
    parts = {e.name: e.value for e in model.by_kind("property") if e.name.startswith("partition.")}
    assert parts["partition.ts"] == "days"
    assert parts["partition.id"] == "bucket"


def test_write_api_evidence(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": 'spark.conf.set("spark.sql.catalog.cat", "iceberg.SparkCatalog")\n'
            'df.writeTo("cat.db.t").using("iceberg").append()\n'
            'df.insertInto("cat.db.t")\n'
        },
    )
    model = iceberg_model(ctx)
    apis = {(e.name, e.line) for e in model.by_kind("write_api")}
    assert any(name == "v2" for name, _ in apis)
    assert any(name == "legacy_catalog" for name, _ in apis)


def test_catalog_type_evidence(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "conf.properties": "spark.sql.catalog.prod=org.apache.iceberg.aws.glue.GlueCatalog\n",
        },
    )
    model = iceberg_model(ctx)
    types = {e.name: e.value for e in model.by_kind("catalog_type")}
    assert types.get("prod") == "glue"
