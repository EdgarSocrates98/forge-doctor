"""Runs selected checks and isolates per-check failures."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, replace

from forge_doctor import __version__
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.fingerprint import assign_fingerprints
from forge_doctor.core.models import CheckResult, ScanReport, Severity
from forge_doctor.core.registry import CheckRegistry
from forge_doctor.plugins.protocol import Check


@dataclass(frozen=True)
class InternalFailure:
    """A check that crashed; details surfaced only under ``--verbose``."""

    check: Check
    traceback: str


class CheckRunner:
    def __init__(self, registry: CheckRegistry) -> None:
        self._registry = registry
        self.failures: list[InternalFailure] = []
        # check id -> seconds, populated for --stats diagnostics
        self.timings: dict[str, float] = {}

    def run(self, ctx: ProjectContext) -> ScanReport:
        checks = self._registry.select(
            categories=ctx.options.categories,
            ignore=(*ctx.config.ignore, *ctx.options.ignore),
        )
        results: list[CheckResult] = []
        self.failures = []
        self.timings = {}
        for check in checks:
            started = time.perf_counter()
            try:
                produced = check.run(ctx)
            except Exception:
                self.failures.append(InternalFailure(check=check, traceback=traceback.format_exc()))
                results.append(_internal_error(check))
                continue
            finally:
                self.timings[check.id] = time.perf_counter() - started
            source = getattr(check, "__fd_source__", None)
            if source is not None:
                produced = [
                    r if r.source is not None else replace(r, source=source) for r in produced
                ]
            results.extend(produced)
        results = _dedupe(results)
        results = assign_fingerprints(results, ctx)
        return ScanReport(version=__version__, project=ctx.root, results=results)


def _dedupe(results: list[CheckResult]) -> list[CheckResult]:
    """Drop exact-duplicate findings before fingerprints are assigned."""
    seen: set[tuple[object, ...]] = set()
    unique: list[CheckResult] = []
    for result in results:
        key = (
            result.check_id,
            result.file,
            result.line,
            result.column,
            result.message,
            result.severity,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


def _internal_error(check: Check) -> CheckResult:
    return CheckResult(
        check_id=check.id,
        title=check.title,
        severity=Severity.ERROR,
        category="internal",
        message=f"Unexpected error while running {check.id}.",
        recommendation="Run with --verbose for details.",
    )
