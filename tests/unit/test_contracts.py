"""Forge ecosystem contracts: schemas, handoff bundle, verify command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor_data.checks import builtin_checks
from forge_doctor_data.cli import app
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.contract_check import validate, verify_contract
from forge_doctor_data.core.handoff import build_handoff_bundle
from forge_doctor_data.core.registry import CheckRegistry
from forge_doctor_data.core.runner import CheckRunner
from forge_doctor_data.core.schemas import SCHEMAS

runner = CliRunner()


def _scan(root: Path):
    ctx = ProjectContext(root=root.resolve())
    registry = CheckRegistry()
    registry.register_all(builtin_checks())
    return ctx, CheckRunner(registry).run(ctx)


def _bundle(root: Path) -> dict:
    ctx, report = _scan(root)
    return build_handoff_bundle(report, ctx)


# --- schema registry ---------------------------------------------------------


def test_all_contracts_registered() -> None:
    expected = {
        "scan-report",
        "policy-pack",
        "lab-expected",
        "golden-snapshot",
        "finding",
        "evidence",
        "platform-graph",
        "capability-report",
        "remediation-plan",
        "handoff-bundle",
    }
    assert expected <= set(SCHEMAS)
    for name, schema in SCHEMAS.items():
        assert schema.get("$id", "").endswith(f"/{name}.json"), name


# --- handoff bundle ----------------------------------------------------------


def test_bundle_validates_against_own_schema(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.10"\n', encoding="utf-8"
    )
    bundle = _bundle(tmp_path)
    assert bundle["contract"] == "handoff-bundle"
    assert verify_contract(bundle, "handoff-bundle") == []


def test_bundle_deterministic(tmp_path: Path) -> None:
    (tmp_path / "jobs.py").write_text("x = 1\n", encoding="utf-8")
    a = json.dumps(_bundle(tmp_path), sort_keys=True)
    b = json.dumps(_bundle(tmp_path), sort_keys=True)
    assert a == b


def test_bundle_has_stable_keys(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    for key in (
        "contract",
        "contract_version",
        "schema_version",
        "tool",
        "project",
        "summary",
        "results",
        "graph",
        "capabilities",
        "plans",
    ):
        assert key in bundle
    assert "generated_at" not in bundle  # determinism: no timestamps


def test_verify_rejects_malformed_bundle() -> None:
    errors = verify_contract({"contract": "handoff-bundle"}, "handoff-bundle")
    assert errors
    assert any("results" in e or "graph" in e for e in errors)


def test_verify_rejects_bad_capability_status() -> None:
    errors = verify_contract({"p": {"C": "bogus"}}, "capability-report")
    assert any("enum" in e for e in errors)


def test_verify_unknown_contract_named() -> None:
    errors = verify_contract({}, "nope")
    assert any("unknown contract" in e for e in errors)


def test_validate_pattern_and_const() -> None:
    schema = {"type": "object", "properties": {"id": {"type": "string", "pattern": "^[A-Z]+$"}}}
    assert validate({"id": "ABC"}, schema) == []
    assert validate({"id": "abc"}, schema)


def test_validate_oneof() -> None:
    schema = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert validate("x", schema) == [] and validate(3, schema) == []
    assert validate(1.5, schema)


def test_validate_additional_properties_false() -> None:
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "additionalProperties": False,
    }
    assert validate({"a": 1}, schema) == []
    assert validate({"a": 1, "b": 2}, schema)


# --- CLI ---------------------------------------------------------------------


def test_cli_export_handoff(tmp_path: Path) -> None:
    (tmp_path / "jobs.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(app, ["export", str(tmp_path), "--format", "handoff"])
    assert result.exit_code == 0, result.output
    bundle = json.loads(result.output)
    assert bundle["tool"]["name"] == "forge-doctor-data"
    assert verify_contract(bundle, "handoff-bundle") == []


def test_cli_contracts_verify_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "jobs.py").write_text("x = 1\n", encoding="utf-8")
    bundle_file = tmp_path / "bundle.json"
    result = runner.invoke(app, ["export", str(tmp_path), "-f", "handoff", "-o", str(bundle_file)])
    assert result.exit_code == 0
    ok = runner.invoke(app, ["contracts", "verify", str(bundle_file)])
    assert ok.exit_code == 0, ok.output
    bad = tmp_path / "bad.json"
    bad.write_text('{"contract": "handoff-bundle"}', encoding="utf-8")
    rejected = runner.invoke(app, ["contracts", "verify", str(bad)])
    assert rejected.exit_code == 1
    assert "invalid" in rejected.output


def test_cli_contracts_verify_stdin(tmp_path: Path) -> None:
    (tmp_path / "jobs.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(app, ["export", str(tmp_path), "-f", "handoff"])
    assert result.exit_code == 0
    ok = runner.invoke(app, ["contracts", "verify", "-"], input=result.output)
    assert ok.exit_code == 0, ok.output


def test_cli_contracts_verify_unknown_contract(tmp_path: Path) -> None:
    f = tmp_path / "x.json"
    f.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["contracts", "verify", str(f), "--contract", "nope"])
    assert result.exit_code == 1


def test_cli_contracts_list() -> None:
    result = runner.invoke(app, ["contracts", "list"])
    assert result.exit_code == 0
    for name in SCHEMAS:
        assert name in result.output
