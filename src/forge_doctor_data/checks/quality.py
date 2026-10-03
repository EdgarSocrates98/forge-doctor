"""Data-quality coverage checks (spec 222).

`DataQualityModel` captures declared expectation suites (Deequ, Great
Expectations, SodaCL, dbt tests) and the gates that run them. The
checks compare declared quality intent against the detected platform
graph — prod tables with no coverage, defined-but-unwired suites,
stale suites targeting absent tables, and column expectations on
fields the detected schema no longer carries.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from forge_doctor_data.analyzers.quality_model import quality_model
from forge_doctor_data.core.models import Confidence, EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult
    from forge_doctor_data.core.platform_graph import DataPlatformGraph, Entity

_DQ002_CAP = 10

_DATA_KIND_VALUES = {"table", "dataset", "view", "dbt_model", "stream"}
_PROD_RE = re.compile(r"(?:^|[\W_])prod(?:uction)?(?:$|[\W_])", re.I)


class _QualityCheck(CheckBase):
    category = "quality"


def _graph(ctx: ProjectContext) -> DataPlatformGraph:
    from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph

    return build_platform_graph(ctx)


def _entity_names(g: DataPlatformGraph) -> dict[str, Entity]:
    """tail-name -> entity for *detected* data-bearing entities."""
    out: dict[str, Entity] = {}
    for e in g.entities():
        if e.kind.value not in _DATA_KIND_VALUES or e.domain == "metadata":
            continue
        tail = e.identifier.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()
        out.setdefault(tail, e)
        out.setdefault(e.identifier.lower(), e)
    return out


def _attrs(e: Entity) -> dict[str, str]:
    return dict(e.attrs)


def _is_prod(e: Entity) -> bool:
    for key in ("env", "environment", "fabric"):
        v = _attrs(e).get(key)
        if isinstance(v, str) and v.upper() == "PROD":
            return True
    if _PROD_RE.search(e.identifier):
        return True
    ident = e.identifier.lower()
    return "prod" in ident.split("/") or "prod" in ident.split(".")


class QualitySurface(_QualityCheck):
    """DQ000: anchor census of declared data-quality evidence."""

    id = "DQ000"
    title = "Data quality surface"
    why = "Anchor: sizes declared expectation suites and gate wiring."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = quality_model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no data-quality evidence detected")]
        engines = ", ".join(sorted(model.engines())) or "-"
        return [
            self.result(
                Severity.INFO,
                f"{len(model.suites)} expectation suites ({engines}), "
                f"{len(model.gates)} gates, "
                f"{len(model.observed_runs)} observed runs, "
                f"{len(model.covered_tables())} tables covered",
            )
        ]


class ProdTableNoExpectations(_QualityCheck):
    """DQ001: prod-signaled table with zero expectations (practice exists)."""

    id = "DQ001"
    title = "Production table without quality expectations"
    why = (
        "The project declares quality suites, so the practice exists — "
        "a prod-signaled table with zero expectations is a real coverage "
        "gap, not an absence of practice."
    )
    when_ok = "Every prod-signaled detected table has expectations."
    fix = "Add an expectation suite covering the prod table."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = quality_model(ctx)
        if not model.suites:
            return []  # no practice in project — gap is undefined
        covered = model.covered_tables()
        out: list[CheckResult] = []
        for e in _graph(ctx).entities():
            if e.kind.value not in _DATA_KIND_VALUES or e.domain == "metadata":
                continue
            if not _is_prod(e):
                continue
            tail = e.identifier.rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()
            if tail in covered or e.identifier.lower() in covered:
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"prod-signaled {e.kind.value} '{e.identifier}' has no "
                    "expectation suite — coverage gap",
                    file=e.file,
                    evidence=f"suites cover: {sorted(covered)[:8]}",
                    confidence=Confidence.MEDIUM,
                )
            )
        return out


class SuiteNeverWired(_QualityCheck):
    """DQ002: suite defined but no checkpoint/pipeline reference."""

    id = "DQ002"
    title = "Expectation suite never wired to a gate"
    why = (
        "A suite with no checkpoint, CI invocation, or observed run is "
        "defined but never executed — quality intent without enforcement."
    )
    when_ok = "Every declared suite is referenced by a gate or observed run."
    fix = "Wire the suite into a checkpoint/pipeline or remove it."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        unwired = quality_model(ctx).unwired_suites()
        out: list[CheckResult] = []
        for s in unwired[:_DQ002_CAP]:
            out.append(
                self.result(
                    Severity.WARNING,
                    f"{s.engine} suite '{s.name}' has no gate wiring — defined but never run",
                    file=s.file,
                    evidence=f"{len(s.expectations)} expectations declared",
                )
            )
        if len(unwired) > _DQ002_CAP:
            out.append(
                self.result(
                    Severity.INFO,
                    f"+{len(unwired) - _DQ002_CAP} more unwired suites",
                )
            )
        return out


class StaleQualitySuite(_QualityCheck):
    """DQ003: suite covers a table absent from the detected graph."""

    id = "DQ003"
    title = "Quality suite targets a missing table"
    why = (
        "The suite's target table has no detected entity — either the "
        "table was dropped or the suite is stale."
    )
    when_ok = "Every suite's target tail-matches a detected entity."
    fix = "Retarget or remove the stale suite."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = quality_model(ctx)
        targets = {s for s in model.suites if s.table}
        if not targets:
            return []
        detected = _entity_names(_graph(ctx))
        if not detected:
            return []
        out: list[CheckResult] = []
        for s in targets:
            if s.table.lower() in detected:
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"{s.engine} suite '{s.name}' targets '{s.table}' — no "
                    "detected entity matches (stale suite?)",
                    file=s.file,
                    evidence=f"declared target: {s.table}",
                    evidence_kind=EvidenceKind.OBSERVED_METADATA,
                )
            )
        return out


class DroppedColumnExpectation(_QualityCheck):
    """DQ004: expectation on a column absent from the detected schema."""

    id = "DQ004"
    title = "Expectation on dropped column"
    why = (
        "The suite expects a column the detected schema no longer carries — contract/quality drift."
    )
    when_ok = "Every column expectation exists in the detected field set."
    fix = "Update the suite or restore the column."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = quality_model(ctx)
        detected = _entity_names(_graph(ctx))
        out: list[CheckResult] = []
        for s in model.suites:
            if not s.table or not s.columns:
                continue
            ent = detected.get(s.table.lower())
            if ent is None:
                continue
            fields = {k.split(".", 1)[1].lower() for k in _attrs(ent) if k.startswith("field.")}
            if not fields:
                continue  # schema unknown — can't prove drift
            for col in s.columns:
                if col.lower() not in fields:
                    out.append(
                        self.result(
                            Severity.WARNING,
                            f"{s.engine} suite '{s.name}' expects column "
                            f"'{col}' on '{s.table}' — detected schema "
                            "lacks it (dropped?)",
                            file=s.file,
                            evidence=(
                                f"declared column '{col}' vs detected fields {sorted(fields)[:8]}"
                            ),
                            evidence_kind=EvidenceKind.OBSERVED_METADATA,
                        )
                    )
        return out


CHECKS: tuple[Check, ...] = (
    QualitySurface(),
    ProdTableNoExpectations(),
    SuiteNeverWired(),
    StaleQualitySuite(),
    DroppedColumnExpectation(),
)
