from forge_doctor_data.core.models import CheckResult, Severity, Summary


def _result(severity: Severity) -> CheckResult:
    return CheckResult(
        check_id="X001",
        title="t",
        severity=severity,
        category="repository",
        message="m",
    )


def test_severity_parse():
    assert Severity.parse("warning") is Severity.WARNING
    assert Severity.parse(" ERROR ") is Severity.ERROR


def test_summary_counts_each_severity():
    results = [
        _result(Severity.PASS),
        _result(Severity.INFO),
        _result(Severity.WARNING),
        _result(Severity.WARNING),
        _result(Severity.ERROR),
    ]
    summary = Summary.from_results(results)
    assert (summary.passed, summary.info, summary.warnings, summary.errors) == (1, 1, 2, 1)


def test_summary_empty():
    summary = Summary.from_results([])
    assert (summary.passed, summary.info, summary.warnings, summary.errors) == (0, 0, 0, 0)
