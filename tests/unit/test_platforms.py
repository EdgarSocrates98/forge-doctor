"""EMR / Databricks / Delta deep-intelligence tests: models, checks, rules."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.analyzers.databricks_model import databricks_model
from forge_doctor_data.analyzers.delta_model import delta_model
from forge_doctor_data.analyzers.emr_model import emr_model
from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph
from forge_doctor_data.checks.platform_rules import RULES
from forge_doctor_data.checks.platforms import CHECKS
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.crossdomain import rule_context


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ctx(root: Path) -> ProjectContext:
    return ProjectContext(root=root)


def _results(ctx: ProjectContext, check_id: str) -> list:
    check = next(c for c in CHECKS if c.id == check_id)
    return check.run(ctx)


_EMR_TF = """
resource "aws_emr_cluster" "etl" {
  name                 = "etl-cluster"
  release_label        = "emr-5.36.0"
  master_instance_type = "m5.xlarge"
  core_instance_type   = "m5.xlarge"
  core_instance_count  = 4
  service_role         = "arn:aws:iam::111122223333:role/EMR_DefaultRole"
  log_uri              = "s3://logs/emr"

  step {
    name = "spark-submit"
  }
  bootstrap_action {
    name = "install-libs"
    path = "s3://bootstrap/run.sh"
  }
}

resource "aws_emrserverless_application" "app" {
  name          = "srv-app"
  release_label = "emr-7.1.0"
  type          = "spark"
}

resource "aws_emrcontainers_virtual_cluster" "vc" {
  name = "eks-vc"
  container_provider {
    id   = "eks-main"
    type = "EKS"
  }
}
"""

_DBX_TF = """
resource "databricks_job" "nightly" {
  name = "nightly-etl"
  task {
    task_key            = "extract"
    existing_cluster_id = "0901-abc"
    notebook_task {
      notebook_path = "/jobs/extract"
    }
  }
}

resource "databricks_cluster" "shared" {
  cluster_name  = "shared"
  spark_version = "12.2.x-scala2.12"
  node_type_id  = "i3.xlarge"
  num_workers   = 4
}

resource "databricks_external_location" "ext" {
  name = "ext-loc"
  url  = "s3://data/ext"
}

resource "databricks_pipeline" "pipe" {
  name       = "bronze-ingest"
  continuous = "true"
  catalog    = "main"
}
"""

_DELTA_SQL = """
CREATE TABLE orders (id INT) USING delta
TBLPROPERTIES (
  'delta.enableDeletionVectors' = 'true',
  'delta.enableChangeDataFeed' = 'true',
  'delta.minReaderVersion' = '3',
  'delta.minWriterVersion' = '7'
);
MERGE INTO orders o USING staging s ON o.id = s.id WHEN MATCHED THEN UPDATE SET *;
DELETE FROM events WHERE dt < '2020-01-01';
"""


def test_emr_model_full_fixture(tmp_path: Path) -> None:
    _write(tmp_path, "emr.tf", _EMR_TF)
    m = emr_model(_ctx(tmp_path))
    assert m.has_emr
    c = m.clusters[0]
    assert c.name == "etl-cluster"
    assert c.release == "emr-5.36.0"
    assert c.master_type == "m5.xlarge" and c.core_count == 4
    assert c.bootstrap_actions == 1
    assert c.log_uri == "s3://logs/emr"
    assert not c.autoscaling and not c.dynamic_allocation
    assert c.steps == ("spark-submit",) and c.steps_without_fail_action == 1
    app = m.serverless_apps[0]
    assert app.name == "srv-app" and app.release == "emr-7.1.0"
    assert app.engine == "spark" and not app.max_cpu
    assert m.eks_clusters[0].name == "eks-vc"
    assert "emr-5.36.0" in m.releases and "emr-7.1.0" in m.releases


def test_emr_checks(tmp_path: Path) -> None:
    _write(tmp_path, "emr.tf", _EMR_TF)
    ctx = _ctx(tmp_path)
    assert _results(ctx, "EMR000")
    # emr-5.36.0 is below the 6.x floor.
    res = _results(ctx, "EMR001")
    assert len(res) == 1 and "5.36.0" in res[0].message
    # no scaling policy + no dynamic allocation on the EC2 cluster
    assert any("etl-cluster" in r.message for r in _results(ctx, "EMR002"))
    assert _results(ctx, "EMR005")  # no security_configuration
    assert _results(ctx, "EMR006")  # step without action_on_failure
    assert _results(ctx, "EMR007")  # serverless app without max capacity
    # log_uri is set -> EMR004 must not fire
    assert not _results(ctx, "EMR004")


def test_emr_spot_fleets(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "emr.tf",
        """
resource "aws_emr_cluster" "spot" {
  name          = "all-spot"
  release_label = "emr-6.15.0"
  instance_fleet {
    instance_type_config {
      bid_price = "0.5"
    }
    market = "SPOT"
  }
}
""",
    )
    ctx = _ctx(tmp_path)
    res = _results(ctx, "EMR003")
    assert len(res) == 1 and "all-spot" in res[0].message


def test_databricks_model_and_checks(tmp_path: Path) -> None:
    _write(tmp_path, "dbx.tf", _DBX_TF)
    ctx = _ctx(tmp_path)
    m = databricks_model(ctx)
    assert m.has_databricks
    job = m.jobs[0]
    assert job.name == "nightly-etl" and job.uses_existing_cluster
    assert job.task_count == 1 and job.has_notebook_task
    cl = m.clusters[0]
    assert cl.dbr_version == "12.2.x-scala2.12" and cl.num_workers == 4
    assert not cl.autoscale
    kinds = {o.kind for o in m.uc_objects}
    assert "external_location" in kinds
    assert m.pipelines[0].name == "bronze-ingest" and m.pipelines[0].continuous

    assert _results(ctx, "DBX000")
    assert _results(ctx, "DBX001")  # existing_cluster_id
    assert _results(ctx, "DBX002")  # fixed num_workers, no autoscale
    assert _results(ctx, "DBX003")  # DBR 12.2 < 13.3
    assert _results(ctx, "DBX005")  # external location, no storage credential
    assert _results(ctx, "DBX006")  # no databricks.yml bundle
    # DBX004 must NOT fire - a UC object exists
    assert not _results(ctx, "DBX004")


def test_databricks_bundle_suppresses_dbx006(tmp_path: Path) -> None:
    _write(tmp_path, "dbx.tf", _DBX_TF)
    _write(tmp_path, "databricks.yml", "bundle:\n  name: proj\n")
    assert not _results(_ctx(tmp_path), "DBX006")


def test_delta_model_and_checks(tmp_path: Path) -> None:
    _write(tmp_path, "ddl.sql", _DELTA_SQL)
    ctx = _ctx(tmp_path)
    m = delta_model(ctx)
    assert m.has_delta
    assert "orders" in m.tables
    counts = m.op_counts()
    assert counts["merge"] == 1 and counts["delete"] == 1
    assert counts["tblproperties"] == 1 and counts["create_using_delta"] == 1
    assert "deletion_vectors" in m.features and "cdf" in m.features
    assert m.protocol_reader == 3 and m.protocol_writer == 7

    assert _results(ctx, "DELTA000")
    assert _results(ctx, "DELTA001")  # churn but no OPTIMIZE
    assert _results(ctx, "DELTA002")  # deletion vectors
    assert _results(ctx, "DELTA004")  # CDF enabled, no consumer
    assert not _results(ctx, "DELTA003")  # no schema-evolution flags


def test_delta_python_apis(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "job.py",
        """
from delta.tables import DeltaTable

dt = DeltaTable.forName(spark, "silver.orders")
dt.update(condition="id < 0", set={"flag": "1"})
dt.optimize()
spark.read.format("delta").load("s3://lake/events")
spark.readStream.format("delta").table("orders").writeStream.format("delta").start("s3://out/t")
""",
    )
    m = delta_model(_ctx(tmp_path))
    assert "silver.orders" in m.tables
    counts = m.op_counts()
    assert counts["update"] == 1 and counts["optimize"] == 1
    assert m.delta_reads >= 1
    assert m.streaming_delta >= 1


def test_platform_graph_entities(tmp_path: Path) -> None:
    _write(tmp_path, "emr.tf", _EMR_TF)
    _write(tmp_path, "dbx.tf", _DBX_TF)
    _write(tmp_path, "ddl.sql", _DELTA_SQL)
    g = build_platform_graph(_ctx(tmp_path))
    ids = {e.id for e in g.entities()}
    assert "compute_job:emr:etl-cluster" in ids
    assert "compute_job:emr:srv-app" in ids
    assert "compute_job:databricks:nightly-etl" in ids
    assert "compute_job:databricks:shared" in ids
    assert "compute_job:databricks:bronze-ingest" in ids
    assert "storage_location:databricks:ext-loc" in ids
    assert "table:delta:orders" in ids
    # delta ops become query entities writing tables
    rels = {(r.kind.value.lower(), r.dst) for r in g.relationships()}
    assert ("writes", "table:delta:orders") in rels or (
        "depends_on",
        "table:delta:orders",
    ) in rels


def test_plat008_emr_iceberg_lakeformation(tmp_path: Path) -> None:
    """EMR writes Iceberg while LF governs, no security_configuration -> hit."""
    _write(
        tmp_path,
        "emr.tf",
        """
resource "aws_emr_cluster" "etl" {
  name          = "etl"
  release_label = "emr-6.15.0"
}
resource "aws_lakeformation_permissions" "g" {
  principal   = "arn:aws:iam::111122223333:role/R"
  permissions = ["SELECT"]
  resource {
    database { name = "db" }
  }
}
""",
    )
    _write(
        tmp_path,
        "job.py",
        'df.writeTo("glue_catalog.db.t").using("iceberg").overwritePartitions()\n',
    )
    rc = rule_context(_ctx(tmp_path))
    rule = next(r for r in RULES if r.id == "PLAT008")
    hits = rule.evaluate(rc)
    assert hits and "etl" in hits[0].message


def test_plat009_dbr_below_delta_floor(tmp_path: Path) -> None:
    """DBR 12.2 cluster + deletion vectors -> floor violation."""
    _write(tmp_path, "dbx.tf", _DBX_TF)
    _write(tmp_path, "ddl.sql", _DELTA_SQL)
    rc = rule_context(_ctx(tmp_path))
    rule = next(r for r in RULES if r.id == "PLAT009")
    hits = rule.evaluate(rc)
    assert hits and "shared" in hits[0].message


def test_deterministic_output(tmp_path: Path) -> None:
    _write(tmp_path, "emr.tf", _EMR_TF)
    _write(tmp_path, "dbx.tf", _DBX_TF)
    _write(tmp_path, "ddl.sql", _DELTA_SQL)
    a = [(r.check_id, r.message) for c in CHECKS for r in c.run(_ctx(tmp_path))]
    b = [(r.check_id, r.message) for c in CHECKS for r in c.run(_ctx(tmp_path))]
    assert a == b
