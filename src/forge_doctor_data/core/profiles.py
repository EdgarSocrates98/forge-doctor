"""Scan profiles: named severity policies applied after checks run.

A profile never invents findings - it only upgrades severities of results
the deterministic engine already produced, so ``security`` can demand SHA
pins where ``default`` accepts a release tag.
"""

from __future__ import annotations

import dataclasses

from forge_doctor_data.core.models import ScanReport, Severity

# check_id -> minimum severity. Results below the floor are raised to it;
# PASS results and already-higher severities are untouched.
_UPGRADES: dict[str, dict[str, Severity]] = {
    "default": {},
    "strict": {
        "CI002": Severity.WARNING,
        "CI003": Severity.WARNING,
        "DEP005": Severity.WARNING,
        "GLUE002": Severity.WARNING,
    },
    "security": {
        "CI001": Severity.WARNING,
        "CI002": Severity.WARNING,
        "GIT002": Severity.ERROR,
        "DOCKER004": Severity.ERROR,
    },
    "spark-performance": {
        "SPARK004": Severity.WARNING,
        "SPARK008": Severity.WARNING,
        "SPARK010": Severity.WARNING,
    },
    "glue-migration": {
        "GLUE002": Severity.WARNING,
        "GLUE003": Severity.WARNING,
        "GLUE004": Severity.WARNING,
    },
}
_UPGRADES["production"] = {**_UPGRADES["strict"], **_UPGRADES["security"]}

_ORDER = [Severity.PASS, Severity.INFO, Severity.WARNING, Severity.ERROR]

PROFILES = tuple(_UPGRADES)


def apply_profile(report: ScanReport, profile: str) -> ScanReport:
    """Raise severities to the profile's floors; identity for ``default``."""
    floors = _UPGRADES.get(profile, {})
    if not floors:
        return report
    results = []
    for result in report.results:
        floor = floors.get(result.check_id)
        if (
            floor is not None
            and result.severity is not Severity.PASS
            and _ORDER.index(result.severity) < _ORDER.index(floor)
        ):
            result = dataclasses.replace(result, severity=floor)
        results.append(result)
    return dataclasses.replace(report, results=results)
