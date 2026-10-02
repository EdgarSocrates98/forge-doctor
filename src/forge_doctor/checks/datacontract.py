"""Data contract checks (DCTR###) over the DataContractModel (spec 217).

Evidence is declarative — contract YAML/JSON is config-plane evidence.
DCTR003 compares contract field types against *detected* real schemas
(SQL DDL, Terraform schemas, observed column exports) and marks the
finding with the plane that supplied the detected schema.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.datacontract_model import DataContractModel, datacontract_model
from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _model(ctx: ProjectContext) -> DataContractModel:
    return datacontract_model(ctx)


_EVIDENCE = {
    "static": EvidenceKind.STATIC,
    "config": EvidenceKind.CONFIG,
    "observed_metadata": EvidenceKind.OBSERVED_METADATA,
}


class _DctrCheck(CheckBase):
    category = "datacontract"


class ContractSurface(_DctrCheck):
    """DCTR000: anchor census of the contract surface."""

    id = "DCTR000"
    title = "Data contract surface"
    why = "Anchor: sizes the contract estate feeding the DCTR checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no data contract files detected")]
        fields = sum(c.schema_fields for c in model.contracts)
        sla = sum(len(c.sla) for c in model.contracts)
        return [
            self.result(
                Severity.INFO,
                f"{len(model.contracts)} contract(s): {fields} fields, "
                f"{sla} SLA props, "
                f"{sum(len(c.servers) for c in model.contracts)} servers, "
                f"{len(model.detected)} detected schema(s), "
                f"{len(model.unparsed)} unparsed",
            )
        ]


class MissingSchema(_DctrCheck):
    """DCTR001: contract declares no schema fields."""

    id = "DCTR001"
    title = "Contract missing schema section"
    why = (
        "A contract without schema is a promise without content — "
        "consumers cannot type-check and schema drift is invisible."
    )
    when_ok = "Every contract declares at least one schema object with fields."
    fix = "Add a schema: section with named objects and typed fields."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"contract '{c.id}' declares no schema fields — consumers "
                "have nothing to type-check against",
                file=c.file,
                evidence="contract file parsed; no schema fields found",
                evidence_kind=EvidenceKind.CONFIG,
            )
            for c in _model(ctx).contracts
            if c.schema_fields == 0
        ]


class ProdWithoutSla(_DctrCheck):
    """DCTR002: production-claimed contract without SLA/freshness."""

    id = "DCTR002"
    title = "Production contract without SLA"
    why = (
        "A contract served on a production server without servicelevels "
        "or slaProperties leaves availability and freshness unguarded."
    )
    when_ok = "Every production-claimed contract carries SLA properties."
    fix = "Add servicelevels (availability/freshness/retention) or slaProperties."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"contract '{c.id}' declares a production server but no SLA "
                "properties — availability and freshness are unguarded",
                file=c.file,
                evidence="production server present; no servicelevels/slaProperties",
                evidence_kind=EvidenceKind.CONFIG,
            )
            for c in _model(ctx).contracts
            if c.claims_production() and not c.sla
        ]


class TypeDrift(_DctrCheck):
    """DCTR003: contract field type vs detected real schema (cross-domain)."""

    id = "DCTR003"
    title = "Contract field type drift"
    why = (
        "When the contract's declared type disagrees with the schema the "
        "platform actually built, one of them is lying to consumers."
    )
    when_ok = "Contract field families match detected table schemas."
    fix = "Align the contract field type or the table DDL; re-scan."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        out: list[CheckResult] = []
        for c in model.contracts:
            for obj in c.objects:
                detected = model.detected.get(obj.name.rpartition(".")[2].lower())
                if detected is None:
                    continue
                for f in obj.fields:
                    real = detected.fields.get(f.name)
                    if real is not None and real != f.type:
                        out.append(
                            self.result(
                                Severity.WARNING,
                                f"contract '{c.id}' field '{f.name}' declares "
                                f"'{f.raw_type or f.type}' but detected schema "
                                f"'{detected.object_name}' has '{real}'",
                                file=c.file,
                                evidence=(
                                    f"contract {f.name}={f.raw_type or f.type} vs "
                                    f"detected {detected.object_name}.{f.name}={real}"
                                ),
                                evidence_kind=_EVIDENCE[detected.evidence],
                            )
                        )
        return out


CHECKS: tuple[Check, ...] = (
    ContractSurface(),
    MissingSchema(),
    ProdWithoutSla(),
    TypeDrift(),
)
