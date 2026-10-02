"""Public API surface (roadmap-2 phase 8) - stability contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor import api


def test_public_surface_is_pinned() -> None:
    """``api.__all__`` is the contract - changing it is a semver event."""
    assert set(api.__all__) == {
        "SCHEMA_VERSION",
        "DataPlatformGraph",
        "ScanOptions",
        "ScanReport",
        "capabilities_evaluate",
        "migrate_plans",
        "platform_graph",
        "scan",
        "version",
        "what_if",
    }


def test_version_and_schema_version() -> None:
    assert api.version()
    assert api.SCHEMA_VERSION == "1.0"
    parts = api.SCHEMA_VERSION.split(".")
    assert len(parts) == 2 and all(p.isdigit() for p in parts)


def test_scan_returns_report(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hi')\n")
    report = api.scan(tmp_path)
    assert hasattr(report, "results")
    assert hasattr(report, "summary")


def test_platform_graph_returns_graph(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text('resource "aws_s3_bucket" "b" {\n  bucket = "data"\n}\n')
    g = api.platform_graph(tmp_path)
    assert any("s3" in e.id for e in g.entities())


def test_capabilities_evaluate_status(tmp_path: Path) -> None:
    status = api.capabilities_evaluate("glue", "GLUE_ICEBERG", version="5.0")
    assert status in {"supported", "unsupported", "conditional", "unknown"}


def test_capabilities_evaluate_unknown_platform() -> None:
    assert api.capabilities_evaluate("no-such-platform", "X") == "unknown"


def test_what_if_and_migrate_plans(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_glue_job" "j" {\n  name = "x"\n  glue_version = "4.0"\n}\n'
    )
    reports = api.what_if(tmp_path, {"glue-version": "5.0"})
    assert isinstance(reports, list) and reports
    plans = api.migrate_plans(tmp_path)
    assert isinstance(plans, list)


def test_scan_report_json_matches_contract(tmp_path: Path) -> None:
    """Every required key in the scan-report JSON Schema is present."""
    (tmp_path / "app.py").write_text("x = 1\n")
    report = api.scan(tmp_path)
    from forge_doctor.output.json_renderer import result_to_dict

    sample = None
    for r in report.results:
        d = result_to_dict(r)
        if d.get("severity") != "pass":
            sample = d
            break
        sample = sample or d
    assert sample is not None
    required = {
        "check_id",
        "title",
        "severity",
        "category",
        "message",
        "fingerprint",
        "file",
        "line",
        "recommendation",
    }
    assert required <= set(sample)
    assert sample["severity"] in {"error", "warning", "info", "pass"}


def test_schema_registry_covers_public_artifacts() -> None:
    from forge_doctor.core.schemas import SCHEMAS

    assert set(SCHEMAS) == {
        "scan-report",
        "policy-pack",
        "lab-expected",
        "golden-snapshot",
    }
    for schema in SCHEMAS.values():
        assert schema["$schema"].endswith("/schema")
        assert "title" in schema
        json.dumps(schema)  # serializable


def test_schema_contracts_cli_lists_and_dumps() -> None:
    from typer.testing import CliRunner

    from forge_doctor.cli.app import app

    runner = CliRunner()
    result = runner.invoke(app, ["schema", "contracts"])
    assert result.exit_code == 0
    assert "scan-report" in result.stdout

    dumped = runner.invoke(app, ["schema", "contracts", "policy-pack"])
    assert dumped.exit_code == 0
    doc = json.loads(dumped.stdout)
    assert doc["title"].startswith("Organization")

    bad = runner.invoke(app, ["schema", "contracts", "nope"])
    assert bad.exit_code != 0
