"""dbt checks (DBT###) over the vendor DbtProjectModel.

Evidence is declarative — ``dbt_project.yml``, model SQL jinja surface
(``config``/``ref``/``source``), ``schema.yml`` tests/freshness, and
``target/manifest.json``/``run_results.json`` as observed artifacts.
No ``dbt`` process is ever invoked; profiles.yml secrets stay out of
findings (key names only, per spec 216).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor_data.analyzers.dbt_model import DbtProjectModel, dbt_model
from forge_doctor_data.core.models import Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult


def _model(ctx: ProjectContext) -> DbtProjectModel:
    return dbt_model(ctx)


class _DbtCheck(CheckBase):
    category = "dbt"


class DbtUsage(_DbtCheck):
    """DBT000: anchor census of the dbt surface."""

    id = "DBT000"
    title = "dbt surface"
    why = "Anchor: sizes the dbt estate feeding the DBT checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no dbt evidence detected")]
        return [
            self.result(
                Severity.INFO,
                f"project '{model.project_name or '?'}': {len(model.models)} models "
                f"({model.materialized_counts()}), {len(model.sources)} sources, "
                f"{len(model.seeds)} seeds, {len(model.snapshots)} snapshots, "
                f"{len(model.exposures)} exposures, "
                f"{len(model.singular_tests)} singular tests, "
                f"{model.manifest_nodes} manifest nodes",
            )
        ]


class ModelWithoutTests(_DbtCheck):
    """DBT001: model carrying no declared test."""

    id = "DBT001"
    title = "Model without tests"
    why = (
        "Untested models ship silent data bugs — schema.yml tests "
        "(unique/not_null/relationships/accepted_values) are the cheap "
        "contract layer."
    )
    when_ok = "Every model has at least one declared or singular test."
    fix = "Add a schema.yml test block (or a tests/*.sql singular test)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        singular = {t.lower() for t in model.singular_tests}
        results = []
        for m in model.models:
            covered = (
                bool(m.tests)
                or m.name.lower() in singular
                or any(m.name.lower() in t for t in singular)
            )
            if not covered:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"model '{m.name}' has no declared tests",
                        file=m.file,
                        evidence=f"no tests in schema yml for {m.name}",
                    )
                )
        return results


class IncrementalWithoutUniqueKey(_DbtCheck):
    """DBT002: incremental model without ``unique_key``."""

    id = "DBT002"
    title = "Incremental model without unique_key"
    why = (
        "An incremental model with no unique_key appends duplicates on "
        "re-runs — the most common dbt correctness bug."
    )
    when_ok = "Every incremental model declares unique_key."
    fix = "Add unique_key = '<pk>' to the model's config block."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"incremental model '{m.name}' has no unique_key",
                file=m.file,
                evidence=self.evidence_at(ctx, m.file, 1),
            )
            for m in _model(ctx).models
            if m.materialized == "incremental" and not m.unique_key
        ]


class SourceWithoutFreshness(_DbtCheck):
    """DBT003: source without a freshness block."""

    id = "DBT003"
    title = "Source without freshness"
    why = (
        "Sources are where upstream SLAs live; without freshness there is "
        "no signal that the producer stopped."
    )
    when_ok = "Every source table declares a freshness block."
    fix = "Add freshness: warn_after/error_after on the source table."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"source '{s.name}' declares no freshness block",
                file=s.file,
                evidence=f"sources entry in {s.file.as_posix()}",
            )
            for s in _model(ctx).sources
            if not s.has_freshness
        ]


class DeclaredSourceNeverReferenced(_DbtCheck):
    """DBT004: source declared but never ``source()``-ed."""

    id = "DBT004"
    title = "Source declared but never referenced"
    why = (
        "A declared-but-unused source is dead contract surface — either "
        "stale documentation or a model that bypassed source()."
    )
    when_ok = "Every declared source table is referenced via source()."
    fix = "Reference it via {{ source('name','table') }} or remove the declaration."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        used = {s for m in model.models for s in m.sources_used}
        return [
            self.result(
                Severity.INFO,
                f"source '{s.name}' is declared but no model references it",
                file=s.file,
                evidence=f"sources entry in {s.file.as_posix()}",
            )
            for s in model.sources
            if s.name not in used
        ]


class DocCoverageBelowThreshold(_DbtCheck):
    """DBT005: model descriptions below coverage threshold."""

    id = "DBT005"
    title = "Low model doc coverage"
    why = (
        "Descriptions are the contract consumers read; below threshold "
        "the project is self-serve-unfriendly."
    )
    when_ok = "At least half the models carry a description."
    fix = "Add `description:` on models or a docs block."

    _THRESHOLD = 0.5

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        coverage = model.doc_coverage()
        if not model.models or coverage >= self._THRESHOLD:
            return []
        return [
            self.result(
                Severity.INFO,
                f"{coverage:.0%} of {len(model.models)} models carry a "
                f"description (threshold {self._THRESHOLD:.0%})",
                evidence="schema.yml descriptions counted",
            )
        ]


CHECKS: tuple[Check, ...] = (
    DbtUsage(),
    ModelWithoutTests(),
    IncrementalWithoutUniqueKey(),
    SourceWithoutFreshness(),
    DeclaredSourceNeverReferenced(),
    DocCoverageBelowThreshold(),
)
