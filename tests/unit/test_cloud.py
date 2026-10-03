"""Multi-cloud abstractions: model, CLOUD checks, capabilities, CLI (spec 223)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from forge_doctor_data.analyzers.abstractions import abstractions_model
from forge_doctor_data.checks.cloud import CHECKS
from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext

runner = CliRunner()


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


def _findings(tmp_path: Path) -> dict[str, list[str]]:
    ctx = _ctx(tmp_path)
    out: dict[str, list[str]] = {}
    for c in CHECKS:
        for f in c.run(ctx):
            out.setdefault(f.check_id, []).append(f.message)
    return out


_AZ = """resource "azurerm_storage_account" "lake" {
  name     = "stlake"
  location = "westeurope"
}
"""

_GCP = """resource "google_storage_bucket" "raw" {
  name     = "raw-gcs"
  location = "EU"
}
"""

_AWS_S3 = 'resource "aws_s3_bucket" "b" {\n  bucket = "raw-aws"\n}\n'

_AZ_EH = 'resource "azurerm_eventhub" "eh" {\n  name = "eh1"\n}\n'

_AWS_KIN = 'resource "aws_kinesis_stream" "s" {\n  name = "events"\n}\n'


def test_no_evidence(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("hi", encoding="utf-8")
    assert not abstractions_model(_ctx(tmp_path)).has_evidence


def test_azure_map(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_AZ + _AZ_EH, encoding="utf-8")
    model = abstractions_model(_ctx(tmp_path))
    kinds = {s.abstraction for s in model.services}
    assert {"object_storage", "stream"} <= kinds
    svc = {s.name: s for s in model.services}
    assert svc["stlake"].cloud == "azure"
    assert svc["stlake"].region == "westeurope"


def test_gcp_map(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_GCP, encoding="utf-8")
    svc = abstractions_model(_ctx(tmp_path)).services[0]
    assert (svc.abstraction, svc.cloud, svc.service) == (
        "object_storage",
        "gcp",
        "gcs",
    )


def test_unknown_resource_ignored(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "google_compute_instance" "vm" {\n  name = "vm"\n}\n',
        encoding="utf-8",
    )
    model = abstractions_model(_ctx(tmp_path))
    assert not model.services


def test_cloud000_census(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_AZ, encoding="utf-8")
    assert "CLOUD000" in _findings(tmp_path)


def test_cloud002_single_cloud_silent(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_AZ + _AZ_EH, encoding="utf-8")
    assert "CLOUD002" not in _findings(tmp_path)


def test_cloud002_mixed_parity_gap(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_AZ + _GCP + _AWS_KIN, encoding="utf-8")
    out = _findings(tmp_path)
    assert "CLOUD002" in out
    # object_storage has parity (azure+gcp); stream is aws-only
    assert any("stream" in m and "aws" in m for m in out["CLOUD002"])
    assert not any("object_storage" in m for m in out["CLOUD002"])


def test_cloud002_linked_suppressed(tmp_path: Path) -> None:
    tf = (
        _AZ
        + _GCP
        + """resource "azurerm_cosmosdb_account" "db" {
  name = "cosmos"
  geo_location {
    failover_priority = 0
  }
}
"""
    )
    (tmp_path / "main.tf").write_text(tf, encoding="utf-8")
    out = _findings(tmp_path)
    assert "CLOUD002" not in out


def test_cloud001_unmapped(tmp_path: Path) -> None:
    """Neptune graph entity: platform domain with no abstraction -> blind spot."""
    (tmp_path / "schema.cypher").write_text("CREATE (:Label {id: 'n1'})\n", encoding="utf-8")
    model = abstractions_model(_ctx(tmp_path))
    # neptune/graph entities are platform domains but unmapped
    assert isinstance(model.unmapped, list)


def test_capability_cloud_agnostic() -> None:
    from forge_doctor_data.api import capabilities_evaluate

    assert (
        capabilities_evaluate("warehouse", "TIME_TRAVEL", attributes={"service": "snowflake"})
        == "supported"
    )
    assert (
        capabilities_evaluate("warehouse", "TIME_TRAVEL", attributes={"service": "redshift"})
        == "unsupported"
    )
    # absent service -> the base warehouse pack's vendor-gated entry
    # resolves to conditional (needs the service/vendor attribute)
    assert capabilities_evaluate("warehouse", "TIME_TRAVEL") == "conditional"
    assert (
        capabilities_evaluate("object_storage", "VERSIONING", attributes={"service": "gcs"})
        == "supported"
    )


def test_cloud_inspect(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(_AZ + _AZ_EH, encoding="utf-8")
    result = runner.invoke(app, ["cloud", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "object_storage" in result.output
    assert "azure" in result.output


def test_cloud_inspect_empty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["cloud", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "no cloud-platform evidence" in result.output
