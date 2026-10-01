"""Exit-code and summary helpers."""

from __future__ import annotations

from forge_doctor.core.models import ScanReport, Severity

_EXIT_OK = 0
_EXIT_ERRORS = 1
_EXIT_INTERNAL = 2


def exit_code(report: ScanReport, fail_on: str = "error") -> int:
    """Map a report to a process exit code.

    0 = nothing at or above the threshold; 1 = threshold hit; warnings alone
    never fail unless ``fail_on="warning"``. With a baseline, only findings
    **new** since the baseline count — pre-existing debt does not fail CI.
    """
    threshold = Severity.parse(fail_on)
    results = report.new_results
    errors = sum(1 for r in results if r.severity is Severity.ERROR)
    if threshold is Severity.ERROR:
        return _EXIT_ERRORS if errors else _EXIT_OK
    warnings = sum(1 for r in results if r.severity is Severity.WARNING)
    return _EXIT_ERRORS if errors + warnings else _EXIT_OK


INTERNAL_ERROR_EXIT = _EXIT_INTERNAL
