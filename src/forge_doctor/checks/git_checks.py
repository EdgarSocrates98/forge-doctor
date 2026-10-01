"""Git checks (GIT*): repository presence and tracked-file hygiene."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

CATEGORY = "git"

_MAX_EXAMPLES = 3


def _is_sensitive(basename: str) -> bool:
    """True for credential-like file names (already lowercased)."""
    return (
        basename == ".env"
        or basename.startswith(".env.")
        or basename == "credentials"
        or basename.startswith("secrets.")
    )


def _is_artifact(path: str) -> bool:
    """True for Python build artifacts (already lowercased posix path)."""
    return "__pycache__/" in path or path.endswith((".pyc", ".pyo")) or ".egg-info/" in path


class GitRepository(CheckBase):
    """GIT001 - the project is a git repository."""

    id = "GIT001"
    title = "Git repository"
    category = CATEGORY
    evidence_kind = EvidenceKind.OBSERVED_METADATA
    why = "History, collaboration and CI all assume version control."
    when_ok = "Tarballs and vendored trees."
    fix = "Run `git init`."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        if ctx.git.is_repo:
            return [self.result(Severity.PASS, "Project is a git repository.")]
        return [
            self.result(
                Severity.INFO,
                "Project is not a git repository.",
                recommendation="Run 'git init' to track changes and enable collaboration.",
            )
        ]


class SensitiveFilesTracked(CheckBase):
    """GIT002 - credential-like files must not be committed."""

    id = "GIT002"
    title = "Sensitive files tracked"
    category = CATEGORY
    evidence_kind = EvidenceKind.OBSERVED_METADATA
    why = "Committed credentials leak with the repository forever."
    when_ok = "Never."
    fix = "git rm --cached, rotate the credential, add the pattern to .gitignore."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        tracked = ctx.git.tracked_files
        if not ctx.git.is_repo or tracked is None:
            return []
        findings = [
            self.result(
                Severity.ERROR,
                f"Sensitive file tracked by git: {name}",
                file=Path(name),
                recommendation=(
                    "Remove it from the index (git rm --cached) and rotate the exposed credentials."
                ),
            )
            for name in sorted(tracked)
            if _is_sensitive(PurePosixPath(name).name.lower())
        ]
        if findings:
            return findings
        return [self.result(Severity.PASS, "No sensitive files tracked by git.")]


class PythonArtifactsTracked(CheckBase):
    """GIT003 - Python build artifacts must not be committed."""

    id = "GIT003"
    title = "Python artifacts tracked"
    category = CATEGORY
    evidence_kind = EvidenceKind.OBSERVED_METADATA
    why = "Artifacts bloat the repo and produce spurious diffs across machines."
    when_ok = "Never - they are always generated."
    fix = "git rm --cached the files and add patterns to .gitignore."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        tracked = ctx.git.tracked_files
        if not ctx.git.is_repo or tracked is None:
            return []
        artifacts = sorted(name for name in tracked if _is_artifact(name.lower()))
        if not artifacts:
            return [self.result(Severity.PASS, "No Python artifacts tracked by git.")]
        examples = ", ".join(artifacts[:_MAX_EXAMPLES])
        if len(artifacts) > _MAX_EXAMPLES:
            examples += ", ..."
        return [
            self.result(
                Severity.WARNING,
                f"{len(artifacts)} Python artifact(s) tracked by git: {examples}.",
                recommendation=(
                    "Remove them with 'git rm --cached' and add the patterns to .gitignore."
                ),
            )
        ]


CHECKS: list[Check] = [
    GitRepository(),
    SensitiveFilesTracked(),
    PythonArtifactsTracked(),
]
