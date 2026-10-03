"""Unit tests for the EvidenceKind source-plane classification (spec 169)."""

from __future__ import annotations

import json
from pathlib import Path

from forge_doctor_data.checks.controlm import EventNeverProduced
from forge_doctor_data.checks.git_checks import GitRepository
from forge_doctor_data.checks.iceberg import IcebergUsage, RuntimeCompatibility
from forge_doctor_data.checks.parquet import SmallFileDataset, UncompressedWrite
from forge_doctor_data.checks.repository import PyprojectExists
from forge_doctor_data.checks.spark import CollectToDriver
from forge_doctor_data.checks.stepfunctions import _SfnCheck
from forge_doctor_data.checks.streaming import SharedCheckpoint, TempCheckpoint
from forge_doctor_data.checks.terraform import _TfCheck
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import (
    CheckResult,
    EvidenceKind,
    ScanReport,
    Severity,
)
from forge_doctor_data.output.json_renderer import render_json, result_to_dict
from forge_doctor_data.plugins.protocol import CheckBase


def test_parse() -> None:
    assert EvidenceKind.parse("STATIC") is EvidenceKind.STATIC
    assert EvidenceKind.parse(" config ") is EvidenceKind.CONFIG
    assert EvidenceKind.parse("observed_metadata") is EvidenceKind.OBSERVED_METADATA


def test_default_is_static() -> None:
    class _Probe(CheckBase):
        id = "PROBE001"
        title = "probe"
        category = "probe"

    result = _Probe().result(Severity.INFO, "msg")
    assert result.evidence_kind is EvidenceKind.STATIC


def test_result_kwarg_overrides_class_attr() -> None:
    class _Probe(CheckBase):
        id = "PROBE002"
        title = "probe"
        category = "probe"
        evidence_kind = EvidenceKind.CONFIG

    override = _Probe().result(Severity.INFO, "msg", evidence_kind=EvidenceKind.RUNTIME)
    assert override.evidence_kind is EvidenceKind.RUNTIME


def test_category_tags() -> None:
    assert CollectToDriver().evidence_kind is EvidenceKind.STATIC
    assert TempCheckpoint().evidence_kind is EvidenceKind.STATIC
    assert IcebergUsage().evidence_kind is EvidenceKind.STATIC
    assert UncompressedWrite().evidence_kind is EvidenceKind.STATIC
    assert _TfCheck.evidence_kind is EvidenceKind.CONFIG
    assert _SfnCheck.evidence_kind is EvidenceKind.CONFIG
    assert PyprojectExists().evidence_kind is EvidenceKind.CONFIG
    assert GitRepository().evidence_kind is EvidenceKind.OBSERVED_METADATA
    assert SmallFileDataset().evidence_kind is EvidenceKind.OBSERVED_METADATA
    assert RuntimeCompatibility().evidence_kind is EvidenceKind.DERIVED
    assert SharedCheckpoint().evidence_kind is EvidenceKind.DERIVED
    assert EventNeverProduced().evidence_kind is EvidenceKind.DERIVED


def test_fingerprint_excludes_evidence_kind() -> None:
    plain = CheckResult(
        check_id="X001",
        title="t",
        severity=Severity.INFO,
        category="c",
        message="m",
    )
    tagged = CheckResult(
        check_id="X001",
        title="t",
        severity=Severity.INFO,
        category="c",
        message="m",
        evidence_kind=EvidenceKind.RUNTIME,
    )
    assert plain.fingerprint == tagged.fingerprint


def test_json_payload_emits_evidence_kind(tmp_path: Path) -> None:
    result = CollectToDriver().result(Severity.WARNING, "m", file=Path("j.py"))
    assert result_to_dict(result)["evidence_kind"] == "static"

    ctx = ProjectContext(root=tmp_path)
    report = ScanReport(version="t", project=ctx.root, results=[result])
    rendered = json.loads(render_json(report))
    assert rendered["results"][0]["evidence_kind"] == "static"


def test_untagged_result_omits_evidence_kind() -> None:
    result = CheckResult(
        check_id="X002",
        title="t",
        severity=Severity.PASS,
        category="c",
        message="m",
    )
    assert "evidence_kind" not in result_to_dict(result)
