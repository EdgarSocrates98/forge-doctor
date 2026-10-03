import json
from pathlib import Path

from forge_doctor_data.core.models import CheckResult, ScanReport, Severity
from forge_doctor_data.output.json_renderer import render_json
from forge_doctor_data.output.summary import exit_code


def _report(*severities: Severity) -> ScanReport:
    return ScanReport(
        version="0.1.0",
        project=Path("/tmp/x"),
        results=[
            CheckResult(
                check_id=f"X{i:03}",
                title="t",
                severity=s,
                category="python",
                message="m",
                file=Path("a.py"),
                line=1,
            )
            for i, s in enumerate(severities)
        ],
    )


def test_json_contract_shape():
    payload = json.loads(render_json(_report(Severity.WARNING)))
    assert payload["version"] == "0.1.0"
    assert set(payload["summary"]) == {"passed", "info", "warnings", "errors"}
    result = payload["results"][0]
    assert result["check_id"] == "X000"
    assert result["severity"] == "warning"
    assert result["file"] == "a.py"
    assert result["line"] == 1


def test_json_file_paths_are_posix():
    report = _report(Severity.WARNING)
    payload = json.loads(render_json(report))
    assert payload["results"][0]["file"] == "a.py"
    report = ScanReport(
        version="0.1.0",
        project=Path("/tmp/x"),
        results=[
            CheckResult(
                check_id="X1",
                title="t",
                severity=Severity.WARNING,
                category="spark",
                message="m",
                file=Path("src") / "jobs" / "etl.py",
            )
        ],
    )
    assert json.loads(render_json(report))["results"][0]["file"] == "src/jobs/etl.py"


def test_exit_code_zero_when_clean():
    assert exit_code(_report(Severity.PASS, Severity.WARNING)) == 0


def test_exit_code_one_on_error():
    assert exit_code(_report(Severity.ERROR)) == 1


def test_fail_on_warning():
    assert exit_code(_report(Severity.WARNING), fail_on="warning") == 1
    assert exit_code(_report(Severity.INFO), fail_on="warning") == 0
