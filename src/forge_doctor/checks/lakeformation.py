"""Lake Formation checks (LF###) over IaC facts - evidence-gated, no AWS calls."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge_doctor.analyzers.hcl_lite import IaCResource, project_iac
from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

_LF_TYPE_RE = re.compile(r"^(aws_lakeformation_|aws::lakeformation::)", re.IGNORECASE)
_CATALOG_TYPE_RE = re.compile(r"^(aws_glue_catalog_|aws::glue::(database|table))", re.IGNORECASE)
_RAM_TYPE_RE = re.compile(r"^(aws_ram_|aws::ram::)", re.IGNORECASE)
_TARGET_TOKEN_RE = re.compile(
    r"target[_-]?(database|table|catalog)|resource[_-]?link", re.IGNORECASE
)
_PRINCIPALS_RE = re.compile(r"iam_?allowed_?principals", re.IGNORECASE)
_FGAC_TOKEN_RE = re.compile(r"lf_?tags?|lftag|fgac", re.IGNORECASE)


def _iac(ctx: ProjectContext) -> list[IaCResource]:
    return project_iac(ctx.files, ctx.root)


def _file_texts(ctx: ProjectContext, resources: list[IaCResource]) -> dict[Path, str]:
    """Text of every file that produced an IaC resource (read once)."""
    texts: dict[Path, str] = {}
    for resource in resources:
        relative = Path(resource.file)
        if relative not in texts:
            texts[relative] = ctx.read_text(relative) or ""
    return texts


def _is_lf(resource: IaCResource) -> bool:
    return bool(_LF_TYPE_RE.match(resource.type))


def _is_catalog(resource: IaCResource) -> bool:
    return bool(_CATALOG_TYPE_RE.match(resource.type))


class _LfCheck(CheckBase):
    category = "lakeformation"
    evidence_kind = EvidenceKind.CONFIG


class LakeFormationUsage(_LfCheck):
    """LF000: how much of the project declares Lake Formation resources."""

    id = "LF000"
    title = "Lake Formation usage"
    why = "Anchor: sizes the Lake Formation surface feeding the other LF checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the evidence counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        resources = _iac(ctx)
        lf = [r for r in resources if _is_lf(r)]
        if not lf:
            return [self.result(Severity.PASS, "no Lake Formation resources detected")]
        kinds: dict[str, int] = {}
        for r in lf:
            kinds[r.type] = kinds.get(r.type, 0) + 1
        detail = ", ".join(f"{k}={n}" for k, n in sorted(kinds.items()))
        return [
            self.result(
                Severity.INFO,
                f"{len(lf)} Lake Formation resources ({detail})",
            )
        ]


class ResourceLinkWithoutShare(_LfCheck):
    """LF001: resource-link/cross-account target with no RAM/share evidence."""

    id = "LF001"
    title = "Resource link without RAM share"
    why = (
        "A cross-account resource link needs a RAM share (or grant) on the "
        "producer side - a link alone resolves to nothing."
    )
    when_ok = "Resource links exist and RAM/share resources are declared too."
    fix = (
        "Add an aws_ram_resource_share/AWS::RAM::* share for the linked object, or remove the link."
    )
    evidence_kind = EvidenceKind.DERIVED  # link evidence + share absence
    confidence = Confidence.MEDIUM  # file-level token scan, not per-attr proof

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        resources = _iac(ctx)
        texts = _file_texts(ctx, resources)
        candidates = [r for r in resources if _is_lf(r) or _is_catalog(r)]
        has_ram = any(_RAM_TYPE_RE.match(r.type) for r in resources) or any(
            re.search(r"aws_ram_|AWS::RAM", text) for text in texts.values()
        )
        if has_ram:
            return []
        results: list[CheckResult] = []
        flagged: set[Path] = set()
        for resource in candidates:
            if any(_TARGET_TOKEN_RE.search(str(k)) for k in resource.attrs):
                flagged.add(Path(resource.file))
        for relative, text in texts.items():
            if relative not in flagged and _TARGET_TOKEN_RE.search(text):
                flagged.add(relative)
        for relative in sorted(flagged):
            results.append(
                self.result(
                    Severity.WARNING,
                    "resource-link/cross-account target declared but no RAM "
                    "share evidence in project",
                    file=relative,
                )
            )
        return results


class HybridAccessAmbiguity(_LfCheck):
    """LF002: IAMAllowedPrincipals grant alongside FGAC/LF-tag evidence."""

    id = "LF002"
    title = "IAMAllowedPrincipals alongside FGAC tags"
    why = (
        "IAMAllowedPrincipals grants bypass Lake Formation for principals not "
        "enrolled in hybrid access - with LF-tags in play the effective gate "
        "is ambiguous."
    )
    when_ok = "IAMAllowedPrincipals grants only where hybrid access is intended, or not at all."
    fix = "Enroll principals in hybrid access mode, or drop the IAMAllowedPrincipals grant."
    evidence_kind = EvidenceKind.DERIVED
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        resources = _iac(ctx)
        texts = _file_texts(ctx, resources)
        lf_files = {
            relative
            for relative, text in texts.items()
            if any(Path(r.file) == relative for r in resources if _is_lf(r))
        }
        principals_in = sorted(
            f.as_posix() for f, text in texts.items() if _PRINCIPALS_RE.search(text)
        )
        fgac_in = sorted(
            f.as_posix()
            for f, text in texts.items()
            if f in lf_files and _FGAC_TOKEN_RE.search(text)
        ) or sorted(f.as_posix() for f, text in texts.items() if _FGAC_TOKEN_RE.search(text))
        if not (principals_in and fgac_in):
            return []
        return [
            self.result(
                Severity.WARNING,
                "IAMAllowedPrincipals grant + FGAC/LF-tag evidence "
                f"(grant: {', '.join(principals_in)}; tags: {', '.join(fgac_in)}) - "
                "hybrid-access ambiguity",
            )
        ]


def _model(ctx: ProjectContext) -> Any:
    from forge_doctor.analyzers.lakeformation_model import lakeformation_model

    return lakeformation_model(ctx)


class NoDataLakeSettings(_LfCheck):
    """LF010: LF grants/locations exist but no data-lake-settings declaration."""

    id = "LF010"
    title = "Lake Formation resources without data-lake settings"
    why = (
        "Grants and registered locations without a declared "
        "aws_lakeformation_data_lake_settings leave admins and default "
        "permissions implicit - the permission graph has no anchor."
    )
    when_ok = "Either no LF resources, or settings/admins are declared alongside them."
    fix = "Declare aws_lakeformation_data_lake_settings with explicit admins."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_lakeformation or model.admins:
            return []
        has_settings = any(
            r.type
            in ("aws_lakeformation_data_lake_settings", "AWS::LakeFormation::DataLakeSettings")
            for r in model.resources
        )
        if has_settings or not (model.grants or model.data_locations):
            return []
        loc = model.grants[0] if model.grants else model.data_locations[0]
        return [
            self.result(
                Severity.INFO,
                f"{len(model.grants)} grants / {len(model.data_locations)} registered locations "
                "but no aws_lakeformation_data_lake_settings (admins undeclared)",
                file=loc.file,
                line=loc.line,
            )
        ]


class IamAllowedPrincipalsDefault(_LfCheck):
    """LF011: IAMAllowedPrincipals retained in catalog default permissions."""

    id = "LF011"
    title = "IAMAllowedPrincipals in default permissions"
    why = (
        "Default catalog permissions granted to IAMAllowedPrincipals mean new "
        "databases/tables stay IAM-governed - LF grants silently do not apply "
        "unless the table is enrolled or the default is removed."
    )
    when_ok = "create_*_default_permissions no longer grant IAMAllowedPrincipals."
    fix = (
        "Remove IAMAllowedPrincipals from default permissions (opt into LF), "
        "or set hybrid access deliberately."
    )
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        hits = [
            g for g in model.grants if g.resource_name == "default" and _is_allowed(g.principal)
        ]
        return [
            self.result(
                Severity.WARNING,
                f"default permissions grant {', '.join(g.permissions) or 'ALL'} to {g.principal}",
                file=g.file,
                line=g.line,
            )
            for g in hits
        ]


class DanglingResourceLink(_LfCheck):
    """LF012: resource link no grant ever references."""

    id = "LF012"
    title = "Dangling resource link"
    why = (
        "A consumer-side resource link is only usable once the consumer "
        "grants its own principals access to the link - the producer-side RAM "
        "share lives in the other repo, so only the consumer grant is "
        "checkable here."
    )
    when_ok = "Every resource link is referenced by at least one grant."
    fix = "Grant local principals on the link database (aws_lakeformation_permissions)."
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        grant_targets = {g.resource_name for g in model.grants}
        grant_targets |= {
            g.resource_name.split(".")[0] for g in model.grants if "." in g.resource_name
        }
        results: list[CheckResult] = []
        for link in model.resource_links:
            if link.name in grant_targets or link.target_database in grant_targets:
                continue
            results.append(
                self.result(
                    Severity.WARNING,
                    f"resource link '{link.name}' -> {link.target_database or '?'} "
                    f"@{link.target_catalog or '?'} is referenced by no grant",
                    file=link.file,
                    line=link.line,
                )
            )
        return results


class ExternalGrantWithoutRam(_LfCheck):
    """LF013: grant to an external account with no RAM principal association."""

    id = "LF013"
    title = "External account grant without RAM share"
    why = (
        "Cross-account Lake Formation grants require the producer to share via "
        "AWS RAM - a grant to an account id without a RAM principal "
        "association leaves the consumer unable to accept or resolve."
    )
    when_ok = "Every account-level grant has a matching aws_ram_principal_association."
    fix = "Add an aws_ram_resource_share + aws_ram_principal_association for the external account."
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        results: list[CheckResult] = []
        for grant in model.grants:
            if not grant.cross_account:
                continue
            acct = _account_of(grant.principal) or grant.principal
            if acct in model.ram_principals:
                continue
            results.append(
                self.result(
                    Severity.WARNING,
                    f"grant to external account {acct} ({', '.join(grant.permissions)}) "
                    "has no aws_ram_principal_association in project",
                    file=grant.file,
                    line=grant.line,
                )
            )
        return results


class UnregisteredDataLocation(_LfCheck):
    """LF014: data-location grant for an S3 path that was never registered."""

    id = "LF014"
    title = "Grant on unregistered data location"
    why = (
        "LF enforces credentials vending only for registered locations - a "
        "data_location grant against an unregistered S3 arn silently does "
        "nothing."
    )
    when_ok = "Every data_location grant targets a registered aws_lakeformation_resource."
    fix = "Register the S3 path with aws_lakeformation_resource first."
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        registered = {d.arn for d in model.data_locations}
        results: list[CheckResult] = []
        for grant in model.grants:
            if grant.resource_kind != "data_location" or not grant.resource_name:
                continue
            if registered and grant.resource_name not in registered:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"data_location grant on {grant.resource_name} is not among "
                        f"the {len(registered)} registered location(s)",
                        file=grant.file,
                        line=grant.line,
                    )
                )
        return results


class LfTbacCoverage(_LfCheck):
    """LF015: LF-TBAC grant target tags are defined in the project."""

    id = "LF015"
    title = "LF-TBAC tag grant coverage"
    why = (
        "Granting on an lf_tag/lf_tag_expression that no "
        "aws_lakeformation_lf_tag defines leaves the policy inert - the tag "
        "keys must exist before tag-based control works."
    )
    when_ok = "lf_tag grants reference tag keys that are defined."
    fix = "Define the referenced LF tags or fix the grant's tag key."
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        defined = {t.key for t in model.lf_tags}
        results: list[CheckResult] = []
        for grant in model.grants:
            if grant.resource_kind not in ("lf_tag", "lf_tag_expression"):
                continue
            if defined and grant.resource_kind == "lf_tag" and grant.resource_name not in defined:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"lf_tag grant on undefined tag key '{grant.resource_name}'",
                        file=grant.file,
                        line=grant.line,
                    )
                )
        if model.fta and not results:
            results.append(
                self.result(
                    Severity.INFO,
                    f"LF-TBAC in use ({len(defined)} tag keys defined, "
                    f"{sum(1 for g in model.grants if g.resource_kind.startswith('lf_tag'))}"
                    " tag grants)",
                )
            )
        return results


class UnusedCellsFilter(_LfCheck):
    """LF016: data-cells filter defined but referenced by no grant."""

    id = "LF016"
    title = "Data cells filter never granted"
    why = (
        "A data_cells_filter only takes effect when granted to a principal - "
        "a defined-but-ungranted filter is dead governance."
    )
    when_ok = "Every filter appears in at least one grant's data_cells_filter resource."
    fix = "Reference the filter in an aws_lakeformation_permissions resource block."
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        used = {g.resource_name for g in model.grants if g.resource_kind == "data_cells_filter"}
        return [
            self.result(
                Severity.INFO,
                f"data cells filter '{f.name}' on {f.database}.{f.table} is referenced by no grant",
                file=f.file,
                line=f.line,
            )
            for f in model.filters
            if f.name and f.name not in used
        ]


class HybridModeOverlap(_LfCheck):
    """LF017: IAM defaults retained while LF tags/filters also in play."""

    id = "LF017"
    title = "Hybrid access overlap"
    why = (
        "IAMAllowedPrincipals defaults plus FGAC/LF-tag grants mean two "
        "permission systems evaluate the same tables - effective access is "
        "the union, which is rarely intended."
    )
    when_ok = "Hybrid mode is deliberate (enrolled principals) or absent."
    fix = (
        "Remove default IAMAllowedPrincipals or audit which tables are intended to be IAM-governed."
    )
    evidence_kind = EvidenceKind.DERIVED
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not (model.hybrid_access and (model.fgac or model.fta)):
            return []
        return [
            self.result(
                Severity.WARNING,
                "hybrid access: IAMAllowedPrincipals defaults retained while "
                f"FGAC={'on' if model.fgac else 'off'} / LF-TBAC={'on' if model.fta else 'off'}",
            )
        ]


class GrantOptionEscalation(_LfCheck):
    """LF018: grant-with-grant-option to an external account."""

    id = "LF018"
    title = "Grant option to external account"
    why = (
        "permissions_with_grant_option lets the receiving account re-grant "
        "further - on a cross-account principal that delegates control "
        "outside the producer's governance boundary."
    )
    when_ok = "External accounts never receive grant options."
    fix = "Drop permissions_with_grant_option for external principals."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        return [
            self.result(
                Severity.WARNING,
                f"grant option delegated to external account "
                f"{_account_of(g.principal) or g.principal} on {g.resource_name}",
                file=g.file,
                line=g.line,
            )
            for g in model.grants
            if g.cross_account and g.grant_option
        ]


def _account_of(principal: str) -> str | None:
    if re.match(r"^\d{12}$", principal):
        return principal
    m = re.match(r"arn:aws[a-z-]*:iam::(\d{12}):", principal)
    return m.group(1) if m else None


def _is_allowed(principal: str) -> bool:
    return bool(_PRINCIPALS_RE.search(principal))


CHECKS: list[Check] = [
    LakeFormationUsage(),
    ResourceLinkWithoutShare(),
    HybridAccessAmbiguity(),
    NoDataLakeSettings(),
    IamAllowedPrincipalsDefault(),
    DanglingResourceLink(),
    ExternalGrantWithoutRam(),
    UnregisteredDataLocation(),
    LfTbacCoverage(),
    UnusedCellsFilter(),
    HybridModeOverlap(),
    GrantOptionEscalation(),
]
