"""Adversarial contract/drift tests: malformed input, absent contract,
no negative-proof drift."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.contract import detect_drift, load_contract
from forge_doctor_data.core.runtime_evidence import RuntimeEvidenceModel, RuntimeExecution


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_contract_empty_and_scalar_docs(tmp_path: Path) -> None:
    _write(tmp_path, "c.yml", "")
    assert not load_contract(tmp_path / "c.yml").ok
    _write(tmp_path, "d.yml", "- just\n- a\n- list\n")
    assert not load_contract(tmp_path / "d.yml").ok


def test_contract_bad_pipeline_sections(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "c.yml",
        "contract_version: 1\nplatform-contract:\n  pipelines:\n    x: notamap\n"
        '    y:\n      compute: "glue"\n      sla:\n        duration: banana\n',
    )
    c = load_contract(tmp_path / "c.yml")
    assert any("x" in i for i in c.issues)
    assert any("sla.duration" in i for i in c.issues)


def test_drift_without_contract_is_empty(tmp_path: Path) -> None:
    _write(tmp_path, "etl.py", "import boto3\nboto3.client('lambda')\n")
    ctx = ProjectContext(root=tmp_path)
    assert ctx.contract is None
    # contract=None -> drift API is not invoked; guard the check anyway
    from forge_doctor_data.checks.architecture import CHECKS

    for chk in CHECKS:
        assert chk.run(ctx) == []


def test_sla_requires_identity_not_coincidence(tmp_path: Path) -> None:
    """A runtime artifact for a *different* pipeline must not violate this
    pipeline's SLA - absence of identity = absence of evidence."""
    _write(
        tmp_path,
        "platform-contract.yml",
        "contract_version: 1\nplatform-contract:\n  pipelines:\n    orders:\n"
        "      sla:\n        duration: 5m\n",
    )
    ctx = ProjectContext(root=tmp_path)
    m = RuntimeEvidenceModel(source="sfn_history")
    m.identifiers["execution_id"] = "someone-elses-exec"
    m.executions.append(RuntimeExecution(id="e1", kind="state", duration_ms=10 * 60_000))
    drift = detect_drift(ctx, ctx.contract, [m])
    assert not [d for d in drift if d.check_id == "ARCH005"]


def test_version_drift_needs_config_evidence(tmp_path: Path) -> None:
    """Contracted version with zero observed versions = UNRESOLVED, not drift."""
    _write(
        tmp_path,
        "platform-contract.yml",
        "contract_version: 1\nplatform-contract:\n  pipelines:\n    etl:\n"
        '      compute:\n        platform: glue\n        version: "5.1"\n',
    )
    ctx = ProjectContext(root=tmp_path)
    drift = detect_drift(ctx, ctx.contract)
    assert not [d for d in drift if d.check_id == "ARCH002"]
