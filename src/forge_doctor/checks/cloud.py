"""Multi-cloud abstraction checks (spec 223).

`CloudAbstractionModel` is a view over existing entities — Terraform
``azurerm_*``/``google_*``/``aws_*`` resources and vendor-attributed
graph entities fold into six vendor-neutral abstractions. The checks
fire only on cross-cloud parity rules: coverage blind spots in the
mapping itself, and mixed-cloud estates where an abstraction exists on
one cloud only without a declared replication/migration link.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.abstractions import abstractions_model
from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

_CLOUD001_CAP = 15


class _CloudCheck(CheckBase):
    category = "cloud"


class CloudSurface(_CloudCheck):
    """CLOUD000: anchor census of the abstraction view."""

    id = "CLOUD000"
    title = "Multi-cloud abstraction surface"
    why = "Anchor: sizes the vendor-neutral view over detected services."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = abstractions_model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no cloud-platform evidence")]
        kinds = ", ".join(f"{k}={len(v)}" for k, v in sorted(model.kind_clouds().items()))
        return [
            self.result(
                Severity.INFO,
                f"{len(model.services)} abstracted services across "
                f"{len(model.clouds())} clouds "
                f"({', '.join(sorted(model.clouds())) or '-'}): {kinds}",
            )
        ]


class UnmappedPlatformService(_CloudCheck):
    """CLOUD001: platform entity with no abstraction mapping."""

    id = "CLOUD001"
    title = "Platform entity outside abstraction coverage"
    why = (
        "Entities in platform domains that don't resolve to an "
        "abstraction are blind spots in the vendor-neutral view — an "
        "internal signal, not a user defect."
    )
    when_ok = "Every platform-domain entity maps to an abstraction."
    fix = "Extend the abstraction map for this domain/kind."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = abstractions_model(ctx)
        if not model.unmapped:
            return []
        out: list[CheckResult] = []
        seen: set[tuple[str, str]] = set()
        for u in model.unmapped:
            key = (u.domain, u.kind)
            if key in seen:
                continue
            seen.add(key)
            if len(out) >= _CLOUD001_CAP:
                break
            out.append(
                self.result(
                    Severity.INFO,
                    f"{u.domain} {u.kind} '{u.name}' has no abstraction "
                    "mapping (coverage blind spot)",
                    file=u.file,
                    evidence="abstraction map gap",
                )
            )
        return out


class SingleCloudAbstraction(_CloudCheck):
    """CLOUD002: abstraction kind on one cloud only in a mixed estate."""

    id = "CLOUD002"
    title = "Single-cloud abstraction in mixed-cloud estate"
    why = (
        "A mixed-cloud project with an abstraction kind present on only "
        "one cloud risks drift — the equivalent service may be missing "
        "or unreplicated on the other clouds."
    )
    when_ok = "Each abstraction kind is multi-cloud or replication-linked."
    fix = "Declare a replication/migration link or deploy the equivalent."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = abstractions_model(ctx)
        clouds = model.clouds() - {"vendor"}  # neutral rows aren't a cloud
        if len(clouds) < 2:
            return []
        out: list[CheckResult] = []
        for kind, kind_clouds in sorted(model.kind_clouds().items()):
            real = kind_clouds - {"vendor"}
            if len(real) != 1:
                continue
            members = [s for s in model.services if s.abstraction == kind]
            if any(s.linked for s in members):
                continue  # replication/migration link declared
            cloud = sorted(real)[0]
            missing = sorted(clouds - real)
            names = ", ".join(sorted({f"{s.service}:{s.name}" for s in members})[:4])
            out.append(
                self.result(
                    Severity.INFO,
                    f"{kind} exists only on {cloud} ({names}) — no "
                    f"{', '.join(missing)} equivalent or declared "
                    "replication/migration link",
                    file=members[0].file if members else None,
                    evidence=(f"clouds in estate: {sorted(clouds)}; {kind} on: {sorted(real)}"),
                    evidence_kind=EvidenceKind.CONFIG,
                )
            )
        return out


CHECKS: tuple[Check, ...] = (
    CloudSurface(),
    UnmappedPlatformService(),
    SingleCloudAbstraction(),
)
