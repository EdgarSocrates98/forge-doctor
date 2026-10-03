"""Repository hygiene checks (REP*): manifests, docs, tests, CI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from forge_doctor_data.core.models import EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult

CATEGORY = "repository"

_README_NAMES = frozenset({"readme.md", "readme.rst", "readme.txt"})
_LICENSE_PREFIXES = ("license", "licence", "copying")
_LOCKFILES = ("Pipfile.lock", "uv.lock", "pdm.lock")


def _root_file_names(ctx: ProjectContext) -> set[str]:
    """Lowercased names of files sitting at the project root."""
    return {f.name.lower() for f in ctx.files if f.parent == Path(".")}


class PyprojectExists(CheckBase):
    """REP001 - the project declares a ``pyproject.toml``."""

    id = "REP001"
    title = "pyproject.toml"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "No pyproject.toml means no modern build or dependency metadata."
    when_ok = "Non-Python repos, or repos that are pure scripts by design."
    fix = "Add a pyproject.toml with a [project] table."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        if ctx.has_file("pyproject.toml"):
            return [
                self.result(Severity.PASS, "pyproject.toml found.", file=Path("pyproject.toml"))
            ]
        return [
            self.result(
                Severity.WARNING,
                "pyproject.toml not found.",
                recommendation="Add a PEP 621 pyproject.toml at the project root.",
            )
        ]


class ReadmeExists(CheckBase):
    """REP002 - the project ships a README."""

    id = "REP002"
    title = "README"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "A README is the first thing a human or a tool reads."
    when_ok = "Internal throwaway utilities."
    fix = "Even a five-line README pays for itself."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        if _root_file_names(ctx) & _README_NAMES:
            return [self.result(Severity.PASS, "README found at the project root.")]
        return [
            self.result(
                Severity.WARNING,
                "No README.md, README.rst or README.txt at the project root.",
                recommendation="Add a README describing the project and how to run it.",
            )
        ]


class LicenseExists(CheckBase):
    """REP003 - the project ships a license file."""

    id = "REP003"
    title = "License"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Open source without a license is not legally usable."
    when_ok = "Private/internal projects."
    fix = "Add a LICENSE file (e.g. MIT, Apache-2.0)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        names = _root_file_names(ctx)
        if any(name.startswith(_LICENSE_PREFIXES) for name in names):
            return [self.result(Severity.PASS, "License file found at the project root.")]
        return [
            self.result(
                Severity.INFO,
                "No LICENSE, LICENCE or COPYING file found.",
                recommendation="Add a license file so others know the terms of use.",
            )
        ]


class GitignoreExists(CheckBase):
    """REP004 - the project ignores build artifacts and secrets."""

    id = "REP004"
    title = ".gitignore"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Keeps build artifacts and secrets out of version control."
    when_ok = "Non-git directories."
    fix = "Add one before the first __pycache__ slips in."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        if ctx.has_file(".gitignore"):
            return [self.result(Severity.PASS, ".gitignore found.", file=Path(".gitignore"))]
        return [
            self.result(
                Severity.INFO,
                "No .gitignore found.",
                recommendation="Add a .gitignore covering __pycache__, .venv and .env.",
            )
        ]


class TestsExist(CheckBase):
    """REP005 - the project contains a test suite."""

    id = "REP005"
    title = "Tests"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Untested data pipelines fail silently in production."
    when_ok = "Prototypes you intend to delete."
    fix = "Add tests/ with pytest modules."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        if ctx.has_dir("tests") or ctx.has_dir("test") or any(_is_test_file(f) for f in ctx.files):
            return [self.result(Severity.PASS, "Test files or test directory found.")]
        return [
            self.result(
                Severity.INFO,
                "No tests/ directory or test_*.py files found.",
                recommendation="Add a tests/ directory with pytest test modules.",
            )
        ]


class SrcLayout(CheckBase):
    """REP006 - the project uses the ``src/`` layout."""

    id = "REP006"
    title = "src layout"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "src layout prevents accidental imports of the working tree in tests."
    when_ok = "Small projects; a preference, not a defect."
    fix = "Move package code under src/<package>/."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        src_files = [f for f in ctx.files if f.suffix == ".py" and f.parts and f.parts[0] == "src"]
        packages = {f.parent for f in src_files if f.name == "__init__.py"}
        if src_files:
            if packages:
                package = sorted(p.as_posix() for p in packages)[0]
                message = f"src layout in use (package at {package})."
            else:
                message = "src layout in use."
            return [self.result(Severity.PASS, message)]
        return [
            self.result(
                Severity.INFO,
                "No Python sources under src/.",
                recommendation=(
                    "Consider a src layout (src/<package>/) to avoid accidental "
                    "imports of the working tree."
                ),
            )
        ]


class ConflictingManifests(CheckBase):
    """REP007 - dependency manifests/locks that contradict each other."""

    id = "REP007"
    title = "Conflicting manifests"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Two dependency sources of truth will drift apart."
    when_ok = "Never for real conflicts; migrate deliberately."
    fix = "Pick one dependency manager and remove the other manifest."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        conflicts: list[str] = []
        if ctx.has_file("requirements.txt") and ctx.has_file("poetry.lock"):
            conflicts.append("requirements.txt + poetry.lock")
        if ctx.has_file("poetry.lock"):
            for other in _LOCKFILES:
                if ctx.has_file(other):
                    conflicts.append(f"poetry.lock + {other}")
        if conflicts:
            return [
                self.result(
                    Severity.WARNING,
                    f"Conflicting dependency manifests: {', '.join(conflicts)}.",
                    recommendation=(
                        "Keep a single dependency manager; remove the unused manifest/lock."
                    ),
                )
            ]
        return [self.result(Severity.PASS, "No conflicting dependency manifests.")]


class CiConfigured(CheckBase):
    """REP008 - a continuous-integration configuration exists."""

    id = "REP008"
    title = "CI configuration"
    category = CATEGORY
    evidence_kind = EvidenceKind.CONFIG
    why = "Without CI, regressions are found by humans in production."
    when_ok = "Early prototypes."
    fix = "Add .github/workflows (or equivalent) running tests and lint."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        found = (
            ctx.has_dir(".github/workflows")
            or ctx.has_file(".gitlab-ci.yml")
            or ctx.has_file("azure-pipelines.yml")
            or ctx.has_file(".circleci/config.yml")
        )
        if found:
            return [self.result(Severity.PASS, "CI configuration found.")]
        return [
            self.result(
                Severity.INFO,
                "No CI configuration found.",
                recommendation=(
                    "Add a CI pipeline (e.g. .github/workflows) to run tests and lint."
                ),
            )
        ]


def _is_test_file(path: Path) -> bool:
    name = path.name.lower()
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py")


CHECKS: list[Check] = [
    PyprojectExists(),
    ReadmeExists(),
    LicenseExists(),
    GitignoreExists(),
    TestsExist(),
    SrcLayout(),
    ConflictingManifests(),
    CiConfigured(),
]
