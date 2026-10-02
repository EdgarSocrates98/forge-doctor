"""Azure data-platform checks (AZ###) over the AzurePlatformModel.

Evidence plane: config (Terraform azurerm_* resources). Rules are
deterministic config facts — no cloud calls, no inventory APIs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.azure_model import azure_model
from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


class _AzureCheck(CheckBase):
    category = "azure"


class AzureSurface(_AzureCheck):
    """AZ000: anchor census of the Azure data-platform surface."""

    id = "AZ000"
    title = "Azure data-platform surface"
    why = "Anchor: sizes the Azure estate feeding the AZ checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = azure_model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no Azure data-platform evidence detected")]
        parts = [
            f"{len(model.adls_accounts)} storage accounts",
            f"{len(model.eventhubs)} event hubs",
            f"{len(model.synapse_workspaces)} synapse workspaces",
            f"{len(model.adf_factories)} data factories",
            f"{len(model.purview_accounts)} purview accounts",
        ]
        return [self.result(Severity.INFO, ", ".join(parts))]


class AdlsWithoutHns(_AzureCheck):
    """AZ001: storage account hosting ADLS Gen2 filesystems without HNS."""

    id = "AZ001"
    title = "ADLS Gen2 filesystem on non-hierarchical account"
    why = (
        "ADLS Gen2 semantics (directory ACLs, atomic renames, true "
        "hierarchy) require the hierarchical namespace; a filesystem on a "
        "flat-namespace account silently behaves like plain blob storage."
    )
    when_ok = "Accounts hosting data-lake filesystems set is_hns_enabled = true."
    fix = "Set is_hns_enabled = true on the storage account (recreate if needed)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for acc in azure_model(ctx).adls_accounts:
            if not acc.filesystems:
                continue
            if acc.hns_enabled == "true":
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"storage account '{acc.name}' hosts ADLS filesystems "
                    f"({', '.join(acc.filesystems)}) but is_hns_enabled is "
                    f"{acc.hns_enabled or 'unset'} — flat namespace is not ADLS Gen2",
                    file=acc.file,
                    line=acc.line,
                    evidence=f"is_hns_enabled={acc.hns_enabled or 'missing'}",
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


class EventHubShortRetention(_AzureCheck):
    """AZ002: Event Hub at minimum retention (1 day) — no replay window."""

    id = "AZ002"
    title = "Event Hub with minimum message retention"
    why = (
        "message_retention_in_days=1 gives consumers no replay or "
        "catch-up window: a day of downstream downtime loses the stream."
    )
    when_ok = "Hubs retain >= 2 days (Standard) or use capture."
    fix = "Raise message_retention_in_days or add capture_description."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for hub in azure_model(ctx).eventhubs:
            if hub.capture:
                continue
            retention = hub.message_retention_days
            if retention and retention != "1":
                continue
            out.append(
                self.result(
                    Severity.WARNING,
                    f"event hub '{hub.name}' has "
                    f"message_retention_in_days={retention or '1 (default)'} "
                    "and no capture — a day's outage loses events",
                    file=hub.file,
                    line=hub.line,
                    evidence=f"retention={retention or '1'}, no capture_description",
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


class NoGovernancePlane(_AzureCheck):
    """AZ003: data estate present (ADLS/Synapse/Fabric) with no Purview."""

    id = "AZ003"
    title = "Azure data estate without governance plane"
    why = (
        "Storage + analytics with no Purview account means no declared "
        "classification, lineage, or ownership metadata for the estate."
    )
    when_ok = "A purview account is declared alongside the data estate."
    fix = "Declare azurerm_purview_account (or document the external catalog)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = azure_model(ctx)
        estate = model.adls_accounts or model.synapse_workspaces or model.fabric_capacities
        if not estate or model.purview_accounts:
            return []
        first = (model.adls_accounts + model.synapse_workspaces + model.fabric_capacities)[0]
        return [
            self.result(
                Severity.INFO,
                "azure data estate detected (adls/synapse/fabric) but no "
                "purview account — no governance plane declared",
                file=first.file,
                evidence="no azurerm_purview_account in Terraform",
                evidence_kind=EvidenceKind.CONFIG,
            )
        ]


CHECKS: tuple[Check, ...] = (
    AzureSurface(),
    AdlsWithoutHns(),
    EventHubShortRetention(),
    NoGovernancePlane(),
)
