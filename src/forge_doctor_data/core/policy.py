"""Policy as Code: custom severity floors and governed suppressions.

Profiles stay built-in; ``[tool.forge-doctor-data.policy]`` layers per-rule
severity floors and kill switches on top, and
``[[tool.forge-doctor-data.suppressions]]`` replaces eternal ``ignore`` lists with
scoped, owned, *expiring* exceptions. An expired suppression stops
suppressing - the finding comes back and a POLICY001 warning says why.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatch

from forge_doctor_data.core.config import ForgeDoctorDataConfig, Suppression
from forge_doctor_data.core.models import (
    CheckResult,
    ScanReport,
    Severity,
    SuppressionRecord,
)

POLICY_CATEGORY = "policy"
EXPIRED_CHECK_ID = "POLICY001"
UNUSED_CHECK_ID = "POLICY002"


@dataclass(frozen=True)
class SuppressionStatus:
    """Audit record for one configured suppression."""

    suppression: Suppression
    status: str  # "active" | "expired" | "unused"
    matched: int = 0


def is_expired(suppression: Suppression, today: date) -> bool:
    if suppression.expires is None:
        return False
    try:
        return date.fromisoformat(suppression.expires) < today
    except ValueError:
        return False  # malformed date: treat as non-expiring, surface in verify


def _matches(suppression: Suppression, result: CheckResult) -> bool:
    if suppression.rule != result.check_id:
        return False
    if suppression.path is not None and (
        result.file is None or not fnmatch(result.file.as_posix(), suppression.path)
    ):
        return False
    return suppression.line is None or suppression.line == result.line


def apply_policy(
    report: ScanReport,
    config: ForgeDoctorDataConfig,
    today: date | None = None,
) -> ScanReport:
    """Apply rule overrides and suppressions; returns an annotated report."""
    today = today or date.today()
    rules = config.policy.rules
    suppressions = config.suppressions
    if not rules and not suppressions:
        return report

    live = [s for s in suppressions if not is_expired(s, today)]
    expired = [s for s in suppressions if is_expired(s, today)]

    kept: list[CheckResult] = []
    for result in report.results:
        override = rules.get(result.check_id)
        if override is not None and not override.enabled:
            continue
        if override is not None and override.severity is not None:
            try:
                target = Severity.parse(override.severity)
            except ValueError:
                target = None  # invalid value surfaced by `doctor` config check
            if target is not None and result.severity is not Severity.PASS:
                result = dataclasses.replace(result, severity=target)
        if not any(_matches(s, result) for s in live):
            kept.append(result)

    for suppression in expired:
        kept.append(_expired_finding(suppression))

    statuses = suppression_statuses(suppressions, report.results, today)
    records = tuple(
        SuppressionRecord(
            rule=s.suppression.rule,
            path=s.suppression.path,
            reason=s.suppression.reason,
            owner=s.suppression.owner,
            expires=s.suppression.expires,
            approved_by=s.suppression.approved_by,
            status=s.status,
            matched=s.matched,
        )
        for s in statuses
    )
    return dataclasses.replace(report, results=kept, suppressions=records)


def suppression_statuses(
    suppressions: tuple[Suppression, ...],
    results: list[CheckResult],
    today: date | None = None,
) -> list[SuppressionStatus]:
    """ACTIVE / EXPIRED / UNUSED for every configured suppression."""
    today = today or date.today()
    statuses: list[SuppressionStatus] = []
    for suppression in suppressions:
        if is_expired(suppression, today):
            statuses.append(SuppressionStatus(suppression, "expired", 0))
            continue
        matched = sum(1 for r in results if _matches(suppression, r))
        statuses.append(SuppressionStatus(suppression, "active" if matched else "unused", matched))
    return statuses


def _expired_finding(suppression: Suppression) -> CheckResult:
    return CheckResult(
        check_id=EXPIRED_CHECK_ID,
        title="Expired suppression",
        severity=Severity.WARNING,
        category=POLICY_CATEGORY,
        message=(
            f"suppression for {suppression.rule}"
            + (f" on {suppression.path}" if suppression.path else "")
            + f" expired {suppression.expires}"
        ),
        recommendation="Renew with a new expires date or remove the suppression.",
    )
