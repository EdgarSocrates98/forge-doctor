from datetime import date, timedelta
from pathlib import Path

from forge_doctor_data.core.config import (
    ForgeDoctorDataConfig,
    Policy,
    RuleOverride,
    Suppression,
)
from forge_doctor_data.core.models import CheckResult, ScanReport, Severity
from forge_doctor_data.core.policy import (
    apply_policy,
    is_expired,
    suppression_statuses,
)


def _report(results: list[CheckResult]) -> ScanReport:
    return ScanReport(version="0", project=Path("."), results=results)


def _finding(check_id: str = "SPARK001", file: str | None = "job.py", line: int | None = 3):
    return CheckResult(
        check_id=check_id,
        title="t",
        severity=Severity.WARNING,
        category="spark",
        message="m",
        file=Path(file) if file else None,
        line=line,
    )


def test_severity_override_replaces_severity():
    cfg = ForgeDoctorDataConfig(policy=Policy(rules={"SPARK001": RuleOverride(severity="error")}))
    report = apply_policy(_report([_finding()]), cfg)
    assert report.results[0].severity is Severity.ERROR


def test_disabled_rule_removes_findings():
    cfg = ForgeDoctorDataConfig(policy=Policy(rules={"SPARK001": RuleOverride(enabled=False)}))
    report = apply_policy(_report([_finding()]), cfg)
    assert report.results == []


def test_active_suppression_removes_finding():
    cfg = ForgeDoctorDataConfig(
        suppressions=(Suppression(rule="SPARK001", reason="legacy", owner="team"),)
    )
    report = apply_policy(_report([_finding()]), cfg)
    assert report.results == []
    assert report.suppressions[0].status == "active"
    assert report.suppressions[0].matched == 1


def test_expired_suppression_reactivates_and_warns():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    cfg = ForgeDoctorDataConfig(
        suppressions=(Suppression(rule="SPARK001", reason="temp", expires=yesterday),)
    )
    report = apply_policy(_report([_finding()]), cfg)
    ids = [r.check_id for r in report.results]
    assert "SPARK001" in ids  # finding is back
    assert "POLICY001" in ids  # warning about the expiry
    assert report.suppressions[0].status == "expired"


def test_suppression_scoped_by_path_and_line():
    cfg = ForgeDoctorDataConfig(suppressions=(Suppression(rule="SPARK001", path="other/**/*.py"),))
    report = apply_policy(_report([_finding(file="job.py")]), cfg)
    assert any(r.check_id == "SPARK001" for r in report.results)
    assert report.suppressions[0].status == "unused"


def test_is_expired_malformed_date_not_expired():
    s = Suppression(rule="X", expires="not-a-date")
    assert not is_expired(s, date.today())


def test_suppression_statuses():
    results = [_finding()]
    sup = (
        Suppression(rule="SPARK001"),
        Suppression(rule="NOPE999"),
        Suppression(rule="SPARK001", expires="2000-01-01"),
    )
    statuses = suppression_statuses(sup, results)
    assert [s.status for s in statuses] == ["active", "unused", "expired"]
