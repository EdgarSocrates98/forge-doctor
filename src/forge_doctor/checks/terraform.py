"""Terraform checks (TF###) over the shared TerraformProjectModel."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from forge_doctor.analyzers.terraform_model import TerraformProjectModel, terraform_model
from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

_LIVE_REF_RE = re.compile(r"ref=(main|master|HEAD|head|trunk|develop)\b", re.IGNORECASE)
_GIT_SRC_RE = re.compile(r"^(git::|git@|https://github\.com|ssh://git)")
_VERSION_LIKE_RE = re.compile(r"^(v?\d|[0-9a-f]{7,40}$)", re.IGNORECASE)
_UNBOUNDED_RE = re.compile(r"(>=|~>)")
_SRC_QUOTED_RE = re.compile(r'\bsource\s*=\s*"([^"]+)"')


def _module_source(b: object) -> str:
    """Module source from the raw block - flat attrs truncate at ``//``."""
    match = _SRC_QUOTED_RE.search(b.body)  # type: ignore[attr-defined]
    if match:
        return match.group(1)
    return str(b.attrs.get("source", ""))  # type: ignore[attr-defined]


def _model(ctx: ProjectContext) -> TerraformProjectModel:
    return terraform_model(ctx)


class _TfCheck(CheckBase):
    category = "terraform"
    evidence_kind = EvidenceKind.CONFIG


class TerraformUsage(_TfCheck):
    """TF000: how much of the project is Terraform-managed."""

    id = "TF000"
    title = "Terraform usage"
    why = "Anchor: sizes the Terraform surface feeding the other TF checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_terraform:
            return [self.result(Severity.PASS, "no Terraform detected")]
        kinds: dict[str, int] = {}
        for b in model.blocks:
            kinds[b.kind] = kinds.get(b.kind, 0) + 1
        detail = ", ".join(f"{k}={n}" for k, n in sorted(kinds.items()))
        return [
            self.result(
                Severity.INFO,
                f"{model.tf_files} .tf files, {len(model.edges)} references ({detail})",
            )
        ]


class MissingRequiredVersion(_TfCheck):
    """TF001: resources exist but the terraform block has no required_version."""

    id = "TF001"
    title = "Missing required_version"
    why = "Without a floor, CI/dev machines can plan on incompatible Terraform "
    "releases and produce different behavior silently."
    when_ok = "required_version set in a terraform block."
    fix = 'Add `terraform { required_version = ">= X.Y" }` to the root module.'

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.resources or model.required_version:
            return []
        first = model.resources[0]
        return [
            self.result(
                Severity.INFO,
                f"{len(model.resources)} resources but no terraform.required_version",
                file=first.file,
                line=first.line,
                evidence=self.evidence_at(ctx, first.file, first.line),
            )
        ]


class ProviderUnconstrained(_TfCheck):
    """TF002: provider block (or requirement) with no version constraint."""

    id = "TF002"
    title = "Provider without version constraint"
    why = "An unpinned provider resolves to whatever the registry serves that "
    "day - plans become non-reproducible and breaking changes arrive unreviewed."
    when_ok = "Every provider has a version constraint in required_providers."
    fix = "Pin each provider in terraform.required_providers."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        required = model.required_providers
        results = []
        for name, req in required.items():
            version = str(req.attrs.get("version", "") or "")
            if not version:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"provider '{name}' declared without a version constraint",
                        file=req.file,
                        line=req.line,
                    )
                )
        for b in model.providers:
            name = b.labels[0] if b.labels else ""
            if name and name not in required:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"provider '{name}' configured but absent from "
                        "required_providers - constraint is implicit",
                        file=b.file,
                        line=b.line,
                        evidence=self.evidence_at(ctx, b.file, b.line),
                    )
                )
        return results


class ProviderConstraintBroad(_TfCheck):
    """TF003: provider constraint unbounded above (>= / ~> without upper cap)."""

    id = "TF003"
    title = "Overly broad provider constraint"
    why = "`>= 5.0` admits the next major version - breaking schema changes "
    "can land via a routine init."
    when_ok = "Constraint has an upper bound (`>= 5.0, < 6.0` or `~> 5.x`)."
    fix = "Cap the constraint below the next major version."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        results = []
        for name, req in model.required_providers.items():
            version = str(req.attrs.get("version", "") or "")
            if not _UNBOUNDED_RE.search(version):
                continue
            # ``~>`` always caps at the last specified component; ``>=``
            # alone admits every future major version.
            has_upper = bool(re.search(r"<\s*\d", version)) or version.strip().startswith("~>")
            if not has_upper:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"provider '{name}' constraint '{version}' has no upper bound",
                        file=req.file,
                        line=req.line,
                        evidence=self.evidence_at(ctx, req.file, req.line),
                    )
                )
        return results


class LocalModule(_TfCheck):
    """TF020: module source is a local path."""

    id = "TF020"
    title = "Local module detected"
    why = "Local modules are fine, but each one is worth noting for ownership "
    "and versioning review - they cannot be upgraded independently."
    when_ok = "Intentional in-repo modules."
    fix = "Nothing required; consider registry pinning for shared modules."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"module '{b.labels[0] if b.labels else b.address}' is a local "
                f"path ({_module_source(b)})",
                file=b.file,
                line=b.line,
            )
            for b in _model(ctx).modules
            if _module_source(b).startswith(("./", "../"))
        ]


class RegistryModuleUnpinned(_TfCheck):
    """TF021: registry module source without a version."""

    id = "TF021"
    title = "Registry module unpinned"
    why = "A registry source with no `version` resolves to latest at init "
    "time - two applies can build different infrastructure."
    when_ok = "Every registry module sets `version`."
    fix = 'Add `version = "x.y.z"` to the module block.'

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for b in _model(ctx).modules:
            source = _module_source(b)
            if not source or source.startswith(("./", "../")) or _GIT_SRC_RE.match(source):
                continue
            if not str(b.attrs.get("version", "")):
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"module '{b.labels[0] if b.labels else b.address}' "
                        f"source '{source}' has no version",
                        file=b.file,
                        line=b.line,
                        evidence=self.evidence_at(ctx, b.file, b.line),
                    )
                )
        return results


class GitModuleMutableRef(_TfCheck):
    """TF022: git-sourced module without an immutable ref."""

    id = "TF022"
    title = "Git module not pinned to immutable ref"
    why = "`ref=main` (or no ref) means the module changes under you; a "
    "production deploy can pick up untested module code."
    when_ok = "git source pinned to a commit SHA or version tag (?ref=vX.Y.Z)."
    fix = "Pin `?ref=` to a commit SHA or immutable tag."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for b in _model(ctx).modules:
            source = _module_source(b)
            if not (_GIT_SRC_RE.match(source) or "git::" in source):
                continue
            ref = re.search(r"[?&]ref=([^&\"]+)", source)
            mutable = ref is None or (
                _LIVE_REF_RE.search(source) is not None or not _VERSION_LIKE_RE.match(ref.group(1))
            )
            if mutable:
                detail = f"ref={ref.group(1)}" if ref else "no ?ref= at all"
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"module '{b.labels[0] if b.labels else b.address}' "
                        f"git source is mutable ({detail})",
                        file=b.file,
                        line=b.line,
                        evidence=self.evidence_at(ctx, b.file, b.line),
                    )
                )
        return results


class LocalBackend(_TfCheck):
    """TF130: `backend "local"` - state lives on someone's disk."""

    id = "TF130"
    title = "Local backend"
    why = "Local state means no locking, no sharing, and one laptop away "
    "from losing the deployment record."
    when_ok = "Personal throwaway environments only."
    fix = "Move to a remote backend (s3+dynamodb, azurerm, gcs, remote)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if model.backend != "local":
            return []
        b = next(b for b in model.blocks if b.kind == "terraform")
        return [
            self.result(
                Severity.WARNING,
                'terraform backend is "local" - state has no locking or sharing',
                file=b.file,
                line=b.line,
                evidence=self.evidence_at(ctx, b.file, b.line),
            )
        ]


CHECKS: list[Check] = [
    TerraformUsage(),
    MissingRequiredVersion(),
    ProviderUnconstrained(),
    ProviderConstraintBroad(),
    LocalModule(),
    RegistryModuleUnpinned(),
    GitModuleMutableRef(),
    LocalBackend(),
]
