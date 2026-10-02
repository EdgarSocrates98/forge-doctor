"""Roadmap-3 phase 2: governance CLI - policy report + evidence bundles."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor.cli.app import app

_PACK = """
pack: org-gov
version: "2.0"
require_approval: true
rules:
  - id: ORG050
    severity: warning
    message: README required
    require:
      file: README.md
"""


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".forge-doctor" / "policy").mkdir(parents=True)
    (tmp_path / ".forge-doctor" / "policy" / "gov.yml").write_text(_PACK)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.forge-doctor]\n"
        "[[tool.forge-doctor.suppressions]]\n"
        'rule = "S3_001"\n'
        'owner = "alice"\n'
    )
    (tmp_path / "app.py").write_text("print('hi')\n")
    return tmp_path


def test_policy_report_json(tmp_path: Path) -> None:
    _project(tmp_path)
    result = CliRunner().invoke(app, ["policy", "report", "--path", str(tmp_path), "-f", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["packs"][0]["name"] == "org-gov"
    assert data["packs"][0]["require_approval"] is True
    assert data["violations"]["by_rule"]["ORG050"] == 1
    assert data["violations"]["by_rule"]["POLICY011"] == 1  # unapproved suppression
    assert data["suppressions"]["unapproved"] == [{"rule": "S3_001", "path": None}]


def test_policy_report_text_lists_packs_and_audit(tmp_path: Path) -> None:
    _project(tmp_path)
    result = CliRunner().invoke(app, ["policy", "report", "--path", str(tmp_path)])
    assert "org-gov" in result.stdout
    assert "Violations" in result.stdout
    assert result.exit_code == 1  # violations present


def test_scan_evidence_out_bundle(tmp_path: Path) -> None:
    _project(tmp_path)
    out = tmp_path / "audit"
    result = CliRunner().invoke(app, ["scan", str(tmp_path), "--evidence-out", str(out)])
    assert result.exit_code == 0, result.stdout
    bundles = list(out.iterdir())
    assert len(bundles) == 1 and bundles[0].name.startswith("evidence-")
    report = json.loads((bundles[0] / "report.json").read_text())
    assert report["schema_version"] == "3.0"
    suppressions = json.loads((bundles[0] / "suppressions.json").read_text())
    assert suppressions[0]["rule"] == "S3_001"
    assert "approved_by" in suppressions[0]
    packs = json.loads((bundles[0] / "packs.json").read_text())
    assert packs["packs"][0]["name"] == "org-gov"


def test_named_baseline_roundtrip_via_cli(tmp_path: Path) -> None:
    _project(tmp_path)
    runner = CliRunner()
    save = runner.invoke(app, ["scan", str(tmp_path), "--save-baseline", "main", "--quiet"])
    assert save.exit_code == 0, save.stdout
    baseline = tmp_path / ".forge-doctor" / "baselines" / "main.json"
    assert baseline.is_file()
    use = runner.invoke(app, ["scan", str(tmp_path), "--baseline", "main", "--new-only", "--quiet"])
    assert use.exit_code == 0, use.stdout
    assert "+0 new" in use.stdout


def test_suppressions_json_includes_approved_by(tmp_path: Path) -> None:
    _project(tmp_path)
    result = CliRunner().invoke(app, ["suppressions", str(tmp_path), "--json"])
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows[0]["rule"] == "S3_001"
    assert rows[0]["approved_by"] == ""
