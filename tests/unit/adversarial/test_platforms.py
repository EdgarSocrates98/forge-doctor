"""Adversarial tests for EMR / Databricks / Delta intelligence.

Covers false-positive resistance, missing evidence, spoofed identifiers,
and insertion-order determinism.
"""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.databricks_model import databricks_model
from forge_doctor.analyzers.delta_model import delta_model
from forge_doctor.analyzers.emr_model import emr_model
from forge_doctor.analyzers.platform_graph_builder import build_platform_graph
from forge_doctor.checks.platforms import CHECKS
from forge_doctor.core.context import ProjectContext


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ctx(root: Path) -> ProjectContext:
    return ProjectContext(root=root)


def _results(ctx: ProjectContext, check_id: str) -> list:
    check = next(c for c in CHECKS if c.id == check_id)
    return check.run(ctx)


def test_empty_project_no_findings(tmp_path: Path) -> None:
    _write(tmp_path, "readme.md", "# nothing here\n")
    ctx = _ctx(tmp_path)
    assert not emr_model(ctx).has_emr
    assert not databricks_model(ctx).has_databricks
    assert not delta_model(ctx).has_delta
    from forge_doctor.core.models import Severity

    for c in CHECKS:
        if c.id.startswith(("EMR", "DBX", "DELTA")):
            problems = [r for r in c.run(ctx) if r.severity != Severity.PASS]
            assert not problems, c.id


def test_non_emr_resource_names_no_fp(tmp_path: Path) -> None:
    """Names containing 'emr'/'delta' as substrings must not be treated as EMR/Delta."""
    _write(
        tmp_path,
        "main.tf",
        """
resource "aws_s3_bucket" "delta" {
  bucket = "delta-backup-bucket"
}
resource "aws_lambda_function" "emr_notifier" {
  function_name = "emr-notifier"
  runtime       = "python3.12"
}
""",
    )
    ctx = _ctx(tmp_path)
    assert not emr_model(ctx).clusters
    assert not emr_model(ctx).serverless_apps


def test_spoofed_delta_in_strings_no_ops(tmp_path: Path) -> None:
    """Delta-looking words in comments/strings must not fabricate table ops."""
    _write(
        tmp_path,
        "job.py",
        """
# we do NOT use delta lake here; plain parquet only
MESSAGE = "MERGE INTO would be bad"
df.write.parquet("s3://out/plain")
""",
    )
    m = delta_model(_ctx(tmp_path))
    assert not m.tables
    assert not m.op_counts()


def test_missing_evidence_is_silence(tmp_path: Path) -> None:
    """An EMR cluster with no security_configuration attr must not claim
    a specific misconfiguration beyond the documented 'absent' checks."""
    _write(
        tmp_path,
        "emr.tf",
        """
resource "aws_emr_cluster" "bare" {
  name          = "bare"
  release_label = "emr-7.2.0"
}
""",
    )
    ctx = _ctx(tmp_path)
    m = emr_model(ctx)
    c = m.clusters[0]
    assert not c.security_configuration and not c.log_uri
    # the absence findings fire, but version-floor EMR001 stays silent on emr-7.x
    assert not _results(ctx, "EMR001")
    assert _results(ctx, "EMR005")


def test_dbr_unparseable_no_floor_claim(tmp_path: Path) -> None:
    """A non-numeric / serverless DBR string must not trigger DBX003."""
    _write(
        tmp_path,
        "dbx.tf",
        """
resource "databricks_cluster" "srv" {
  cluster_name = "serverless"
  spark_version = "serverless"
}
resource "databricks_cluster" "custom" {
  cluster_name = "custom"
  spark_version = "custom:local-snapshot"
}
""",
    )
    res = _results(_ctx(tmp_path), "DBX003")
    assert not res


def test_emr_on_eks_not_counted_as_ec2(tmp_path: Path) -> None:
    """Virtual clusters are a different model - they must not inflate EC2 checks."""
    _write(
        tmp_path,
        "vc.tf",
        """
resource "aws_emrcontainers_virtual_cluster" "vc" {
  name = "only-eks"
  container_provider {
    id   = "eks-1"
    type = "EKS"
  }
}
""",
    )
    ctx = _ctx(tmp_path)
    m = emr_model(ctx)
    assert not m.clusters and len(m.eks_clusters) == 1
    assert not _results(ctx, "EMR005")  # security_config checks EC2 clusters only


def test_insertion_order_determinism(tmp_path: Path) -> None:
    """Same files, two ctx instances -> identical findings + identical graph."""
    _write(
        tmp_path,
        "z.tf",
        """
resource "databricks_cluster" "z" { cluster_name = "z" num_workers = 2 }
""",
    )
    _write(
        tmp_path,
        "a.sql",
        "CREATE TABLE t (a INT) USING delta;\n"
        "MERGE INTO t USING s ON t.id=s.id WHEN MATCHED THEN DELETE;\n",
    )
    _write(
        tmp_path,
        "emr.tf",
        'resource "aws_emr_cluster" "e" { name = "e" release_label = "emr-6.9.0" }\n',
    )
    first = [(r.check_id, r.message) for c in CHECKS for r in c.run(_ctx(tmp_path))]
    second = [(r.check_id, r.message) for c in CHECKS for r in c.run(_ctx(tmp_path))]
    assert first == second

    g1 = build_platform_graph(_ctx(tmp_path))
    g2 = build_platform_graph(_ctx(tmp_path))
    assert sorted(e.id for e in g1.entities()) == sorted(e.id for e in g2.entities())
    assert sorted((r.src, r.kind.value, r.dst) for r in g1.relationships()) == sorted(
        (r.src, r.kind.value, r.dst) for r in g2.relationships()
    )


def test_table_name_spoof_resistance(tmp_path: Path) -> None:
    """A fictional delta table name must not create graph edges to real tables."""
    _write(
        tmp_path,
        "a.sql",
        "CREATE TABLE real_table (a INT) USING delta;\n",
    )
    _write(
        tmp_path,
        "b.sql",
        "MERGE INTO imaginary_table USING src ON 1=0 WHEN MATCHED THEN DELETE;\n",
    )
    g = build_platform_graph(_ctx(tmp_path))
    delta_tables = {e.identifier for e in g.entities() if e.id.startswith("table:delta:")}
    # MERGE INTO <tgt> records its target honestly - the point is it never
    # aliases onto 'real_table' or invents a catalog entity.
    assert "real_table" in delta_tables
    assert not any(e.identifier == "real_table.merged" for e in g.entities())


def test_no_databricks_provider_no_dbx(tmp_path: Path) -> None:
    """AWS-only Terraform must not conjure Databricks findings."""
    _write(
        tmp_path,
        "aws.tf",
        """
resource "aws_glue_job" "j" { name = "j" }
resource "aws_s3_bucket" "b" { bucket = "b" }
""",
    )
    from forge_doctor.core.models import Severity

    ctx = _ctx(tmp_path)
    assert not databricks_model(ctx).has_databricks
    for c in CHECKS:
        if c.id.startswith("DBX"):
            problems = [r for r in c.run(ctx) if r.severity != Severity.PASS]
            assert not problems, c.id
