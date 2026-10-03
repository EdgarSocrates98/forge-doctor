"""Runs selected checks and isolates per-check failures."""

from __future__ import annotations

import time
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, replace

from forge_doctor_data import __version__
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.fingerprint import assign_fingerprints
from forge_doctor_data.core.incremental import IncrementalPlan, plan_incremental
from forge_doctor_data.core.models import CheckResult, ScanReport, Severity
from forge_doctor_data.core.registry import CheckRegistry
from forge_doctor_data.plugins.protocol import Check


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
        # Incremental diagnostics: the last plan plus check ids whose
        # results were actually reused from a prior scan.
        self.last_plan: IncrementalPlan | None = None
        self.reused: frozenset[str] = frozenset()
        # The raw (pre-profile/policy/baseline) report of the last run -
        # what incremental stores reuse on the next pass.
        self.last_report: ScanReport | None = None

    def _select(self, ctx: ProjectContext) -> list[Check]:
        return self._registry.select(
            categories=ctx.options.categories,
            ignore=(*ctx.config.ignore, *ctx.options.ignore),
        )

    def _execute(self, check: Check, ctx: ProjectContext) -> list[CheckResult]:
        started = time.perf_counter()
        try:
            produced = check.run(ctx)
        except Exception:
            self.failures.append(InternalFailure(check=check, traceback=traceback.format_exc()))
            produced = [_internal_error(check)]
        finally:
            self.timings[check.id] = time.perf_counter() - started
        source = getattr(check, "__fd_source__", None)
        if source is not None:
            produced = [r if r.source is not None else replace(r, source=source) for r in produced]
        return produced

    def _finish(self, ctx: ProjectContext, results: list[CheckResult]) -> ScanReport:
        results = _dedupe(results)
        results = assign_fingerprints(results, ctx)
        report = ScanReport(version=__version__, project=ctx.root, results=results)
        self.last_report = report
        return report

    def run(self, ctx: ProjectContext) -> ScanReport:
        self.failures = []
        self.timings = {}
        self.last_plan = None
        self.reused = frozenset()
        results: list[CheckResult] = []
        for check in self._select(ctx):
            results.extend(self._execute(check, ctx))
        return self._finish(ctx, results)

    def run_incremental(
        self,
        ctx: ProjectContext,
        *,
        changed: frozenset[str],
        prior: Mapping[str, list[CheckResult]],
    ) -> ScanReport:
        """Rerun only checks whose evidence domains the change invalidated.

        Skipped checks reuse ``prior`` results - identical inputs produce
        identical outputs, so the merged report equals a full scan. A
        skipped check with no cached result runs anyway (cold start).
        """
        self.failures = []
        self.timings = {}
        self.reused = frozenset()
        checks = self._select(ctx)
        self.last_plan = plan_incremental(changed, checks)
        results: list[CheckResult] = []
        reused: set[str] = set()
        for check in checks:
            cached = prior.get(check.id)
            if check.id in self.last_plan.rerun or cached is None:
                results.extend(self._execute(check, ctx))
                continue
            reused.add(check.id)
            results.extend(cached)
        self.reused = frozenset(reused)
        return self._finish(ctx, results)


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
