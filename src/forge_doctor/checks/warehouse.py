"""Warehouse checks (WARE###) over the vendor-neutral WarehouseProjectModel.

Only vendor-neutral semantics live here — anything that depends on a
specific platform's behavior belongs to the vendor adapters (specs
213-215).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.warehouse_model import (
    WarehouseProjectModel,
    warehouse_model,
)
from forge_doctor.core.models import Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _model(ctx: ProjectContext) -> WarehouseProjectModel:
    return warehouse_model(ctx)


class _WarehouseCheck(CheckBase):
    category = "warehouse"


class WarehouseUsage(_WarehouseCheck):
    """WARE001: warehouse evidence census — anchor for the family."""

    id = "WARE001"
    title = "Warehouse surface"
    why = "Anchor: sizes the warehouse estate feeding the other WARE checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the warehouse surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no warehouse evidence detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(model.platforms)} platform(s) "
                f"({', '.join(model.platforms)}), {len(model.compute)} compute, "
                f"{len(model.namespaces)} namespaces, {len(model.tables)} tables, "
                f"{len(model.views)} views, {len(model.queries)} queries",
            )
        ]


class UnprofiledTable(_WarehouseCheck):
    """WARE010: declared table with no storage/statistics evidence.

    Vendor-neutral: every table the model knows came from declarative
    evidence (Terraform resource, authored DDL); none carry observed
    size/row stats, so profiling coverage is genuinely absent.
    """

    id = "WARE010"
    title = "Unprofiled warehouse table"
    why = (
        "Tables without observed statistics force cost/skip decisions to be "
        "blind - cardinality estimation, pruning, and layout checks all "
        "degrade."
    )
    when_ok = "Warehouse tables carry observed statistics (row/byte counts)."
    fix = "Ingest table statistics (e.g. catalog exports) so checks can profile them."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        return [
            self.result(
                Severity.INFO,
                f"table {t.name} ({t.platform}) has no observed storage statistics - unprofiled",
                file=t.file,
                line=t.line,
                evidence=self.evidence_at(ctx, t.file, t.line) if t.file is not None else None,
            )
            for t in sorted(model.tables, key=lambda t: (t.platform, t.name))
        ]


class DanglingView(_WarehouseCheck):
    """WARE020: view references a base table the model cannot see."""

    id = "WARE020"
    title = "View references unknown base table"
    why = (
        "A view whose base tables are absent from the project either reads "
        "external objects (fine, but undiagnosed) or is already broken."
    )
    when_ok = "Every view's base tables resolve inside the project model."
    fix = "Declare the base table or mark the view's dependency as external."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        known = model.table_names()
        results = []
        for view in sorted(model.views, key=lambda v: (v.platform, v.name)):
            for base in view.tables_read:
                if base in known or any(
                    base == t.name.rpartition(".")[2] or t.name.endswith(f".{base}")
                    for t in model.tables
                ):
                    continue
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"view {view.name} reads '{base}' - no such table in the model",
                        file=view.file,
                        line=view.line,
                        evidence=self.evidence_at(ctx, view.file, view.line)
                        if view.file is not None
                        else None,
                    )
                )
        return results


class ComputeWithoutWlm(_WarehouseCheck):
    """WARE030: compute declared with no workload-management evidence."""

    id = "WARE030"
    title = "Compute without workload management"
    why = (
        "Clusters/warehouses without queues, reservations, or concurrency "
        "scaling saturate under mixed workloads."
    )
    when_ok = "Each compute surface pairs with workload-management config."
    fix = "Attach WLM/reservation/concurrency config to the compute resource."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        wlm_platforms = {w["platform"] for w in model.workload_management}
        return [
            self.result(
                Severity.INFO,
                f"{c.platform} compute '{c.name}' has no workload-management "
                "evidence (queue/reservation/WLM)",
                file=c.file,
                line=c.line,
            )
            for c in sorted(model.compute, key=lambda c: (c.platform, c.name))
            if c.platform not in wlm_platforms
        ]


CHECKS: tuple[Check, ...] = (
    WarehouseUsage(),
    UnprofiledTable(),
    DanglingView(),
    ComputeWithoutWlm(),
)
