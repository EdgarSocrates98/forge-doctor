"""IaC checks (IAC###): Terraform and CloudFormation data-infra hygiene.

Pure static parsing via ``analyzers.hcl_lite`` - no terraform/cfn execution,
no cloud calls. Version statuses come from the knowledge packs, same as the
code-level Glue checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from forge_doctor.analyzers.hcl_lite import IaCResource, project_iac
from forge_doctor.core.knowledge import glue_status
from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

CATEGORY = "iac"
_CACHE_ATTR = "_fd_iac_resources"

_EOL_LAMBDA_RUNTIMES = {
    "python2.7",
    "python3.6",
    "python3.7",
    "python3.8",
    "nodejs12.x",
    "nodejs14.x",
    "nodejs16.x",
    "dotnet6",
    "dotnetcore3.1",
    "ruby2.7",
    "java8",
    "java8.al2",
    "go1.x",
}
_EOL_EMR_PREFIXES = ("emr-4.", "emr-5.")


def _resources(ctx: ProjectContext) -> list[IaCResource]:
    cached = getattr(ctx, _CACHE_ATTR, None)
    if isinstance(cached, list):
        return cached
    resources = project_iac(ctx.files, ctx.root)
    setattr(ctx, _CACHE_ATTR, resources)
    return resources


class _IaCCheck(CheckBase):
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG


class GlueJobVersionIaC(_IaCCheck):
    """IAC001: terraform aws_glue_job glue_version vs the knowledge pack."""

    id = "IAC001"
    title = "Terraform Glue version"
    why = "glue_version pinned in IaC ages silently; EOL runtimes stop launching."
    when_ok = "glue_version is a supported/current Glue release."
    fix = "Update glue_version to a supported release and plan the migration."
    tags = ("glue", "terraform")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for resource in _resources(ctx):
            if resource.source != "terraform" or resource.type != "aws_glue_job":
                continue
            version = str(resource.attrs.get("glue_version", "")).strip()
            if not version:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"{resource.name}: glue_version unset (defaults apply).",
                        file=_file(resource),
                        line=resource.line,
                        confidence=Confidence.HIGH,
                    )
                )
                continue
            status = glue_status(version)
            if status in {"eol", "aging"}:
                results.append(
                    self.result(
                        Severity.ERROR if status == "eol" else Severity.WARNING,
                        f"{resource.name}: glue_version {version} is {status}.",
                        "Upgrade to a supported Glue version.",
                        file=_file(resource),
                        line=resource.line,
                        confidence=Confidence.HIGH,
                    )
                )
            else:
                results.append(
                    self.result(
                        Severity.PASS,
                        f"{resource.name}: glue_version {version} ({status}).",
                        file=_file(resource),
                        line=resource.line,
                    )
                )
        return results


class GlueJobCapacity(_IaCCheck):
    """IAC002: worker_type/number_of_workers sanity on aws_glue_job."""

    id = "IAC002"
    title = "Glue capacity sanity"
    why = "Bad worker types or missing worker counts make jobs fail or crawl."
    when_ok = "worker_type is a known G.x/Z.x type and worker count is set."
    fix = "Set a supported worker_type and an explicit number_of_workers."
    tags = ("glue", "terraform", "capacity")
    confidence = Confidence.MEDIUM

    _VALID_WORKERS: ClassVar[frozenset[str]] = frozenset(
        {"g.025x", "g.1x", "g.2x", "g.4x", "g.8x", "g.12x", "g.16x", "z.2x"}
    )

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for resource in _resources(ctx):
            if resource.source != "terraform" or resource.type != "aws_glue_job":
                continue
            worker_type = str(resource.attrs.get("worker_type", "")).lower()
            workers = resource.attrs.get("number_of_workers")
            if worker_type and worker_type not in self._VALID_WORKERS:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"{resource.name}: worker_type '{worker_type}' is not a "
                        "known Glue worker type.",
                        "Use G.025X/G.1X/G.2X/G.4X/G.8X/G.12X/G.16X/Z.2X.",
                        file=_file(resource),
                        line=resource.line,
                    )
                )
            if workers is None:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"{resource.name}: number_of_workers unset.",
                        file=_file(resource),
                        line=resource.line,
                    )
                )
            elif isinstance(workers, int) and workers > 299:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"{resource.name}: number_of_workers={workers} is large; "
                        "confirm the DPU quota supports it.",
                        file=_file(resource),
                        line=resource.line,
                        confidence=Confidence.LOW,
                    )
                )
        return results


class CfnGlueVersion(_IaCCheck):
    """IAC003: CloudFormation AWS::Glue::Job GlueVersion lifecycle status."""

    id = "IAC003"
    title = "CloudFormation GlueVersion"
    why = "GlueVersion in CFN templates decays the same way as terraform pins."
    when_ok = "GlueVersion is a supported/current release."
    fix = "Update the template's GlueVersion and re-deploy."
    tags = ("glue", "cloudformation")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for resource in _resources(ctx):
            if resource.source != "cloudformation" or resource.type != "AWS::Glue::Job":
                continue
            version = str(resource.attrs.get("GlueVersion", "")).strip().strip("\"'")
            if not version:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"{resource.name}: GlueVersion unset.",
                        file=_file(resource),
                        line=resource.line,
                    )
                )
                continue
            status = glue_status(version)
            if status in {"eol", "aging"}:
                results.append(
                    self.result(
                        Severity.ERROR if status == "eol" else Severity.WARNING,
                        f"{resource.name}: GlueVersion {version} is {status}.",
                        "Upgrade to a supported Glue version.",
                        file=_file(resource),
                        line=resource.line,
                        confidence=Confidence.HIGH,
                    )
                )
            else:
                results.append(
                    self.result(
                        Severity.PASS,
                        f"{resource.name}: GlueVersion {version} ({status}).",
                        file=_file(resource),
                        line=resource.line,
                    )
                )
        return results


class CfnRuntime(_IaCCheck):
    """IAC004: CloudFormation Lambda/EMR runtimes past end-of-life."""

    id = "IAC004"
    title = "CloudFormation runtime lifecycle"
    why = "EOL Lambda runtimes and EMR releases stop accepting deployments."
    when_ok = "Runtimes and EMR release labels are supported."
    fix = "Move to a supported runtime/release before the next deploy."
    tags = ("cloudformation", "lambda", "emr", "lifecycle")

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for resource in _resources(ctx):
            if resource.source != "cloudformation":
                continue
            if resource.type == "AWS::Lambda::Function":
                runtime = str(resource.attrs.get("Runtime", "")).strip("\"'")
                if runtime.lower() in _EOL_LAMBDA_RUNTIMES:
                    results.append(
                        self.result(
                            Severity.ERROR,
                            f"{resource.name}: Lambda runtime {runtime} is EOL.",
                            "Upgrade to a supported Lambda runtime.",
                            file=_file(resource),
                            line=resource.line,
                            confidence=Confidence.HIGH,
                        )
                    )
            elif resource.type == "AWS::EMR::Cluster":
                release = str(resource.attrs.get("ReleaseLabel", "")).strip("\"'")
                if release.startswith(_EOL_EMR_PREFIXES):
                    results.append(
                        self.result(
                            Severity.WARNING,
                            f"{resource.name}: EMR release {release} is aging.",
                            "Plan migration to a current EMR release.",
                            file=_file(resource),
                            line=resource.line,
                        )
                    )
        return results


def _file(resource: IaCResource) -> Path | None:
    return Path(resource.file) if resource.file else None


CHECKS: list[Check] = [
    GlueJobVersionIaC(),
    GlueJobCapacity(),
    CfnGlueVersion(),
    CfnRuntime(),
]
