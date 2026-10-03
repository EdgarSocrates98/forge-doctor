"""Unit tests for the ICE### checks (IcebergProjectModel consumers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_doctor_data.checks.iceberg import (
    CHECKS,
    ConflictingCatalogConfig,
    FeatureFormatFloor,
    FormatVersionCompat,
    IcebergUsage,
    InsertIntoCatalog,
    LegacyWriteApi,
    MergeNoExtensions,
    MergeNoPartition,
    MergeNoPartitionPruning,
    NoExpireSnapshots,
    NoRewriteDataFiles,
    NoRewriteManifests,
    RuntimeCompatibility,
    SmallFilePattern,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


ICEBERG_JOB = """\
spark.conf.set("spark.sql.catalog.cat", "org.apache.iceberg.spark.SparkCatalog")
df.writeTo("cat.db.t").using("iceberg").append()
"""

MERGE_SQL = "MERGE INTO cat.db.t t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET *;\n"

CONF = "spark.sql.catalog.cat=org.apache.iceberg.spark.SparkCatalog\n"

TF_GLUE3 = 'resource "aws_glue_job" "etl" {\n  glue_version = "3.0"\n}\n'


def test_usage_anchor(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": ICEBERG_JOB})
    results = IcebergUsage().run(ctx)
    assert results[0].severity == Severity.INFO
    assert "tables" in results[0].message


def test_usage_anchor_empty(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": "x = 1\n"})
    assert IcebergUsage().run(ctx)[0].severity == Severity.PASS


def test_format_version_v1_with_merge(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(
        tmp_path,
        {
            "conf.properties": CONF,
            "ddl.sql": "CREATE TABLE cat.db.t (id BIGINT) USING iceberg "
            "TBLPROPERTIES ('format-version'='1');\n" + MERGE_SQL,
        },
    )
    results = FormatVersionCompat().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "format-version=1" in results[0].message


def test_format_version_unset_with_merge(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(
        tmp_path,
        {"conf.properties": CONF, "q.sql": MERGE_SQL},
    )
    results = FormatVersionCompat().run(ctx)
    assert len(results) == 1
    assert "unset" in results[0].message


def test_format_version_v2_clean(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(
        tmp_path,
        {
            "conf.properties": CONF,
            "q.sql": "CREATE TABLE cat.db.t (id BIGINT) USING iceberg "
            "TBLPROPERTIES ('format-version'='2');\n" + MERGE_SQL,
        },
    )
    assert FormatVersionCompat().run(ctx) == []


def test_merge_no_partition(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(tmp_path, {"conf.properties": CONF, "q.sql": MERGE_SQL})
    results = MergeNoPartition().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.INFO


def test_merge_partitioned_ok(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(
        tmp_path,
        {
            "conf.properties": CONF,
            "q.sql": "CREATE TABLE cat.db.t (id BIGINT) USING iceberg "
            "PARTITIONED BY (days(ts));\n" + MERGE_SQL,
        },
    )
    assert MergeNoPartition().run(ctx) == []


def test_maintenance_absent(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": ICEBERG_JOB})
    assert NoExpireSnapshots().run(ctx)[0].severity == Severity.WARNING
    assert NoRewriteDataFiles().run(ctx)[0].severity == Severity.INFO
    assert NoRewriteManifests().run(ctx)[0].severity == Severity.INFO


def test_maintenance_present(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(
        tmp_path,
        {
            "job.py": ICEBERG_JOB,
            "maint.sql": (
                "CALL cat.system.expire_snapshots('db.t');\n"
                "CALL cat.system.rewrite_data_files('db.t');\n"
                "CALL cat.system.rewrite_manifests('db.t');\n"
            ),
        },
    )
    assert NoExpireSnapshots().run(ctx) == []
    assert NoRewriteDataFiles().run(ctx) == []
    assert NoRewriteManifests().run(ctx) == []


def test_catalog_conflict(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "a.py": 'spark.conf.set("spark.sql.catalog.cat", "iceberg.SparkCatalog")\n',
            "b.py": 'spark.conf.set("spark.sql.catalog.cat", "iceberg.GlueCatalog")\n',
        },
    )
    results = ConflictingCatalogConfig().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "cat" in results[0].message


def test_catalog_same_impl_ok(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "a.py": 'spark.conf.set("spark.sql.catalog.cat", "impl.A")\n',
            "b.py": 'spark.conf.set("spark.sql.catalog.cat", "impl.A")\n',
        },
    )
    assert ConflictingCatalogConfig().run(ctx) == []


def test_runtime_incompat_glue3(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": ICEBERG_JOB, "main.tf": TF_GLUE3})
    results = RuntimeCompatibility().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "Glue 3.0" in results[0].message


def test_runtime_compat_glue5(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": ICEBERG_JOB,
            "main.tf": 'resource "aws_glue_job" "etl" {\n  glue_version = "5.0"\n}\n',
        },
    )
    assert RuntimeCompatibility().run(ctx) == []


def test_runtime_no_iceberg_silent(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_GLUE3})
    assert RuntimeCompatibility().run(ctx) == []


def test_all_checks_run(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": ICEBERG_JOB})
    for check in CHECKS:
        for r in check.run(ctx):
            assert r.check_id == check.id
            assert r.category == "iceberg"


def _cli(args: list[str]):
    from typer.testing import CliRunner

    from forge_doctor_data.cli import app

    return CliRunner().invoke(app, args)


def test_cli_inspect(tmp_path: Path) -> None:
    make_context(tmp_path, {"job.py": ICEBERG_JOB})
    result = _cli(["iceberg", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "Iceberg" in result.output
    assert "Maintenance" in result.output


def test_cli_inspect_empty(tmp_path: Path) -> None:
    result = _cli(["iceberg", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "no Iceberg evidence" in result.output


def test_cli_maintenance(tmp_path: Path) -> None:
    make_context(tmp_path, {"job.py": ICEBERG_JOB})
    result = _cli(["iceberg", "maintenance", str(tmp_path)])
    assert result.exit_code == 0
    assert "expire_snapshots" in result.output
    assert "not detected" in result.output


def test_cli_compatibility(tmp_path: Path) -> None:
    make_context(tmp_path, {"job.py": ICEBERG_JOB, "main.tf": TF_GLUE3})
    result = _cli(["iceberg", "compatibility", str(tmp_path)])
    assert result.exit_code == 0
    assert "glue 3.0" in result.output
    assert "HIGH" in result.output


def test_cli_compatibility_no_runtimes(tmp_path: Path) -> None:
    make_context(tmp_path, {"job.py": ICEBERG_JOB})
    result = _cli(["iceberg", "compatibility", str(tmp_path)])
    assert result.exit_code == 0
    assert "no runtime pins" in result.output


LEGACY_JOB = """\
df.write.format("iceberg").save("s3://bucket/t")
"""

INSERT_CATALOG_JOB = """\
spark.conf.set("spark.sql.catalog.cat", "org.apache.iceberg.spark.SparkCatalog")
df.insertInto("cat.db.t")
"""

MERGE_NO_EXT_SQL = (
    "CREATE TABLE cat.db.t (id BIGINT, dt STRING) USING iceberg "
    "PARTITIONED BY (days(dt)) TBLPROPERTIES ('format-version'='2');\n"
    "MERGE INTO cat.db.t t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET *;\n"
)

MERGE_PRUNE_SQL = (
    "CREATE TABLE cat.db.t (id BIGINT, dt STRING) USING iceberg "
    "PARTITIONED BY (days(dt)) TBLPROPERTIES ('format-version'='2');\n"
    "MERGE INTO cat.db.t t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET *;\n"
)

SMALL_FILE_JOB = """\
spark.conf.set("spark.sql.catalog.cat", "org.apache.iceberg.spark.SparkCatalog")
df.repartition(1).writeTo("cat.db.t").using("iceberg").append()
"""

MOR_SQL = (
    "CREATE TABLE cat.db.t (id BIGINT) USING iceberg "
    "TBLPROPERTIES ('format-version'='1', 'write.delete.mode'='merge-on-read');\n"
)

EXT_CONF = (
    "spark.sql.catalog.cat=org.apache.iceberg.spark.SparkCatalog\n"
    "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions\n"
)


def test_legacy_write_api(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": LEGACY_JOB})
    results = LegacyWriteApi().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.INFO


def test_insert_into_catalog(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": INSERT_CATALOG_JOB})
    results = InsertIntoCatalog().run(ctx)
    assert len(results) == 1
    assert "catalog-qualified" in results[0].message


def test_insert_into_catalog_off_session_cat(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'df.insertInto("db.t")\nx = "iceberg"\n'})
    assert InsertIntoCatalog().run(ctx) == []


def test_merge_no_extensions(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(tmp_path, {"conf.properties": CONF, "q.sql": MERGE_NO_EXT_SQL})
    results = MergeNoExtensions().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING


def test_merge_with_extensions_ok(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(tmp_path, {"conf.properties": EXT_CONF, "q.sql": MERGE_NO_EXT_SQL})
    assert MergeNoExtensions().run(ctx) == []


def test_merge_no_pruning(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(tmp_path, {"conf.properties": CONF, "q.sql": MERGE_PRUNE_SQL})
    results = MergeNoPartitionPruning().run(ctx)
    assert len(results) == 1
    assert "dt" in results[0].message


def test_merge_pruning_ok(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(
        tmp_path,
        {
            "conf.properties": CONF,
            "q.sql": "CREATE TABLE cat.db.t (id BIGINT, dt STRING) USING iceberg "
            "PARTITIONED BY (days(dt));\nMERGE INTO cat.db.t t USING s "
            "ON t.id=s.id AND t.dt=s.dt WHEN MATCHED THEN UPDATE SET *;\n",
        },
    )
    assert MergeNoPartitionPruning().run(ctx) == []


def test_small_file_pattern(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": SMALL_FILE_JOB})
    results = SmallFilePattern().run(ctx)
    assert len(results) == 1
    assert "repartition" in results[0].message


def test_small_file_pattern_no_write(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": 'x = "iceberg"\ndf.repartition(1).show()\n'})
    assert SmallFilePattern().run(ctx) == []


def test_mor_on_v1(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(tmp_path, {"conf.properties": CONF, "ddl.sql": MOR_SQL})
    results = FeatureFormatFloor().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "format-version>=2" in results[0].message


def test_mor_on_v2_ok(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    ctx = make_context(
        tmp_path,
        {
            "conf.properties": CONF,
            "ddl.sql": "CREATE TABLE cat.db.t (id BIGINT) USING iceberg "
            "TBLPROPERTIES ('format-version'='2', 'write.delete.mode'='merge-on-read');\n",
        },
    )
    assert FeatureFormatFloor().run(ctx) == []


def test_cli_merge(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot")
    make_context(tmp_path, {"conf.properties": CONF, "q.sql": MERGE_PRUNE_SQL})
    result = _cli(["iceberg", "merge", str(tmp_path)])
    assert result.exit_code == 0
    assert "MERGE analysis" in result.output
    assert "cat.db.t" in result.output


def test_cli_files(tmp_path: Path) -> None:
    make_context(tmp_path, {"job.py": SMALL_FILE_JOB})
    result = _cli(["iceberg", "files", str(tmp_path)])
    assert result.exit_code == 0
    assert "small-file" in result.output
    assert "repartition" in result.output
