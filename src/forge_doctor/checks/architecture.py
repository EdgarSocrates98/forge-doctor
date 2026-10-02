"""ARCH### - architecture contract drift checks.

Each check compares the declared contract (``platform-contract.yml``)
against implemented/declared planes via :func:`detect_drift`. Runtime-plane
drift (ARCH005, runtime ARCH001) is additionally surfaced through
``forge-doctor architecture drift --runtime <artifact>``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.core.contract import detect_drift
from forge_doctor.core.models import CheckResult, Confidence, EvidenceKind
from forge_doctor.plugins.protocol import CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext

_META = {
    "ARCH001": (
        "Runtime/implemented platform differs from contract",
        "contract pins a compute platform; code or IaC uses another",
        "declared and implemented platforms agree",
        "Align the contract or the implementation; platform drift breaks capability assumptions",
    ),
    "ARCH002": (
        "Version drift vs contract",
        "contracted runtime version differs from the configured one",
        "configured version matches the contract",
        "Update the contract or the configured version",
    ),
    "ARCH003": (
        "Storage-format drift",
        "contracted storage format differs from implemented writes",
        "implemented format matches the contract",
        "Reconcile contract format with actual write paths",
    ),
    "ARCH004": (
        "Undeclared dependency",
        "code/IaC uses a service absent from allowed_dependencies",
        "every observed dependency is declared",
        "Add the dependency to the contract or remove the usage",
    ),
    "ARCH005": (
        "SLA runtime violation",
        "runtime artifact shows an execution exceeding the contracted SLA",
        "runtime durations stay within SLA",
        "Investigate the violating execution; adjust SLA or pipeline",
    ),
    "ARCH006": (
        "Idempotency contract lacks evidence",
        "contract requires idempotent writes; write paths show none",
        "idempotent contracts have merge/upsert/dedup evidence",
        "Make sinks idempotent or relax the contract explicitly",
    ),
    "ARCH007": (
        "Ownership conflict",
        "the same resource is provisioned by multiple systems",
        "each resource has exactly one owner",
        "Pick one provisioning owner (Terraform, manual CLI, ...) per resource",
    ),
    "ARCH008": (
        "Feature outside approved capability",
        "implemented features exceed the pipeline's approved capabilities",
        "implemented features are all approved",
        "Extend approved capabilities or remove the feature",
    ),
}


class _ArchCheck(CheckBase):
    category = "architecture"
    confidence = Confidence.HIGH
    arch_id: str

    def __init__(self, arch_id: str) -> None:
        self.arch_id = arch_id
        self.id = arch_id
        self.title, self.why, self.when_ok, self.fix = _META[arch_id]
        if arch_id in {"ARCH005"}:
            self.evidence_kind = EvidenceKind.RUNTIME
        elif arch_id in {"ARCH002"}:
            self.evidence_kind = EvidenceKind.CONFIG
        else:
            self.evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        contract = ctx.contract
        if contract is None or not contract.ok:
            return []
        results = []
        for drift in detect_drift(ctx, contract):
            if drift.check_id != self.id:
                continue
            results.append(
                self.result(
                    drift.severity,
                    f"{drift.message} [expected={drift.expected} "
                    f"observed={drift.observed} via {drift.source}]",
                )
            )
        return results


CHECKS = [_ArchCheck(f"ARCH{n:03d}") for n in range(1, 9)]
