from pathlib import Path

from forge_doctor_data.core.models import CheckResult, Severity
from forge_doctor_data.integrations.lsp_server import (
    diagnostics_for_results,
    diagnostics_plan,
    finding_to_diagnostic,
    uri_to_fs_path,
)


def _finding(sev: Severity, line: int = 5, file: str | None = "job.py") -> CheckResult:
    return CheckResult(
        check_id="SPARK001",
        title="t",
        severity=sev,
        category="spark",
        message="m",
        file=Path(file) if file else None,
        line=line,
        column=3,
        recommendation="fix it",
    )


def test_finding_to_diagnostic_shape():
    d = finding_to_diagnostic(_finding(Severity.ERROR))
    assert d["source"] == "forge-doctor-data"
    assert d["code"] == "SPARK001"
    assert d["severity"] == 1  # LSP Error
    assert d["range"]["start"] == {"line": 4, "character": 2}  # 0-based
    assert "fix it" in d["message"]


def test_severity_mapping():
    assert finding_to_diagnostic(_finding(Severity.WARNING))["severity"] == 2
    assert finding_to_diagnostic(_finding(Severity.INFO))["severity"] == 3


def test_diagnostics_grouping_skips_pass_and_fileless():
    grouped = diagnostics_for_results(
        [
            _finding(Severity.ERROR),
            _finding(Severity.PASS),
            _finding(Severity.WARNING, file=None),
        ]
    )
    assert list(grouped) == ["job.py"]
    assert len(grouped["job.py"]) == 1


def test_uri_to_fs_path_decodes():
    assert uri_to_fs_path("file:///home/u/proj/job%20one.py").as_posix().endswith("proj/job one.py")
    win = uri_to_fs_path("file:///C:/Users/e/proj/job.py")
    assert win is not None and "C:" in str(win)
    assert uri_to_fs_path("untitled:x") is None
    assert uri_to_fs_path("file:///localhost/etc/hosts").as_posix().endswith("etc/hosts")


def test_diagnostics_plan_clears_stale():
    grouped = diagnostics_for_results([_finding(Severity.ERROR, file="job.py")])
    plan = diagnostics_plan(grouped, previously_published={"job.py", "gone.py"})
    assert len(plan["job.py"]) == 1
    assert plan["gone.py"] == []  # empty publish clears squiggles
