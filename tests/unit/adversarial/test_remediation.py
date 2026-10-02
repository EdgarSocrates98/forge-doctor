"""Adversarial remediation tests: planning must stay advisory and total."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.core.models import CheckResult, Severity
from forge_doctor.core.remediation import plan_remediation


def _finding(check_id: str) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title="t",
        severity=Severity.WARNING,
        category="test",
        message="m",
        file=Path("x.py"),
    )


def test_empty_inputs_no_plans_no_crash() -> None:
    assert plan_remediation([]) == []
    assert plan_remediation([], []) == []
    assert plan_remediation([], [], root_cause="RC_X") == []


def test_root_cause_suppresses_finding_plans() -> None:
    """--root-cause must not leak finding-based plans alongside."""
    plans = plan_remediation([_finding("SPARK003")], [], root_cause="RC_SPARK_SKEW")
    assert plans == []


def test_finding_without_remediation_pack_is_silent() -> None:
    """Unmapped check ids produce no plan - never a guessed remediation."""
    plans = plan_remediation([_finding("AWS001"), _finding("REP010")])
    assert plans == []
