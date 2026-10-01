import json
from pathlib import Path

from forge_doctor.core.baseline import (
    BASELINE_FORMAT,
    apply_baseline,
    load_baseline,
    result_key,
    save_baseline,
)
from forge_doctor.core.models import CheckResult, ScanReport, Severity
from forge_doctor.output.summary import exit_code


def _result(
    check_id: str, message: str = "m", severity: Severity = Severity.WARNING
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        title="t",
        severity=severity,
        category="python",
        message=message,
        file=Path("a.py"),
        line=1,
    )


def _report(*results: CheckResult) -> ScanReport:
    return ScanReport(version="0.1.0", project=Path("/tmp/x"), results=list(results))


def test_save_load_roundtrip(tmp_path: Path):
    report = _report(_result("X001"), _result("X002", message="other"))
    path = tmp_path / "baseline.json"
    save_baseline(report, path)
    keys = load_baseline(path)
    assert keys == {result_key(r) for r in report.results}
    payload = json.loads(path.read_text())
    assert payload["format"] == BASELINE_FORMAT
    assert len(payload["results"]) == 2


def test_load_missing_or_corrupt_raises(tmp_path: Path):
    import pytest

    from forge_doctor.core.baseline import BaselineError

    with pytest.raises(BaselineError, match="not found"):
        load_baseline(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(BaselineError, match="not valid JSON"):
        load_baseline(bad)
    weird = tmp_path / "weird.json"
    weird.write_text('{"results": [{"check_id": 1}, "nope", {}]}', encoding="utf-8")
    with pytest.raises(BaselineError, match="fingerprint version"):
        load_baseline(weird)


def test_wrong_fingerprint_version_fails_loud(tmp_path: Path):
    """A v1 baseline must error, not silently mark everything NEW."""
    import pytest

    from forge_doctor.core.baseline import BaselineError

    legacy = tmp_path / "old.json"
    legacy.write_text(
        json.dumps(
            {
                "format": 1,
                "results": [{"check_id": "X001", "fingerprint": "abc123"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BaselineError, match="fingerprint version"):
        load_baseline(legacy)


def test_apply_baseline_marks_new_existing_and_fixed(tmp_path: Path):
    old = _report(_result("X001"), _result("X002"))
    path = tmp_path / "baseline.json"
    save_baseline(old, path)

    current = _report(_result("X001"), _result("X003"), _result("X004", severity=Severity.ERROR))
    tagged = apply_baseline(current, path)

    assert tagged.baseline is not None
    assert tagged.baseline.existing == 1  # X001 still present
    assert tagged.baseline.new == 2  # X003, X004
    assert tagged.baseline.fixed == 1  # X002 gone
    by_id = {r.check_id: r for r in tagged.results}
    assert by_id["X001"].is_new is False
    assert by_id["X003"].is_new is True
    assert by_id["X004"].is_new is True


def test_exit_code_counts_only_new_errors(tmp_path: Path):
    path = tmp_path / "baseline.json"
    save_baseline(_report(_result("X001", severity=Severity.ERROR)), path)

    # Same pre-existing error -> does not fail.
    same = apply_baseline(_report(_result("X001", severity=Severity.ERROR)), path)
    assert exit_code(same) == 0

    # A brand-new error -> fails.
    fresh = apply_baseline(
        _report(
            _result("X001", severity=Severity.ERROR),
            _result("X009", severity=Severity.ERROR),
        ),
        path,
    )
    assert exit_code(fresh) == 1


def test_fail_on_warning_with_baseline(tmp_path: Path):
    path = tmp_path / "baseline.json"
    save_baseline(_report(_result("X001")), path)
    report = apply_baseline(_report(_result("X001"), _result("X002")), path)
    assert exit_code(report, fail_on="warning") == 1
