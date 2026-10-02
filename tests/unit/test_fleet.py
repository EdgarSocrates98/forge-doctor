"""Roadmap-3 phase 3: fleet/estate intelligence (spec 204)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor.cli.app import app
from forge_doctor.core.fleet import (
    FleetManifestError,
    build_fleet_model,
    load_manifest,
)


def _estate(root: Path) -> Path:
    """Three-repo fleet: terraform defines a glue job, glue-jobs
    implements it, airflow-dags invokes it."""
    (root / "terraform").mkdir(parents=True)
    (root / "terraform" / "main.tf").write_text(
        'resource "aws_glue_job" "orders_etl" {\n  name = "orders-etl"\n  glue_version = "4.0"\n}\n'
    )
    (root / "glue-jobs").mkdir(parents=True)
    (root / "glue-jobs" / "pyproject.toml").write_text('[project]\nname = "glue-jobs"\n')
    (root / "glue-jobs" / "orders_etl.py").write_text(
        "from awsglue.job import Job\nfrom awsglue.context import GlueContext\n"
    )
    (root / "airflow-dags" / "dags").mkdir(parents=True)
    (root / "airflow-dags" / "dags" / "etl.py").write_text(
        "from airflow import DAG\n"
        "from airflow.providers.amazon.aws.operators.glue import GlueJobOperator\n"
        'with DAG("orders"):\n'
        '    GlueJobOperator(task_id="run_etl", job_name="orders-etl")\n'
    )
    return root


def _manifest(root: Path) -> Path:
    m = root / "fleet.yml"
    m.write_text(
        "fleet: test-estate\n"
        "repos:\n"
        "  - ./terraform\n"
        "  - path: ./glue-jobs\n"
        "    name: jobs\n"
        "  - ./airflow-dags\n"
    )
    return m


def test_load_manifest_list_and_dicts(tmp_path: Path) -> None:
    root = _estate(tmp_path)
    manifest = load_manifest(_manifest(root))
    assert manifest.name == "test-estate"
    names = [r.name for r in manifest.repos]
    assert "jobs" in names  # custom name wins over dir name
    assert len(manifest.repos) == 3


def test_load_manifest_json_list(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    m = tmp_path / "fleet.json"
    m.write_text(json.dumps({"repos": ["./a"]}))
    manifest = load_manifest(m)
    assert manifest.repos[0].path == (tmp_path / "a").resolve()


def test_load_manifest_directory_is_workspace(tmp_path: Path) -> None:
    root = _estate(tmp_path)
    manifest = load_manifest(root)
    assert manifest.workspace_root == root.resolve() or manifest.workspace_root == root


def test_load_manifest_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text("repos:\n  - ./missing\n")
    with pytest.raises(FleetManifestError, match="not a directory"):
        load_manifest(bad)
    empty = tmp_path / "empty.yml"
    empty.write_text("fleet: x\n")
    with pytest.raises(FleetManifestError, match="neither"):
        load_manifest(empty)
    with pytest.raises(FleetManifestError, match="not a file or directory"):
        load_manifest(tmp_path / "nope.yml")


def test_fleet_model_merges_and_links(tmp_path: Path) -> None:
    root = _estate(tmp_path)
    model = build_fleet_model(load_manifest(_manifest(root)))
    assert len(model.repositories) == 3
    ids = {e.id for e in model.graph.entities()}
    assert "compute_job:glue:orders-etl" in ids
    assert "repo:workspace:terraform" in ids
    kinds = {lnk.kind.value for lnk in model.links}
    assert "DEFINES" in kinds
    assert "INVOKES" in kinds


def test_fleet_inspect_cli(tmp_path: Path) -> None:
    root = _estate(tmp_path)
    result = CliRunner().invoke(app, ["fleet", "inspect", str(_manifest(root))])
    assert result.exit_code == 0
    assert "test-estate" in result.stdout
    assert "repositories" in result.stdout


def test_fleet_query_runtimes_json(tmp_path: Path) -> None:
    root = _estate(tmp_path)
    result = CliRunner().invoke(
        app, ["fleet", "query", str(_manifest(root)), "runtimes", "-f", "json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["domains"]["glue"] == 1
    versions = data["versions"]
    assert any(v["value"] == "4.0" for v in versions)


def test_fleet_query_dependents(tmp_path: Path) -> None:
    root = _estate(tmp_path)
    result = CliRunner().invoke(
        app, ["fleet", "query", str(_manifest(root)), "dependents", "*orders-etl*"]
    )
    assert result.exit_code == 0
    assert "task:airflow:run_etl" in result.stdout


def test_fleet_query_capability_buckets(tmp_path: Path) -> None:
    root = _estate(tmp_path)
    result = CliRunner().invoke(
        app,
        ["fleet", "query", str(_manifest(root)), "capability", "ICEBERG_MERGE_WRITE"],
    )
    assert result.exit_code == 0
    # glue 4.0 entity evaluates; bucketed as supported or conditional.
    assert "compute_job:glue:orders-etl" in result.stdout


def test_fleet_query_findings_by_check_id(tmp_path: Path) -> None:
    root = _estate(tmp_path)
    result = CliRunner().invoke(app, ["fleet", "query", str(_manifest(root)), "findings", "TF*"])
    assert result.exit_code == 0
    assert "terraform" in result.stdout


def test_fleet_report_json(tmp_path: Path) -> None:
    root = _estate(tmp_path)
    result = CliRunner().invoke(app, ["fleet", "report", str(_manifest(root)), "-f", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["fleet"] == "test-estate"
    assert len(data["repositories"]) == 3
    assert "glue" in data["entities"]["by_domain"]
    assert data["findings"]["by_repo"]


def test_fleet_manifest_missing_file_errors(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["fleet", "inspect", str(tmp_path / "nope.yml")])
    assert result.exit_code != 0
