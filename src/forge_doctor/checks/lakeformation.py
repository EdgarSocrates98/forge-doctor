"""Lake Formation checks (LF###) over IaC facts - evidence-gated, no AWS calls."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

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


CHECKS: list[Check] = [
    LakeFormationUsage(),
    ResourceLinkWithoutShare(),
    HybridAccessAmbiguity(),
]
