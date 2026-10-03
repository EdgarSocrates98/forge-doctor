"""What-if evaluation + migration planning tests (Phase 10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.migration import plan_migrations
from forge_doctor_data.core.whatif import evaluate_change, parse_change


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


TF_GLUE4 = """
resource "aws_glue_job" "etl" {
  name         = "etl"
  glue_version = "4.0"
}
"""

ICEBERG_SQL = """
CREATE TABLE glue_catalog.ice.events (id bigint) USING iceberg
"""

PY_PARQUET = 'df.write.format("parquet").mode("overwrite").save("s3://b/x")\n'

TF_LAMBDA = """
resource "aws_lambda_function" "worker" {
  function_name = "worker"
  runtime       = "python3.8"
}
"""


def test_parse_change_glue() -> None:
    ch = parse_change("glue-version=5.1")
    assert ch.target == "glue" and ch.to == "5.1"
    assert ch.property == "glue_version"


def test_parse_change_iceberg() -> None:
    ch = parse_change("iceberg-format-version=2")
    assert ch.target == "iceberg" and ch.to == "2"


def test_parse_change_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        parse_change("glue-version")
    with pytest.raises(ValueError):
        parse_change("bogus-target=1")


def test_whatif_glue_upgrade(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_GLUE4})
    report = evaluate_change(ctx, parse_change("glue-version=5.0"))
    assert report.change.from_ == "4.0"
    assert any("glue" in e for e in report.affected_entities)
    details = " ".join(i.detail for i in report.impacts)
    assert "Python 3.10 -> 3.11" in details
    assert report.has_blockers  # Java 17 + Python 3.11 are HIGH severity


def test_whatif_glue_capability_diff(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_GLUE4})
    report = evaluate_change(ctx, parse_change("glue-version=5.0"))
    # LAKEFORMATION_FGAC: conditional@4.0 -> supported@5.0
    assert "LAKEFORMATION_FGAC" in report.supported_now


def test_whatif_iceberg_format(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"t.sql": ICEBERG_SQL})
    report = evaluate_change(ctx, parse_change("iceberg-format-version=2"))
    assert report.affected_entities  # the table is affected
    assert any("format-version 2" in i.detail for i in report.impacts)


def test_whatif_unobserved_from_is_honest(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "print(1)\n"})
    report = evaluate_change(ctx, parse_change("glue-version=5.0"))
    assert report.unknown  # no glue_version observed → honest unknown
    assert "unobserved" in report.change.from_


def test_whatif_contract_conflict(tmp_path: Path) -> None:
    contract = """
platform-contract:
  contract_version: 1
  pipelines:
    etl:
      compute:
        platform: glue
        version: "4.0"
"""
    ctx = make_context(tmp_path, {"platform-contract.yml": contract, "main.tf": TF_GLUE4})
    report = evaluate_change(ctx, parse_change("glue-version=5.0"))
    assert any(i.category == "contract_conflict" for i in report.impacts)


def test_migrations_empty_project(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {})
    assert plan_migrations(ctx) == []


def test_migrations_glue_4_to_5(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_GLUE4})
    plans = plan_migrations(ctx)
    glue = next(p for p in plans if p.path_id == "glue-4-to-5")
    assert glue.source_environment == "glue 4.0"
    assert "5.0" in glue.target_environment
    assert glue.blockers  # java17 + python3.11
    assert glue.required_changes and glue.validation_steps and glue.rollback


def test_migrations_iceberg_v1(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"t.sql": ICEBERG_SQL})
    plans = plan_migrations(ctx)
    assert any(p.path_id == "iceberg-v1-to-v2" for p in plans)


def test_migrations_iceberg_v2_done_skipped(tmp_path: Path) -> None:
    sql = (
        "CREATE TABLE glue_catalog.ice.t (id bigint) USING iceberg\n"
        "TBLPROPERTIES ('format-version'='2')\n"
    )
    ctx = make_context(tmp_path, {"t.sql": sql})
    plans = plan_migrations(ctx)
    assert not any(p.path_id == "iceberg-v1-to-v2" for p in plans)


def test_migrations_parquet_to_delta_and_iceberg(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"w.py": PY_PARQUET})
    ids = {p.path_id for p in plan_migrations(ctx)}
    assert "parquet-to-delta" in ids and "parquet-to-iceberg" in ids


def test_migrations_lambda_eol(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_LAMBDA})
    plans = plan_migrations(ctx)
    lam = next((p for p in plans if p.path_id == "lambda-runtime-upgrade"), None)
    assert lam is not None
    assert "python3.8" in lam.source_environment
    assert lam.target_environment == "lambda UNKNOWN"  # pack lacks current fact
    assert any("end-of-life" in w for w in lam.warnings)


def test_migrations_deterministic(tmp_path: Path) -> None:
    files = {"main.tf": TF_GLUE4 + TF_LAMBDA, "w.py": PY_PARQUET}
    ctx = make_context(tmp_path, files)
    p1 = plan_migrations(ctx)
    p2 = plan_migrations(ctx)
    assert [p.path_id for p in p1] == [p.path_id for p in p2]
    assert sorted(p1[0].affected_entities) == sorted(p2[0].affected_entities)


def test_no_execution_in_plans(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_GLUE4})
    for p in plan_migrations(ctx):
        blob = " ".join(p.required_changes + p.validation_steps + p.rollback).lower()
        assert "terraform apply" not in blob and "glue:start" not in blob
